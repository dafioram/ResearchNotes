"""Linking (spec §6.2): [[refs]] shown by title, [[later]] placeholders,
has:later, and the editor's [[ lookup."""

import pytest

from app import db as dbmod
from app import markdown as md


# ---------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------

def test_a_ref_shows_its_notes_title_escaped():
    html = md.render("see [[3]]", {3: "Spacing <effect> & more"})
    assert '<a class="note-ref titled" href="/notes/3" data-id="3"' in html
    assert ">Spacing &lt;effect&gt; &amp; more</a>" in html
    assert 'title="No. 3: Spacing &lt;effect&gt; &amp; more"' in html


def test_a_ref_without_a_title_shows_its_number():
    assert '<a class="note-ref" href="/notes/3">[[3]]</a>' in md.render("[[3]]", {3: ""})
    assert '<a class="note-ref" href="/notes/3">[[3]]</a>' in md.render("[[3]]", {3})
    assert "note-ref-ghost" in md.render("[[4]]", {3: "Three"})


def test_long_titles_are_cut_but_kept_whole_in_the_tooltip():
    title = "word " * 40
    html = md.render("[[3]]", {3: title.strip()})
    shown = html.split('">')[1].split("</a>")[0]
    assert len(shown) <= md.REF_TITLE_MAX and shown.endswith("…")
    assert title.strip() in html


@pytest.mark.parametrize("text,shown", [
    ("[[later]]", "[[later]]"),
    ("[[Later]]", "[[later]]"),
    ("[[ LATER ]]", "[[later]]"),
    ("[[later: Bjork 1994]]", "[[later: Bjork 1994]]"),
    ("[[later:Bjork]]", "[[later: Bjork]]"),
    ("[[later:]]", "[[later]]"),
])
def test_later_placeholders(text, shown):
    html = md.render(f"see {text} here")
    assert f'<span class="note-ref note-ref-later"' in html and f">{shown}</span>" in html


def test_a_later_hint_is_shown_as_typed_and_escaped():
    html = md.render('[[later: see https://x.org "q" <b>*not bold*</b>]]')
    assert "<a " not in html and "<strong>" not in html and "<em>" not in html and "<b>" not in html
    assert "&lt;b&gt;" in html or "&lt;b>" in html
    assert 'title="A link to fill in later: see https://x.org &quot;q&quot;' in html


def test_later_is_not_a_link_and_code_is_left_alone():
    assert md.extract_note_refs("[[later]] [[later: 5]]") == set()
    assert md.has_later("x [[later: a]] y")
    assert not md.has_later("`[[later]]` and\n```\n[[later]]\n```")
    assert not md.has_later("[[laterally]] [[later on]]")
    assert "note-ref-later" not in md.render("`[[later]]`")


# ---------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------

def test_note_page_and_feed_card_show_linked_titles(client, app):
    with app.app_context():
        target = dbmod.create_note("# Spacing effect\n\nbody", "2026-01-01")
        source = dbmod.create_note(f"# Follows [[{target}]]\n\nsee [[{target}]]", "2026-01-02")
    page = client.get(f"/notes/{source}").data.decode()
    assert f'data-id="{target}" title="No. {target}: Spacing effect">Spacing effect</a>' in page
    card = client.get("/").data.decode()
    assert f'Follows <a class="note-ref titled" href="/notes/{target}"' in card


def test_a_trashed_target_is_a_ghost_not_a_title(client, app):
    with app.app_context():
        target = dbmod.create_note("# Gone soon", "2026-01-01")
        source = dbmod.create_note(f"see [[{target}]]", "2026-01-02")
        dbmod.soft_delete_note(target)
    page = client.get(f"/notes/{source}").data.decode()
    assert "Gone soon" not in page and "note-ref-ghost" in page


def test_later_makes_no_backlink_or_graph_node(client, app):
    with app.app_context():
        note = dbmod.create_note("# Waiting\n\n[[later: someone]]", "2026-01-01")
        assert dbmod.get_db().execute("SELECT COUNT(*) FROM note_links").fetchone()[0] == 0
        assert not dbmod.note_has_connections(note)


# ---------------------------------------------------------------------
# has:later
# ---------------------------------------------------------------------

def test_has_later_finds_real_placeholders_only(client, app):
    with app.app_context():
        waiting = dbmod.create_note("# A\n\nsee [[Later: Bjork]]", "2026-01-01")
        dbmod.create_note("# B\n\nI'll do it later.", "2026-01-02")             # just the word
        dbmod.create_note("# C\n\n`[[later]]` is the syntax", "2026-01-03")     # in code
        trashed = dbmod.create_note("# D\n\n[[later]]", "2026-01-04")
        dbmod.soft_delete_note(trashed)
        assert [r["id"] for r in dbmod.search_notes("has:later")] == [waiting]
        assert [r["id"] for r in dbmod.search_notes("has:later bjork")] == [waiting]
        assert dbmod.search_notes("has:later -bjork") == []
        dbmod.update_note(waiting, "# A\n\nsee [[1]]", "2026-01-01")
        assert dbmod.search_notes("has:later") == []


