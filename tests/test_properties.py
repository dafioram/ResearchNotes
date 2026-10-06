"""Property-based tests (Hypothesis): the security-sensitive pure functions
checked against thousands of generated inputs, not just hand-picked ones."""

import html.parser
import re

import pytest

pytest.importorskip("hypothesis", reason="pip install -r requirements-dev.txt")

from hypothesis import HealthCheck, assume, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from app import db as dbmod  # noqa: E402
from app import markdown as md  # noqa: E402
from app import search, security  # noqa: E402

# Text heavy in the characters the renderer and parser care about.
MARKUP = st.lists(
    st.sampled_from(list("ab #*_~`[]()<>&\"'\n:/.-!|=\\")
                    + ["javascript:", "http://x.org", "[[", "]]", "[[later", "```", "<script>",
                       "onerror=", "é", "日", "\r\n", "\t", "#lab", "data:", "&lt;", "\x00", "1",
                       "$", "$$", "\\$", "$x$", "|", "|---|", "\\|", "| a |\n|-|\n"]),
    max_size=60,
).map("".join)
SAFE_TAGS = {"p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "del", "code", "pre",
             "ul", "ol", "li", "blockquote", "hr", "a", "span", "table", "thead", "tbody", "tr", "th", "td"}


class _Tags(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.labels, self.hrefs = [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if attrs.get("class") == "label-tag":
            self.labels.append(attrs["href"])
        if "href" in attrs:
            self.hrefs.append(attrs["href"])


@settings(max_examples=600, deadline=None)
@given(MARKUP)
def test_rendering_never_emits_unsafe_html(text):
    out = md.render(text, {1: "One <b>", 2: ""})
    parser = _Tags()
    parser.feed(out)
    for tag, attrs in parser.tags:
        assert tag in SAFE_TAGS, (text, tag)
        assert set(attrs) <= {"href", "class", "title", "rel", "data-id"}, (text, attrs)
    for href in parser.hrefs:
        assert md.safe_href(href) is not None, (text, href)   # never javascript:, data:, ...
    assert "<script" not in out.lower()


@settings(max_examples=600, deadline=None)
@given(MARKUP)
def test_what_shows_as_a_label_is_what_is_stored(text):
    parser = _Tags()
    parser.feed(md.render(text))
    shown = {re.sub(r"^/\?q=%23", "", href) for href in parser.labels}
    from urllib.parse import unquote
    assert {unquote(s) for s in shown} == md.extract_labels(text), text


@settings(max_examples=400, deadline=None)
@given(MARKUP)
def test_links_shown_are_the_links_stored(text):
    refs = md.extract_note_refs(text)
    out = md.render(text, set(refs))
    shown = {int(m) for m in re.findall(r'href="/notes/(\d+)"', out)}
    assert shown == refs, text


# ---------------------------------------------------------------------
# Search: whatever is typed parses, and runs
# ---------------------------------------------------------------------

QUERY = st.lists(
    st.sampled_from(list('ab1 "-*#+:()') + ["OR", "NEAR(", "is:unlinked", "has:file", "has:later",
                                           "after:2025", "before:2025-02-30", "#x-*", "é", "\x00", "AND"]),
    max_size=25,
).map("".join)


@pytest.fixture
def seeded(app):
    with app.app_context():
        dbmod.create_note("# a b #x #x-y [[later]]", "2025-01-01")
        dbmod.create_note("ab 1 #b", "2026-01-01")
    return app


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(QUERY)
def test_any_search_parses_and_runs(seeded, raw):
    q = search.parse(raw)
    with seeded.app_context():
        rows, total = dbmod.search_page(q, 5, 0)
        assert len(rows) <= total
        if q.in_date_order:
            dbmod.date_position(q, "2025-06-01")
        dbmod.search_snippets(q, [r["id"] for r in rows])
        dbmod.lookup_notes(raw)


@settings(max_examples=400)
@given(QUERY, st.sampled_from(["#x", "#x-*", "is:unlinked", "has:file"]))
def test_toggling_a_term_twice_gives_the_search_back(raw, token):
    assume(not search.has_token(raw, token))
    assert search.toggle(search.toggle(raw, token), token) == " ".join(raw.split())


@given(st.text(max_size=12))
def test_periods_are_well_formed(text):
    bounds = search.period(text)
    if bounds:
        start, end = bounds
        assert start < end and len(start) == len(end) == 10


# ---------------------------------------------------------------------
# Network safety (spec §14)
# ---------------------------------------------------------------------

@given(st.ip_addresses(), st.one_of(st.none(), st.integers(1, 65535)))
def test_ip_addresses_are_always_allowed(ip, port):
    host = f"[{ip}]" if ip.version == 6 else str(ip)
    assert security.host_allowed(host + (f":{port}" if port else ""))


@given(st.from_regex(r"[a-z]{1,10}\.(com|org|net|io|dev)", fullmatch=True))
def test_public_names_are_refused_unless_listed(name):
    assert not security.host_allowed(name)
    assert security.host_allowed(name, security.parse_allowed_hosts(name))
