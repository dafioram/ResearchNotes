"""Random sequences of everything that changes notes, checking after each
step that what's derived from them is exactly right (spec §3, §6.3, §7,
§9.2, §11). Several features keep separate tables in step with the notes'
text -- labels, links, the search index, files, History and versions --
each by its own rules (Trash leaves them out, restore brings them back,
deleting forever removes files nothing uses...). This walks through
hundreds of mixed operations so a rule one feature broke for another shows
up, with the seed and step that broke it."""

import io
import random

import pytest

from app import db as dbmod
from app import markdown as md
from tests.test_activity import Clock

VOCAB = ["memory", "learning", "physics", "physics-quantum", "physics-optics", "draft", "Café", "node.js"]
FILES = [b"alpha", b"beta", b"gamma"]   # few, so uploads are often deduplicated


class Model:
    """What the database should hold, kept alongside it."""

    def __init__(self):
        self.live, self.trash = set(), set()
        self.marker = {}       # note id -> a word only that note's text has


def _body(rng, note_marker, existing):
    """Random note text: labels (some inside code, which don't count),
    links to notes that exist or don't, [[later]], a unique marker word."""
    parts = [f"# Note {note_marker}", note_marker]
    for _ in range(rng.randint(0, 3)):
        parts.append("#" + rng.choice(VOCAB))
    if rng.random() < 0.3:
        parts.append(f"`#{rng.choice(VOCAB)}`")
    for _ in range(rng.randint(0, 2)):
        target = rng.choice(sorted(existing)) if existing and rng.random() < 0.8 else rng.randint(900, 999)
        parts.append(f"[[{target}]]")
    if rng.random() < 0.15:
        parts.append("[[later: someday]]")
    return "\n\n".join(parts)


def _rows(conn, sql, *params):
    return {tuple(r) for r in conn.execute(sql, params)}


def check(app, model, where):
    with app.app_context():
        conn = dbmod.get_db()
        notes = {r["id"]: r for r in conn.execute("SELECT * FROM notes")}
        live = {i for i, r in notes.items() if r["deleted_at"] is None}
        assert live == model.live, where
        assert set(notes) - live == model.trash, where

        # labels and links: exactly what the live notes' text says (§3, §6.3)
        assert _rows(conn, "SELECT note_id, name FROM note_labels") == {
            (i, name) for i in live for name in md.extract_labels(notes[i]["body"])}, where
        assert _rows(conn, "SELECT from_note_id, to_note_id FROM note_links") == {
            (i, ref) for i in live for ref in md.extract_note_refs(notes[i]["body"])}, where

        # the search index holds the live notes and nothing else (§7)
        for i, row in notes.items():
            found = _rows(conn, "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?", f'"{model.marker[i]}"')
            assert found == ({(i,)} if i in live else set()), (where, i)

        # every file record is used by some note, and disk matches records (§9.2)
        records = conn.execute("SELECT id, hash, extension FROM attachments").fetchall()
        used = {r[0] for r in conn.execute("SELECT DISTINCT attachment_id FROM note_attachments")}
        assert {r["id"] for r in records} == used, where
        upload_dir = dbmod.attachment_path("00" * 32, "").parent.parent.parent
        on_disk = {p for p in upload_dir.glob("??/??/*") if p.is_file()} if upload_dir.exists() else set()
        assert on_disk == {dbmod.attachment_path(r["hash"], r["extension"]) for r in records}, where

        # History, versions and file links belong to notes that exist (§11)
        assert not _rows(conn, "SELECT id FROM activity WHERE note_id NOT IN (SELECT id FROM notes)"), where
        assert not _rows(conn, "SELECT note_id FROM note_attachments WHERE note_id NOT IN (SELECT id FROM notes)"), where

        # label counts as the sidebar shows them
        counts = {r["name"]: r["count"] for r in dbmod.get_labels_with_counts()}
        expected: dict = {}
        for i in live:
            for name in md.extract_labels(notes[i]["body"]):
                expected[name] = expected.get(name, 0) + 1
        assert counts == expected, where


@pytest.mark.parametrize("seed", range(6))
def test_random_operations_keep_everything_in_step(app, client, monkeypatch, seed):
    rng = random.Random(seed)
    clock = Clock()
    monkeypatch.setattr(dbmod, "now_iso", clock)
    model = Model()
    counter = iter(range(10**6))

    def new_marker():
        return f"mark{seed}x{next(counter)}"

    for step in range(120):
        clock.advance(rng.choice([1, 5, 20, 90]))    # inside and outside editing sessions
        ops = ["create"] * 3
        if model.live:
            ops += ["edit"] * 4 + ["trash", "attach", "attach", "detach", "restore_version"]
        if model.trash:
            ops += ["restore", "delete_forever"]
        op = rng.choice(ops)
        where = f"seed {seed}, step {step}: {op}"
        existing = model.live | model.trash

        with app.app_context():
            if op == "create":
                marker = new_marker()
                note = dbmod.create_note(_body(rng, marker, existing), "2026-01-01")
                model.live.add(note)
                model.marker[note] = marker
            elif op == "edit":
                note = rng.choice(sorted(model.live))
                dbmod.update_note(note, _body(rng, model.marker[note], existing),
                                  rng.choice(["2026-01-01", "2026-02-02"]))
            elif op == "trash":
                note = rng.choice(sorted(model.live))
                dbmod.soft_delete_note(note)
                model.live.remove(note)
                model.trash.add(note)
            elif op == "restore":
                note = rng.choice(sorted(model.trash))
                dbmod.restore_note(note)
                model.trash.remove(note)
                model.live.add(note)
            elif op == "delete_forever":
                note = rng.choice(sorted(model.trash))
                assert dbmod.delete_forever([note]) == 1
                model.trash.remove(note)
            elif op == "restore_version":
                note = rng.choice(sorted(model.live))
                versions = dbmod.note_versions(note)
                if versions:
                    before = dbmod.get_note(note)
                    if dbmod.restore_version(note, rng.choice(versions)["id"]):
                        # undoing it: the text it replaced is now the newest version
                        newest = dbmod.note_versions(note)[0]
                        assert (newest["body"], newest["sort_date"]) == (before["body"], before["sort_date"]), where
        if op == "attach":
            note = rng.choice(sorted(model.live))
            client.post(f"/notes/{note}/attachments/upload", content_type="multipart/form-data",
                        data={"file": (io.BytesIO(rng.choice(FILES)), rng.choice(["a.txt", "b.pdf"]))})
        elif op == "detach":
            note = rng.choice(sorted(model.live))
            with app.app_context():
                files = dbmod.list_note_attachments(note)
            if files:
                client.post(f"/notes/{note}/attachments/{rng.choice(files)['id']}/remove")
        check(app, model, where)

    # Rebuilding everything from the text changes nothing (flask reindex).
    with app.app_context():
        conn = dbmod.get_db()
        before = (_rows(conn, "SELECT * FROM note_labels"), _rows(conn, "SELECT * FROM note_links"))
        dbmod.reindex()
        assert (_rows(conn, "SELECT * FROM note_labels"), _rows(conn, "SELECT * FROM note_links")) == before
    check(app, model, f"seed {seed}: after reindex")
