"""Notes in Trash are left out of everything derived from note text --
labels, links and the search index -- and restoring brings it all back
(spec §3, §4)."""

import random

from app import db as dbmod
from app import markdown as md


def _rows(sql, *params):
    return [tuple(r) for r in dbmod.get_db().execute(sql, params)]


def _indexed(word):
    """Note ids the search index itself returns for `word`."""
    return sorted(r[0] for r in dbmod.get_db().execute(
        "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?", (word,)))


INDEX_BYTES = "SELECT SUM(LENGTH(block)) FROM notes_fts_data"


def test_trash_takes_labels_links_and_search_entry_with_it(app):
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        note = dbmod.create_note(f"# Spacing\n\n#memory practice, see [[{target}]]", "2026-01-02")

        dbmod.soft_delete_note(note)
        assert _rows("SELECT * FROM note_labels WHERE note_id = ?", note) == []
        assert _rows("SELECT * FROM note_links WHERE from_note_id = ?", note) == []
        assert _indexed("practice") == []
        assert dbmod.get_labels_with_counts() == []
        assert dbmod.get_backlink_counts([target]) == {}
        assert not dbmod.note_has_connections(target)

        dbmod.restore_note(note)
        assert _rows("SELECT name FROM note_labels WHERE note_id = ?", note) == [("memory",)]
        assert _rows("SELECT to_note_id FROM note_links WHERE from_note_id = ?", note) == [(target,)]
        assert _indexed("practice") == [note]
        assert dbmod.get_backlink_counts([target]) == {target: 1}
        assert dbmod.note_has_connections(target)


def test_links_to_a_note_in_trash_stay_and_show_as_ghosts(app):
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        source = dbmod.create_note(f"see [[{target}]]", "2026-01-02")
        dbmod.soft_delete_note(target)
        assert _rows("SELECT to_note_id FROM note_links WHERE from_note_id = ?", source) == [(target,)]
        body = dbmod.get_note(source)["body"]
        assert "note-ref-ghost" in md.render(body, dbmod.note_titles([target]))
        dbmod.restore_note(target)
        assert "note-ref-ghost" not in md.render(body, dbmod.note_titles([target]))


def test_saving_unchanged_text_leaves_the_search_index_alone(app):
    with app.app_context():
        note = dbmod.create_note("# Apple pie", "2026-01-01")
        before = dbmod.get_db().execute(INDEX_BYTES).fetchone()[0]
        dbmod.update_note(note, "# Apple pie", "2026-03-03")  # a new sort date only
        assert dbmod.get_db().execute(INDEX_BYTES).fetchone()[0] == before
        dbmod.update_note(note, "# Cherry pie", "2026-03-03")
        assert _indexed("cherry") == [note] and _indexed("apple") == []


WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]


def test_search_index_matches_the_live_notes_through_any_sequence(app):
    rng = random.Random(11)
    with app.app_context():
        ids = [dbmod.create_note(" ".join(rng.sample(WORDS, 3)), "2026-01-01") for _ in range(30)]
        for _ in range(200):
            nid, action = rng.choice(ids), rng.random()
            row = dbmod.get_note(nid, include_deleted=True)
            if action < 0.15:
                dbmod.soft_delete_note(nid)
            elif action < 0.3:
                dbmod.restore_note(nid)
            elif row["deleted_at"] is None and action < 0.45:
                dbmod.update_note(nid, row["body"], f"2026-02-{rng.randint(1, 28):02d}")
            elif row["deleted_at"] is None:
                dbmod.update_note(nid, " ".join(rng.sample(WORDS, 3)), row["sort_date"])
        live = {r["id"]: r["body"].split() for r in dbmod.list_notes()}
        for word in WORDS:
            assert _indexed(word) == sorted(i for i, words in live.items() if word in words), word
        dbmod.get_db().execute("INSERT INTO notes_fts (notes_fts) VALUES ('integrity-check')")


def test_reindex_leaves_trash_out(app):
    with app.app_context():
        kept = dbmod.create_note("# Kept\n\n#keep kiwi", "2026-01-01")
        trashed = dbmod.create_note(f"# Gone\n\n#gone mango [[{kept}]]", "2026-01-02")
        dbmod.soft_delete_note(trashed)
        conn = dbmod.get_db()
        # What an older database still holds for a note trashed before
        # notes in Trash were left out.
        conn.execute("INSERT INTO note_labels (note_id, name) VALUES (?, 'gone')", (trashed,))
        conn.execute("INSERT INTO note_links (from_note_id, to_note_id) VALUES (?, ?)", (trashed, kept))
        conn.execute("INSERT INTO notes_fts (rowid, body) SELECT id, body FROM notes WHERE id = ?", (trashed,))
        conn.commit()

        assert dbmod.reindex() == 1
        assert [tuple(r) for r in dbmod.get_labels_with_counts()] == [("keep", 1)]
        assert dbmod.get_backlinks(kept) == []
        assert _indexed("mango") == [] and _indexed("kiwi") == [kept]


def test_new_databases_store_labels_and_links_compactly(app):
    with app.app_context():
        for table in ("note_labels", "note_links"):
            sql = dbmod.get_db().execute(
                "SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]
            assert sql.rstrip().endswith("WITHOUT ROWID"), table


def test_old_search_triggers_are_replaced_in_an_existing_database(app):
    with app.app_context():
        conn = dbmod.get_db()
        conn.executescript("""
            CREATE TRIGGER notes_fts_au AFTER UPDATE ON notes BEGIN
                INSERT INTO notes_fts (notes_fts, rowid, body) VALUES ('delete', old.id, old.body);
                INSERT INTO notes_fts (rowid, body) VALUES (new.id, new.body);
            END;
        """)
        dbmod.init_db(app)  # the next startup
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        assert "notes_fts_au" not in names
        assert {"notes_fts_insert", "notes_fts_edit", "notes_fts_trash", "notes_fts_restore",
                "notes_fts_remove"} <= names
        note = dbmod.create_note("# Plum", "2026-01-01")
        dbmod.soft_delete_note(note)
        assert _indexed("plum") == []
