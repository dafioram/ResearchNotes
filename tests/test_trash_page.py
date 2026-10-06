"""Trash (spec §4): titles, pages, deleting permanently, emptying it, and
files nothing uses any more being deleted (spec §9.2)."""

import io

from app import db as dbmod


def _upload(client, note_id, content, name):
    client.post(f"/notes/{note_id}/attachments/upload",
                data={"file": (io.BytesIO(content), name)}, content_type="multipart/form-data")


def _files(app):
    with app.app_context():
        rows = dbmod.get_db().execute("SELECT hash, extension, filename FROM attachments").fetchall()
        return {r["filename"]: dbmod.attachment_path(r["hash"], r["extension"]) for r in rows}


def test_rows_show_titles_and_pages_are_paginated(client, app):
    app.config["PAGE_SIZE"] = 3
    with app.app_context():
        ids = [dbmod.create_note(f"# Idea {i}\n\nbody", "2026-01-01") for i in range(5)]
        for nid in ids:
            dbmod.soft_delete_note(nid)
    first = client.get("/trash").data.decode()
    assert "5 notes" in first and "Idea 4" in first and "Idea 1" not in first
    assert "?page=2" in first
    second = client.get("/trash?page=2").data.decode()
    assert "Idea 1" in second and "Idea 0" in second
    assert client.get("/trash?page=9").status_code == 302


def test_restore_returns_to_the_same_page(client, app):
    app.config["PAGE_SIZE"] = 2
    with app.app_context():
        ids = [dbmod.create_note(f"# N{i}", "2026-01-01") for i in range(5)]
        for nid in ids:
            dbmod.soft_delete_note(nid)
    r = client.post(f"/notes/{ids[0]}/restore?page=3")
    assert r.headers["Location"].endswith("/trash?page=3")


def test_delete_forever_removes_the_note_its_history_and_versions(client, app):
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        source = dbmod.create_note(f"# Source\n\nsee [[{target}]]", "2026-01-01")
        dbmod.soft_delete_note(target)
    r = client.post(f"/notes/{target}/delete-forever")
    assert r.status_code == 302
    with app.app_context():
        conn = dbmod.get_db()
        assert dbmod.get_note(target, include_deleted=True) is None
        assert conn.execute("SELECT COUNT(*) FROM activity WHERE note_id = ?", (target,)).fetchone()[0] == 0
        # the link to it stays, as a ghost; its number is never reused
        assert conn.execute("SELECT COUNT(*) FROM note_links WHERE to_note_id = ?", (target,)).fetchone()[0] == 1
        assert dbmod.create_note("# New", "2026-01-01") > source
    assert "note-ref-ghost" in client.get(f"/notes/{source}").data.decode()
    assert "Target" not in client.get("/trash").data.decode()


def test_only_notes_in_trash_can_be_deleted_forever(client, app):
    with app.app_context():
        live = dbmod.create_note("# Live", "2026-01-01")
    client.post(f"/notes/{live}/delete-forever")
    with app.app_context():
        assert dbmod.get_note(live) is not None


def test_a_file_goes_only_when_no_note_uses_it_trash_included(client, app):
    with app.app_context():
        a = dbmod.create_note("# A", "2026-01-01")
        b = dbmod.create_note("# B", "2026-01-01")
    _upload(client, a, b"shared bytes", "shared.txt")
    _upload(client, b, b"shared bytes", "shared.txt")   # same file, deduplicated
    _upload(client, a, b"only a", "only-a.txt")
    files = _files(app)
    with app.app_context():
        dbmod.soft_delete_note(a)
        dbmod.soft_delete_note(b)
    # In Trash, files stay: restoring brings them back.
    assert files["shared.txt"].exists() and files["only-a.txt"].exists()

    client.post(f"/notes/{a}/delete-forever")
    assert not files["only-a.txt"].exists()
    assert files["shared.txt"].exists()       # B (in Trash) still has it
    client.post(f"/notes/{b}/delete-forever")
    assert not files["shared.txt"].exists()
    assert _files(app) == {}


def test_empty_trash(client, app):
    with app.app_context():
        keep = dbmod.create_note("# Keep", "2026-01-01")
        gone = [dbmod.create_note(f"# Gone {i}", "2026-01-01") for i in range(3)]
        for nid in gone:
            dbmod.soft_delete_note(nid)
    client.post("/trash/empty")
    with app.app_context():
        assert [r["id"] for r in dbmod.list_notes()] == [keep]
        assert dbmod.get_db().execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
    page = client.get("/trash").data.decode()
    assert "Deleted 3 notes permanently." in page and "Trash is empty." in page
    assert "Empty Trash" not in page


def test_permanent_delete_buttons_ask_first(client, app):
    with app.app_context():
        nid = dbmod.create_note("# X", "2026-01-01")
        dbmod.soft_delete_note(nid)
    page = client.get("/trash").data.decode()
    assert f'data-confirm="Permanently delete No. {nid}?' in page
    assert 'data-confirm="Permanently delete all 1 note in Trash?' in page
    assert "trash.js" in page


def test_prune_files_cleans_up_what_earlier_versions_left(client, app):
    with app.app_context():
        nid = dbmod.create_note("# A", "2026-01-01")
    _upload(client, nid, b"kept", "kept.txt")
    _upload(client, nid, b"orphan", "orphan.txt")
    files = _files(app)
    with app.app_context():
        conn = dbmod.get_db()
        # As removing a file used to: unlinked, record and file left behind.
        conn.execute("DELETE FROM note_attachments WHERE attachment_id = "
                     "(SELECT id FROM attachments WHERE filename = 'orphan.txt')")
        conn.commit()
        stray = dbmod.attachment_path("f" * 64, ".bin")
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_bytes(b"half an upload")
        assert dbmod.prune_files() == (1, 2)
    assert files["kept.txt"].exists()
    assert not files["orphan.txt"].exists() and not stray.exists()
    assert set(_files(app)) == {"kept.txt"}


def test_prune_files_command(app):
    result = app.test_cli_runner().invoke(args=["prune-files"])
    assert "Removed 0 unused attachment records and 0 files" in result.output
