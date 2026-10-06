"""The search syntax (spec §7): parsing, and what each part finds."""

import pytest

from app import db as dbmod
from app import search


# ---------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------

def test_parse_every_kind_of_term():
    q = search.parse('memory "spaced repetition" retriev* -flashcards -"rote learning" '
                     '#Learning -#draft is:unlinked has:file after:2025-03 before:2026')
    assert q.words == ['"memory"', '"spaced repetition"', '"retriev"*']
    assert q.exclude_words == ['"flashcards"', '"rote learning"']
    assert q.labels == ["learning"] and q.exclude_labels == ["draft"]
    assert q.unlinked and q.has_file
    assert (q.after, q.before) == ("2025-03-01", "2026-01-01")
    assert q.problems == [] and q.number is None


def test_typed_operators_are_only_ever_text():
    # FTS5's own syntax (OR, NEAR, parentheses, stray quotes) never gets through.
    q = search.parse('a OR b NEAR(c) "unclosed')
    assert q.words == ['"a"', '"OR"', '"b"', '"NEAR(c)"', '"unclosed"']
    assert search.parse("!!! ---").is_empty  # nothing that could match anything


def test_dates_must_be_real_and_bad_ones_are_reported():
    assert search.parse("after:2025-02-29").problems  # not a leap year
    assert search.parse("before:soon").problems
    assert search.parse("after:2024-02-29").after == "2024-02-29"


def test_a_number_alone_is_a_number_search():
    assert search.parse("1234").number == "1234"
    assert search.parse("1234 notes").number is None


def test_plus_label_is_required_and_a_bare_plus_is_a_word():
    q = search.parse("#a #b +#c -#d +word")
    assert (q.labels, q.required_labels, q.exclude_labels) == (["a", "b"], ["c"], ["d"])
    assert q.words == ['"+word"']


def test_only_labels():
    assert search.parse("#memory").only_labels == ["memory"]
    assert search.parse("#a #b").only_labels == ["a", "b"]
    assert search.parse("+#memory").only_labels is None
    assert search.parse("#memory +#learning").only_labels is None
    assert search.parse("#memory word").only_labels is None


def test_toggle_adds_and_removes_a_term():
    assert search.toggle("retrieval", "#memory") == "retrieval #memory"
    assert search.toggle("retrieval #Memory", "#memory") == "retrieval"
    assert search.toggle("", "is:unlinked") == "is:unlinked"


# ---------------------------------------------------------------------
# What each part finds, against a small collection
# ---------------------------------------------------------------------

NOTES = [
    ("# Spacing\n\nSpaced repetition beats cramming. #memory", "2025-01-10"),
    ("# Retrieval\n\nRetrieving from memory strengthens it. #memory #learning", "2025-03-05"),
    ("# Flashcards\n\nFlashcards are retrieval practice too. #learning", "2025-06-20"),
    ("# Draft idea\n\nRepetition of the spaced kind, unsure. #draft", "2026-02-01"),
]


@pytest.fixture
def collection(app):
    with app.app_context():
        ids = [dbmod.create_note(body, date) for body, date in NOTES]
        dbmod.create_note(f"# Links\n\nsee [[{ids[0]}]]", "2026-03-01")
    return ids


def _found(app, raw):
    with app.app_context():
        return sorted(r["id"] for r in dbmod.search_notes(raw))


@pytest.mark.parametrize("raw,expected", [
    ("repetition", [1, 4]),
    ('"spaced repetition"', [1]),          # the exact phrase, in order
    ("retriev*", [2, 3]),                   # prefix
    ("retrieve", [2, 3]),                   # word forms (stemming)
    ("repetition -draft", [1]),             # "-word" excludes a word...
    ('repetition -"spaced kind"', [1]),     # ...or a phrase
    ("#memory", [1, 2]),
    ("#memory #learning", [1, 2, 3]),       # several labels: any of them
    ("#memory +#learning", [2]),            # +#label: must have it
    ("+#memory +#learning", [2]),           # several +#: all of them
    ("#draft #learning -#memory", [3, 4]),
    ("#memory #draft repetition", [1, 4]),  # either label, and the word
    ("#learning -#memory", [3]),
    ("is:unlinked", [2, 3, 4]),             # 1 is linked to, 5 links out
    ("after:2025-03 before:2025-07", [2, 3]),
    ("after:2026", [4, 5]),
    ("#memory after:2025-02", [2]),
    ("-flashcards #learning", [2]),         # exclusion alone, with a label
    ("nothingmatchesthis", []),
])
def test_each_part_of_the_syntax(app, collection, raw, expected):
    assert _found(app, raw) == expected


def test_has_file(client, app, collection):
    import io
    client.post(f"/notes/{collection[2]}/attachments/upload",
                data={"file": (io.BytesIO(b"x"), "deck.txt")}, content_type="multipart/form-data")
    assert _found(app, "has:file") == [collection[2]]


def test_more_matches_rank_higher(app):
    with app.app_context():
        once = dbmod.create_note("memory once, and many other words besides this one", "2026-01-01")
        often = dbmod.create_note("memory memory memory", "2025-01-01")
        assert [r["id"] for r in dbmod.search_notes("memory")] == [often, once]


def test_paging_is_done_in_sql_and_counts_everything(app):
    from app import search as s
    with app.app_context():
        ids = [dbmod.create_note(f"common word {i}", "2026-01-01") for i in range(7)]
        q = s.parse("common")
        everything = [r["id"] for r in dbmod.search_page(q, -1, 0)[0]]
        pages = [dbmod.search_page(q, 3, off) for off in (0, 3, 6)]
        assert all(total == 7 for _, total in pages)
        assert [r["id"] for rows, _ in pages for r in rows] == everything
        assert sorted(everything) == ids


# ---------------------------------------------------------------------
# On the page
# ---------------------------------------------------------------------

def test_results_show_the_matching_passage_highlighted_and_escaped(client, app):
    with app.app_context():
        dbmod.create_note("# Title\n\nA line with <script>x</script> and the retrieval effect.", "2026-01-01")
    body = client.get("/?q=retrieval").data.decode()
    assert '<p class="search-match">' in body
    assert "<mark>retrieval</mark>" in body
    assert "<script>x" not in body and "&lt;script&gt;x" in body


def test_banner_counts_matches_and_reports_problems(client, app, collection):
    body = client.get("/?q=%23memory+after:someday").data.decode()
    assert "2 notes" in body and "match" in body
    assert "couldn&#39;t read the date" in body or "couldn't read the date" in body


def test_sidebar_links_toggle_their_term(client, app, collection):
    body = client.get("/?q=%23memory").data.decode()
    sidebar = body.split('<aside class="sidebar">')[1].split("</aside>")[0]
    assert 'href="/"' in sidebar                       # #memory is on: its link takes it off
    assert 'href="/?q=%23memory+%23learning"' in sidebar
    assert 'href="/?q=%23memory+is:unlinked"' in sidebar


def test_number_search_puts_matching_numbers_first(client, app):
    with app.app_context():
        for _ in range(12):
            dbmod.create_note("filler", "2026-01-01")
        mention = dbmod.create_note("cites figure 1 and 11", "2026-01-01")
    with app.app_context():
        ids = [r["id"] for r in dbmod.search_notes("1")]
    assert ids[:5] == [1, 10, 11, 12, 13]
    assert mention == 13