def test_has_later_is_a_sidebar_view(client, app):
    body = client.get("/").data.decode()
    assert 'href="/?q=has:later"' in body and "Links to fill in" in body


# ---------------------------------------------------------------------
# The [[ lookup
# ---------------------------------------------------------------------

@pytest.fixture
def notes(app):
    with app.app_context():
        ids = [
            dbmod.create_note("# Spacing effect\n\nRepetition over time.", "2025-01-01"),
            dbmod.create_note("# Retrieval practice\n\nTesting yourself, with spacing.", "2025-02-01"),
            dbmod.create_note("# Interleaving\n\nMixing topics.", "2025-03-01"),
        ]
        for i in range(10):
            ids.append(dbmod.create_note(f"# Filler {i}", "2024-01-01"))
    return ids


def _lookup(client, q, exclude=None):
    url = f"/api/notes/lookup?q={q}" + (f"&exclude={exclude}" if exclude else "")
    return client.get(url).get_json()["notes"]


def test_lookup_with_nothing_typed_lists_the_latest_notes(client, notes):
    found = _lookup(client, "")
    assert [n["id"] for n in found[:3]] == [notes[2], notes[1], notes[0]]
    assert len(found) == dbmod.LOOKUP_SIZE
    assert found[0] == {"id": notes[2], "title": "Interleaving", "sort_date": "2025-03-01"}


def test_lookup_takes_the_last_word_as_a_prefix_and_puts_titles_first(client, notes):
    # "spac" matches both; the one with it in its title comes first.
    assert [n["id"] for n in _lookup(client, "spac")] == [notes[0], notes[1]]
    assert [n["id"] for n in _lookup(client, "retrieval pra")] == [notes[1]]


def test_lookup_by_number(client, notes):
    found = [n["id"] for n in _lookup(client, "1")]
    assert found[:5] == [1, 10, 11, 12, 13]


def test_lookup_leaves_out_the_note_being_edited(client, notes):
    assert notes[0] not in [n["id"] for n in _lookup(client, "spacing", exclude=notes[0])]


def test_lookup_never_errors_on_odd_input(client, notes):
    for q in ['"', "%22unclosed", "-", "NEAR(", "%23", "later%3A"]:
        assert client.get(f"/api/notes/lookup?q={q}").status_code == 200


# ---------------------------------------------------------------------
# [[refs]] in plain text -- title lines, backlink passages, History,
# Trash -- shown as their notes' titles too
# ---------------------------------------------------------------------

def test_refs_as_titles_escapes_and_leaves_missing_notes_alone():
    html = str(md.refs_as_titles("a <b> [[3]] and [[4]] & [[later]]", {3: 'Say "hi" <now>'}))
    assert html.startswith("a &lt;b&gt; ")
    assert ('<span class="ref-title" title="No. 3: Say &#34;hi&#34; &lt;now&gt;">'
            'Say &#34;hi&#34; &lt;now&gt;<span class="ref-no">3</span></span>') in html
    assert "[[4]] &amp; [[later]]" in html


def test_other_refs_in_a_backlink_passage_show_titles(client, app):
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        other = dbmod.create_note("# Other idea", "2026-01-01")
        dbmod.create_note(f"# Source\n\nsee [[{target}]] next to [[{other}]] and [[999]]", "2026-01-01")
    body = client.get(f"/notes/{target}").data.decode()
    passage = body.split('class="backlink-context">')[1].split("</span>\n")[0]
    assert f'Other idea<span class="ref-no">{other}</span>' in passage
    assert "[[999]]" in passage                      # a missing note stays a number


def test_history_and_trash_titles_show_ref_titles(client, app):
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        follow = dbmod.create_note(f"# Follow-up to [[{target}]]", "2026-01-01")
        dbmod.update_note(follow, f"# Follow-up to [[{target}]]\n\nmore", "2026-01-01")
    history = client.get("/history").data.decode()
    assert f'Follow-up to <span class="ref-title" title="No. {target}: Target">' in history
    with app.app_context():
        dbmod.soft_delete_note(follow)
    trash = client.get("/trash").data.decode()
    assert f'<span class="trash-title">Follow-up to <span class="ref-title"' in trash


def test_versions_and_graph_titles_show_ref_titles(client, app):
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        follow = dbmod.create_note(f"# Follow-up to [[{target}]]", "2026-01-01")
    expected = f'Follow-up to <span class="ref-title" title="No. {target}: Target">Target'
    assert expected in client.get(f"/notes/{follow}/versions").data.decode()
    assert expected in client.get(f"/graph/{follow}").data.decode()
