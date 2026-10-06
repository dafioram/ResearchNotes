"""Math (spec §5): $inline$ and $$display$$ TeX, left as typed for KaTeX
in the browser, and never read as labels, links, emphasis or prices."""

import pytest

from app import db as dbmod
from app import markdown as md


def _inline(tex):
    return f'<span class="math math-inline">{tex}</span>'


@pytest.mark.parametrize("text,html", [
    ("$x$", f"<p>{_inline('$x$')}</p>"),
    ("so $E=mc^2$ holds", f"<p>so {_inline('$E=mc^2$')} holds</p>"),
    ("$x<y$ & more", f"<p>{_inline('$x&lt;y$')} &amp; more</p>"),       # escaped once
    ("$a_b_c$ and $a*b*c$", f"<p>{_inline('$a_b_c$')} and {_inline('$a*b*c$')}</p>"),
])
def test_inline_math_is_kept_as_typed(text, html):
    assert md.render(text) == html


@pytest.mark.parametrize("text", [
    "costs $5 and $10",          # closing dollar followed by a digit
    "between $5 and $6.",        # space just inside the closing dollar
    "$5-$10",
    "a $ b $ c",                 # space just inside the opening dollar
    "US$5 and $x$5",
    "$$ $$",                     # nothing but space
    "just one $ sign",
    "https://x.org/a$b$c",       # not straight after a letter or digit
])
def test_prices_and_stray_dollars_stay_text(text):
    assert "math" not in md.render(text)


def test_an_escaped_dollar_is_a_dollar():
    assert md.render(r"\$5 or $x$") == f"<p>$5 or {_inline('$x$')}</p>"
    assert "math" not in md.render(r"\$x$")


def test_display_math_can_span_lines_and_blank_lines():
    body = "Before\n\n$$\n\\sum_i x_i\n\n= 1\n$$\n\nAfter"
    html = md.render(body)
    assert '<span class="math math-display">$$\n\\sum_i x_i\n\n= 1\n$$</span>' in html
    assert html.startswith("<p>Before</p>") and html.endswith("<p>After</p>")


def test_nothing_inside_math_is_a_label_link_or_emphasis():
    text = "$#lab [[3]] [[later]] *a* https://x.org$ #real [[4]]"
    html = md.render(text, {3: "Three", 4: "Four"})
    assert _inline("$#lab [[3]] [[later]] *a* https://x.org$") in html
    assert html.count("label-tag") == 1 and html.count("note-ref") == 1 and "<em>" not in html
    assert md.extract_labels(text) == {"real"}
    assert md.extract_note_refs(text) == {4}
    assert not md.has_later("$[[later]]$")


def test_code_wins_over_math():
    assert md.render("`$x$`") == "<p><code>$x$</code></p>"
    assert md.render("```\n$$x$$\n```") == "<pre><code>$$x$$\n</code></pre>"


def test_plain_text_keeps_the_tex():
    assert md.first_line_text("# Curve $R = e^{-t/S}$ #memory") == "Curve $R = e^{-t/S}$ #memory"
    [c] = md.ref_contexts("# T\n\nwe have $x^2$ and [[5]] then $$y$$", 5)
    assert (c["before"], c["after"]) == ("we have $x^2$ and ", " then $$y$$")


def test_math_shows_on_the_note_page_and_its_text_is_searchable(client, app):
    with app.app_context():
        nid = dbmod.create_note("# Curve\n\nwe fit $\\lambda$ to the decay", "2026-01-01")
        assert [r["id"] for r in dbmod.search_notes("lambda")] == [nid]
    page = client.get(f"/notes/{nid}").data.decode()
    assert _inline("$\\lambda$") in page
    assert "vendor/katex-0.19.0/katex.min.js" in page and "math.js" in page
