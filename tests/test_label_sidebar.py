"""The feed's label sidebar (spec §6.4): namespaces, #prefix-* searches,
the Recent / Most used / A–Z orders, the cap, and the order cookie."""

import re

import pytest

from app import db as dbmod
from app import labels as label_list
from app import search
from tests.test_activity import Clock


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(dbmod, "now_iso", c)
    return c


def _note(body, date="2026-01-01"):
    return dbmod.create_note(body, date)


# ---------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------

def test_namespaces_need_two_labels_sharing_a_prefix():
    groups = label_list.namespaces(
        ["physics", "physics-quantum", "physics-optics", "long-term", "self-care", "self-control", "math"])
    assert groups == {
        "physics": ["physics", "physics-optics", "physics-quantum"],  # the bare label first
        "self": ["self-care", "self-control"],
    }
    assert label_list.namespaces(["math", "math-proofs"]) == {"math": ["math", "math-proofs"]}
    assert label_list.namespaces(["long-term"]) == {}


@pytest.mark.parametrize("raw,labels,required,excluded", [
    ("#physics-*", ["physics-*"], [], []),
    ("#Physics-* #math", ["physics-*", "math"], [], []),
    ("+#physics-* -#bio-*", [], ["physics-*"], ["bio-*"]),
    ("#node.js-*", ["node.js-*"], [], []),
])
def test_namespace_terms_parse(raw, labels, required, excluded):
    q = search.parse(raw)
    assert (q.labels, q.required_labels, q.exclude_labels) == (labels, required, excluded)


@pytest.fixture
def physics(app):
    with app.app_context():
        ids = {
            "bare": _note("#physics"),
            "quantum": _note("#physics-quantum"),
            "both": _note("#physics-quantum #physics-optics"),
            "physical": _note("#physical #physicsy"),   # not in the namespace
            "math": _note("#math"),
            "draft": _note("#physics-optics #draft"),
        }
    return ids


@pytest.mark.parametrize("raw,expected", [
    ("#physics-*", ["bare", "quantum", "both", "draft"]),
    ("#physics-* #math", ["bare", "quantum", "both", "math", "draft"]),
    ("#physics-* -#draft", ["bare", "quantum", "both"]),
    ("+#physics-* +#draft", ["draft"]),
    ("#math -#physics-*", ["math"]),
])
def test_namespace_searches(app, physics, raw, expected):
    with app.app_context():
        found = {r["id"] for r in dbmod.search_notes(raw)}
        assert found == {physics[k] for k in expected}
        # a note with two of its labels counts once
        if raw == "#physics-*":
            assert dbmod.labels_note_count(["physics-*"]) == 4


def test_namespace_pages_read_either_way_match(app, physics):
    with app.app_context():
        everything = [r["id"] for r in dbmod.search_page(search.parse("#physics-* is:unlinked"), -1, 0)[0]]
        rows, total = dbmod.list_notes_page(["physics-*"], 2, 0)
        assert total == 4 and [r["id"] for r in rows] == everything[:2]


# ---------------------------------------------------------------------
# The sidebar
# ---------------------------------------------------------------------

def _sidebar(app, raw="", order="used"):
    with app.app_context():
        return label_list.sidebar(search.parse(raw), order, lambda token: token)


def _names(side):
    return [("#" + e["name"] + ("-*" if e["kind"] == "group" else "")) for e in side["entries"]]


def test_orders(app, clock):
    with app.app_context():
        for _ in range(3):
            _note("#zebra")
        _note("#apple #physics-optics")
        _note("#physics-quantum")
        clock.advance(60)
        _note("#mango")                       # the most recently used
    assert _names(_sidebar(app, order="used")) == ["#zebra", "#physics-*", "#apple", "#mango"]
    assert _names(_sidebar(app, order="az")) == ["#apple", "#mango", "#physics-*", "#zebra"]
    # Recent: by last save; ties (same save time) fall back to most used
    assert _names(_sidebar(app, order="recent")) == ["#mango", "#zebra", "#physics-*", "#apple"]


def test_recent_counts_only_the_latest_saves(app, clock):
    with app.app_context():
        _note("#old")
        for i in range(5):
            clock.advance(1)
            _note(f"#new{i}")
        recency = dbmod.label_recency(saves=3)
    assert set(recency) == {"new2", "new3", "new4"}


def test_labels_in_the_search_come_first_and_open_their_group(app, physics):
    side = _sidebar(app, raw="#draft #physics-optics")
    assert _names(side)[:2] in (["#physics-*", "#draft"], ["#draft", "#physics-*"])
    group = next(e for e in side["entries"] if e["kind"] == "group")
    assert group["open"] and not group["active"]          # opened for its child
    assert [c["short"] for c in group["children"]][0] == "#physics"
    assert next(c for c in group["children"] if c["name"] == "physics-optics")["active"]
    assert _sidebar(app, raw="#physics-*")["entries"][0]["active"]


def test_cap_except_for_a_to_z(app):
    with app.app_context():
        for i in range(40):
            _note(f"#label{i:02d}")
    side = _sidebar(app, order="used")
    assert side["capped"] and side["hidden"] == 4
    assert [e["over_cap"] for e in side["entries"]].count(True) == 4
    az = _sidebar(app, order="az")
    assert not az["capped"] and not any(e["over_cap"] for e in az["entries"])


# ---------------------------------------------------------------------
# On the page
# ---------------------------------------------------------------------

def test_page_shows_chips_groups_and_the_order_switch(client, app, physics):
    page = client.get("/").data.decode()
    assert 'class="label-chips"' in page
    assert re.search(r'class="chip group"[^>]*>#physics-\*<span class="n">4</span>', page)
    assert 'href="/?q=%23physics-*"' in page
    assert "<strong aria-current=\"true\">Recent</strong>" in page   # the default
    assert 'href="/labels/order/az?next=/"' in page


def test_order_cookie(client, app, physics):
    r = client.get("/labels/order/az", query_string={"next": "/?q=%23math"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/?q=%23math")
    assert "label_order=az" in r.headers["Set-Cookie"] and "HttpOnly" in r.headers["Set-Cookie"]
    assert "<strong aria-current=\"true\">A–Z</strong>" in client.get("/").data.decode()


@pytest.mark.parametrize("next_url", ["https://evil.example/", "//evil.example/", "/\\evil.example", ""])
def test_order_switch_only_goes_back_into_the_app(client, next_url):
    r = client.get("/labels/order/used", query_string={"next": next_url})
    assert r.headers["Location"] in ("/", "http://localhost/")


def test_unknown_order_sets_nothing(client):
    r = client.get("/labels/order/sideways?next=/")
    assert "Set-Cookie" not in r.headers
