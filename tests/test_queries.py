"""Queries that must cost the same however many notes there are (spec §4.2):
they use the live-notes index, and look up only the notes on screen."""

import random

from app import db as dbmod


def _plan(app, sql, params=()):
    with app.app_context():
        rows = dbmod.get_db().execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return " | ".join(r["detail"] for r in rows)


FEED_PAGE = (
    "SELECT n.* FROM notes n WHERE n.deleted_at IS NULL "
    "ORDER BY n.sort_date DESC, n.id DESC LIMIT 50"
)


def test_feed_page_reads_its_order_from_the_live_notes_index(app):
    plan = _plan(app, FEED_PAGE)
    assert "idx_notes_live" in plan
    assert "TEMP B-TREE" not in plan  # i.e. it doesn't sort every note


def test_old_indexes_are_dropped_from_an_existing_database(app):
    with app.app_context():
        conn = dbmod.get_db()
        conn.execute("CREATE INDEX idx_notes_deleted ON notes (deleted_at)")
        conn.execute("CREATE INDEX idx_notes_sort_date ON notes (sort_date, id)")
        conn.commit()
        dbmod.init_db(app)  # the next startup
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_notes_deleted", "idx_notes_sort_date"}.isdisjoint(names)
    assert "TEMP B-TREE" not in _plan(app, FEED_PAGE)


def test_feed_position_matches_the_feed_order(app):
    rng = random.Random(7)
    with app.app_context():
        for i in range(40):  # plenty of shared dates, so ties fall back to id
            dbmod.create_note(f"n{i}", f"2026-01-{rng.randint(1, 5):02d}")
        for nid in rng.sample(range(1, 41), 5):
            dbmod.soft_delete_note(nid)
        order = [r["id"] for r in dbmod.list_notes()]
        for position, nid in enumerate(order):
            assert dbmod.feed_position(nid) == position


def test_lookups_about_many_ids_are_split_into_chunks(app):
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        dbmod.create_note(f"links to [[{target}]]", "2026-01-02")
        many = list(range(1, 1200)) + [target]
        assert set(dbmod.note_titles(many)) == {1, 2}
        assert dbmod.get_backlink_counts(many) == {target: 1}
        assert dbmod.get_attachment_counts(many) == {}


def _labelled_collection(app):
    """60 notes: #common on most, #rare on two, some of each in Trash."""
    rng = random.Random(3)
    with app.app_context():
        for i in range(60):
            tags = (["#common"] if i % 6 else []) + (["#rare"] if i in (7, 40) else [])
            dbmod.create_note(f"# Note {i} {' '.join(tags)}", f"2026-02-{rng.randint(1, 9):02d}")
        for nid in (2, 9, 41, 59):  # 41 is one of the two #rare notes
            dbmod.soft_delete_note(nid)


def _with_label(name):
    return [r["id"] for r in dbmod.list_notes() if f"#{name}" in r["body"]]


def test_label_pages_match_the_feed_order_whichever_way_they_are_read(app):
    _labelled_collection(app)
    with app.app_context():
        # walked, fetched by label, empty; and either of two labels, each way
        for names in (["common"], ["rare"], ["missing"], ["common", "rare"], ["rare", "missing"]):
            expected = [i for i in (r["id"] for r in dbmod.list_notes())
                        if i in {n for name in names for n in _with_label(name)}]
            assert dbmod.labels_note_count(names) == len(expected)
            for offset in (0, 10, 40):
                rows, total = dbmod.list_notes_page(names, 10, offset)
                assert total == len(expected)
                assert [r["id"] for r in rows] == expected[offset:offset + 10], (names, offset)


def test_label_counts_leave_out_notes_in_trash(app):
    _labelled_collection(app)
    with app.app_context():
        counts = {r["name"]: r["count"] for r in dbmod.get_labels_with_counts()}
        assert counts == {"common": len(_with_label("common")), "rare": len(_with_label("rare"))}
        assert list(counts) == ["common", "rare"]  # most-used first


def test_counts_cover_only_the_ids_asked_about(app):
    with app.app_context():
        a = dbmod.create_note("# A", "2026-01-01")
        b = dbmod.create_note("# B", "2026-01-01")
        dbmod.create_note(f"[[{a}]] and [[{b}]]", "2026-01-02")
        assert dbmod.get_backlink_counts([a]) == {a: 1}


# ---------------------------------------------------------------------
# Graph: expanding hop by hop gives exactly what the whole-graph walk did
# ---------------------------------------------------------------------

def _whole_graph_reference(conn, center, hops):
    """The previous implementation: load every link from a note not in
    Trash, walk it, keep every link among the notes reached."""
    edges = [tuple(r) for r in conn.execute(
        "SELECT l.from_note_id, l.to_note_id FROM note_links l "
        "JOIN notes nf ON nf.id = l.from_note_id WHERE nf.deleted_at IS NULL"
    )]
    adjacency = {}
    for a, b in edges:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    visited, frontier = {center}, {center}
    for _ in range(hops):
        frontier = set().union(*(adjacency.get(n, set()) for n in frontier)) - visited
        visited |= frontier
        if not frontier:
            break
    return visited, {(a, b) for a, b in edges if a in visited and b in visited}


def test_graph_matches_the_whole_graph_walk(app):
    rng = random.Random(42)
    with app.app_context():
        ids = []
        for i in range(120):
            refs = rng.sample(ids, min(len(ids), rng.choice([0, 1, 1, 2, 3])))
            if rng.random() < 0.05:
                refs.append(900 + i)  # a note that doesn't exist: a ghost
            ids.append(dbmod.create_note(" ".join(f"[[{r}]]" for r in refs) or "alone", "2026-01-01"))
        for nid in rng.sample(ids, 10):
            dbmod.soft_delete_note(nid)
        live = [r["id"] for r in dbmod.list_notes()]
        conn = dbmod.get_db()
        for center in rng.sample(live, 15):
            for hops in (1, 2, 3, 5):
                data = dbmod.get_graph_data(center, hops)
                nodes, edges = _whole_graph_reference(conn, center, hops)
                assert {n["id"] for n in data["nodes"]} == nodes, (center, hops)
                assert {(e["from"], e["to"]) for e in data["edges"]} == edges, (center, hops)
