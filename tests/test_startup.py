"""Startup only creates missing tables (spec §13); `flask reindex` rebuilds
everything derived from note text."""

import sqlite3

from app import create_app
from app import db as dbmod


def _cfg(tmp_path):
    return {
        "TESTING": True,
        "DATABASE_PATH": str(tmp_path / "notes.db"),
        "UPLOAD_DIR": str(tmp_path / "uploads"),
    }


def _scalar(path, sql):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


INDEX_BYTES = "SELECT SUM(LENGTH(block)) FROM notes_fts_data"


def test_restarting_leaves_existing_data_alone(tmp_path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg)
    with app.app_context():
        dbmod.create_note("# Apple pie\n\nA recipe. #baking", "2026-01-01")
    index_before = _scalar(cfg["DATABASE_PATH"], INDEX_BYTES)
    history_before = _scalar(cfg["DATABASE_PATH"], "SELECT COUNT(*) FROM activity")
    for _ in range(3):
        create_app(cfg)
    # The search index used to be re-filled, and grow, on every start.
    assert _scalar(cfg["DATABASE_PATH"], INDEX_BYTES) == index_before
    assert _scalar(cfg["DATABASE_PATH"], "SELECT COUNT(*) FROM activity") == history_before


def test_reindex_rebuilds_labels_links_and_search(app):
    with app.app_context():
        a = dbmod.create_note("# Alpha\n\nAbout #memory, see [[2]].", "2026-01-01")
        b = dbmod.create_note("# Beta\n\nRetrieval practice.", "2026-01-02")
        conn = dbmod.get_db()
        conn.execute("DELETE FROM note_labels")
        conn.execute("DELETE FROM note_links")
        conn.execute("INSERT INTO notes_fts (notes_fts) VALUES ('delete-all')")
        conn.commit()
        assert dbmod.search_notes("retrieval") == []

    result = app.test_cli_runner().invoke(args=["reindex"])
    assert result.exit_code == 0, result.output
    assert "for 2 notes" in result.output

    with app.app_context():
        assert [tuple(r) for r in dbmod.get_labels_with_counts()] == [("memory", 1)]
        assert [r["id"] for r in dbmod.get_backlinks(b)] == [a]
        assert [r["id"] for r in dbmod.search_notes("retrieval")] == [b]


def test_reindex_shrinks_an_index_bloated_by_old_restarts(app):
    with app.app_context():
        for i in range(20):
            dbmod.create_note(f"# Note {i}\n\nsome shared words, and word{i}", "2026-01-01")
        conn = dbmod.get_db()
        fresh = conn.execute(INDEX_BYTES).fetchone()[0]
        for _ in range(3):  # what every startup used to do
            conn.execute("INSERT OR IGNORE INTO notes_fts (rowid, body) SELECT id, body FROM notes")
        conn.commit()
        bloated = conn.execute(INDEX_BYTES).fetchone()[0]
        assert bloated > fresh

        dbmod.reindex()
        assert conn.execute(INDEX_BYTES).fetchone()[0] <= fresh
        assert len(dbmod.search_notes("shared")) == 20
        assert [r["id"] for r in dbmod.search_notes("word7")] == [8]


def test_reindex_can_compact_the_file(app):
    with app.app_context():
        dbmod.create_note("# Note", "2026-01-01")
    result = app.test_cli_runner().invoke(args=["reindex", "--vacuum"])
    assert result.exit_code == 0, result.output
    assert "Compacted the database file." in result.output
