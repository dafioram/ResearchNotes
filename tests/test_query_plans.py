"""Query plans (spec §4.2): every page reads through an index, never every
note. Timings are too noisy for a test, but a lost index shows up in the
plan: each function's SELECTs are recorded as they run and checked with
EXPLAIN QUERY PLAN -- so this also covers SQL added later, without listing
it here. (PR 4 found the feed reading and sorting all 36,500 notes, text
and all, to show 50; this is what would have caught it.)"""

import io
import re

import pytest

from app import db as dbmod
from app import search

# A full scan of notes or the History log: "SCAN n" / "SCAN notes" with no
# index after it. Scans of small per-note tables through their keys are fine.
FULL_SCAN = re.compile(r"^SCAN (n|notes|a|activity)$")


@pytest.fixture
def data(app, client):
    with app.app_context():
        ids = []
        for i in range(60):
            refs = f" [[{ids[-1]}]]" if ids and i % 3 else ""
            ids.append(dbmod.create_note(f"# Note {i} about memory #topic{i % 7} #physics-q{i % 2}{refs}",
                                         f"2026-{i % 12 + 1:02d}-{i % 27 + 1:02d}"))
        for nid in ids[:5]:
            dbmod.soft_delete_note(nid)
        dbmod.update_note(ids[10], "# Note 10 edited #topic3", "2026-01-01")
    client.post(f"/notes/{ids[20]}/attachments/upload", content_type="multipart/form-data",
                data={"file": (io.BytesIO(b"x"), "f.txt")})
    return ids


def _plans(app, call):
    """Run call() in the app, recording its SELECTs; return {sql: plan lines}."""
    with app.app_context():
        conn = dbmod.get_db()
        statements = []
        conn.set_trace_callback(statements.append)
        try:
            call()
        finally:
            conn.set_trace_callback(None)
        return {
            sql: [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql)]
            for sql in dict.fromkeys(statements)
            if sql.lstrip().upper().startswith(("SELECT", "WITH"))
        }


CALLS = {
    "feed page": lambda ids: dbmod.list_notes_page(None, 50, 0),
    "feed page 3": lambda ids: dbmod.list_notes_page(None, 10, 20),
    "label page (walked)": lambda ids: dbmod.list_notes_page(["topic1"], 5, 0),
    "label page (fetched)": lambda ids: dbmod.list_notes_page(["topic1"], 10, 1000),
    "either label / namespace": lambda ids: dbmod.list_notes_page(["topic1", "physics-*"], 10, 0),
    "search: words": lambda ids: dbmod.search_page(search.parse("memory note"), 50, 0),
    "search: everything": lambda ids: dbmod.search_page(
        search.parse('memory -"note 3" #topic1 +#physics-* -#topic2 is:unlinked has:file after:2026-02 before:2026-11'), 50, 0),
    "search: a number": lambda ids: dbmod.search_page(search.parse("1"), 50, 0),
    "search: has:later": lambda ids: dbmod.search_page(search.parse("has:later"), 50, 0),
    "snippets": lambda ids: dbmod.search_snippets(search.parse("memory"), ids[5:15]),
    "jump to a month": lambda ids: dbmod.date_position(search.parse("#topic1"), "2026-05-01"),
    "[[ lookup": lambda ids: (dbmod.lookup_notes(""), dbmod.lookup_notes("mem"), dbmod.lookup_notes("1")),
    "card badges and titles": lambda ids: (dbmod.get_backlink_counts(ids), dbmod.get_attachment_counts(ids),
                                           dbmod.note_titles(ids), dbmod.get_link_counts(ids),
                                           dbmod.get_card_labels(ids)),
    "a note's page": lambda ids: (dbmod.get_note(ids[20]), dbmod.get_backlinks(ids[20]),
                                  dbmod.note_has_connections(ids[20]), dbmod.list_note_attachments(ids[20])),
    "graph, 3 hops": lambda ids: dbmod.get_graph_data(ids[30], 3),
    "label sidebar": lambda ids: (dbmod.get_labels_with_counts(), dbmod.label_recency(),
                                  dbmod.labels_note_count(["physics-*"])),
    "landing on a note": lambda ids: dbmod.feed_position(ids[40]),
    # History's filters (New notes, Edits, Link changes...) do read the
    # whole log: 10-25 ms at ten years against 5 for All -- left as is for
    # now, so only All is held to this.
    "History": lambda ids: dbmod.activity_page("all", 50, 0),
    "versions": lambda ids: dbmod.note_versions(ids[10]),
    "images by short hash": lambda ids: (dbmod.files_by_prefix(["191ff6f6b235", "0000000000000"]),
                                         dbmod.get_attachment_by_prefix("191ff6f6b235")),
    "Trash": lambda ids: dbmod.deleted_notes_page(50, 0),
}


@pytest.mark.parametrize("name", CALLS)
def test_no_full_scans(app, data, name):
    plans = _plans(app, lambda: CALLS[name](data))
    assert plans, f"{name}: no queries recorded"
    for sql, plan in plans.items():
        scans = [line for line in plan if FULL_SCAN.match(line)]
        assert not scans, f"{name}: {scans} in\n{sql}\n" + "\n".join(plan)


@pytest.mark.parametrize("name", ["feed page", "feed page 3", "label page (walked)", "jump to a month"])
def test_feed_order_comes_from_the_index(app, data, name):
    # Read in order and stop after the page -- no sorting every match.
    for sql, plan in _plans(app, lambda: CALLS[name](data)).items():
        if "ORDER BY" in sql.upper():
            assert not any("TEMP B-TREE FOR ORDER BY" in line for line in plan), f"{name}:\n{sql}\n{plan}"
