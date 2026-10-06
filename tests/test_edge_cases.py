"""Edge cases the rest of the suite didn't reach: missing notes and files,
refused input, pager gaps, odd text in the renderer, History wording."""

import io
import json

import pytest

from app import activity, create_app
from app import db as dbmod
from app import markdown as md
from app.routes import _diff_lines, _page_window
from tests.test_activity import Clock


@pytest.fixture
def note(app):
    with app.app_context():
        return dbmod.create_note("# A note", "2026-01-01")


# ---------------------------------------------------------------------
# Missing and trashed notes, missing files
# ---------------------------------------------------------------------

@pytest.mark.parametrize("method,url", [
    ("get", "/notes/{gone}/edit"),
    ("post", "/notes/{gone}/edit"),
    ("get", "/graph/{gone}"),
    ("get", "/api/graph/{gone}"),
    ("post", "/notes/{gone}/attachments/upload"),
    ("get", "/notes/{gone}/fragment"),
    ("get", "/notes/{trashed}/edit"),
    ("get", "/graph/{trashed}"),
])
def test_missing_or_trashed_notes_are_404(client, app, method, url):
    with app.app_context():
        trashed = dbmod.create_note("# T", "2026-01-01")
        dbmod.soft_delete_note(trashed)
    r = getattr(client, method)(url.format(gone=999, trashed=trashed),
                                data={"body": "x", "sort_date": "2026-01-01"})
    assert r.status_code == 404


def test_a_file_record_without_its_file_is_404_and_can_still_be_removed(client, app, note):
    client.post(f"/notes/{note}/attachments/upload", content_type="multipart/form-data",
                data={"file": (io.BytesIO(b"bytes"), "f.txt")})
    with app.app_context():
        row = dbmod.get_db().execute("SELECT id, hash, extension FROM attachments").fetchone()
        dbmod.attachment_path(row["hash"], row["extension"]).unlink()   # lost from disk
    assert client.get(f"/files/{row['hash']}").status_code == 404
    assert client.get("/files/" + "0" * 64).status_code == 404
    r = client.post(f"/notes/{note}/attachments/{row['id']}/remove")     # nothing to delete: fine
    assert r.status_code in (200, 302)
    with app.app_context():
        assert dbmod.get_db().execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0


def test_feed_position_of_a_missing_note_is_the_top(app):
    with app.app_context():
        assert dbmod.feed_position(999) == 0


# ---------------------------------------------------------------------
# Refused input
# ---------------------------------------------------------------------

def test_a_bad_date_is_refused_both_ways(client, app, note):
    r = client.post(f"/notes/{note}/edit", data={"body": "changed", "sort_date": "soon"},
                    headers={"X-Requested-With": "fetch"})
    assert r.status_code == 400 and r.get_json()["ok"] is False
    r = client.post(f"/notes/{note}/edit", data={"body": "changed", "sort_date": ""})
    assert r.status_code == 400
    page = r.data.decode()
    assert "changed" in page            # what was typed is shown again, not lost
    r = client.post("/notes/new", data={"body": "new", "sort_date": "2026-02-30"},
                    headers={"X-Requested-With": "fetch"})
    assert r.status_code == 400
    with app.app_context():
        assert dbmod.get_note(note)["body"] == "# A note"
        assert len(dbmod.list_notes()) == 1


def test_restoring_a_version_that_matches_the_note_changes_nothing(client, app, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(dbmod, "now_iso", clock)
    with app.app_context():
        nid = dbmod.create_note("# v1", "2026-01-01")
        clock.advance(60)
        dbmod.update_note(nid, "# v2", "2026-01-01")
        clock.advance(60)
        dbmod.update_note(nid, "# v1", "2026-01-01")    # back by hand
        newest_v1 = next(v for v in dbmod.note_versions(nid) if v["body"] == "# v2")
        older_v1 = dbmod.note_versions(nid)[-1]
        assert not dbmod.restore_version(nid, 999)      # no such version
    r = client.post(f"/notes/{nid}/versions/{older_v1['id']}/restore", follow_redirects=True)
    assert "same as the note now" in r.data.decode()
    assert newest_v1["body"] == "# v2"


def test_saves_after_an_old_style_session_start_a_new_one(app, monkeypatch):
    # Sessions recorded before versions were kept have no starting text;
    # a save soon after one can't be measured against it, so it starts anew.
    clock = Clock()
    monkeypatch.setattr(dbmod, "now_iso", clock)
    with app.app_context():
        nid = dbmod.create_note("# A", "2026-01-01")
        clock.advance(60)
        dbmod.update_note(nid, "# A\nmore", "2026-01-01")
        conn = dbmod.get_db()
        conn.execute("UPDATE activity SET base_body = NULL, base_sort_date = NULL WHERE kind = 'edited'")
        conn.commit()
        clock.advance(2)
        dbmod.update_note(nid, "# A\nmore\nagain", "2026-01-01")
        edits = conn.execute("SELECT base_body FROM activity WHERE kind = 'edited' ORDER BY id").fetchall()
        assert [e[0] for e in edits] == [None, "# A\nmore"]


# ---------------------------------------------------------------------
# Small pieces
# ---------------------------------------------------------------------

@pytest.mark.parametrize("page,total,expected", [
    (1, 1, [1]),
    (1, 4, [1, 2, 3, 4]),
    (5, 9, [1, 2, 3, 4, 5, 6, 7, 8, 9]),          # gaps of one page are filled in
    (5, 12, [1, 2, 3, 4, 5, 6, 7, None, 12]),
    (10, 20, [1, None, 8, 9, 10, 11, 12, None, 20]),
])
def test_page_window(page, total, expected):
    assert _page_window(page, total) == expected


def test_diffs_mark_gaps_between_changes():
    old = "\n".join(f"line {i}" for i in range(12))
    new = old.replace("line 1\n", "line one\n").replace("line 10", "line ten")
    kinds = [kind for kind, _ in _diff_lines(old, new)]
    assert kinds[0] != "gap" and "gap" in kinds        # a gap between, not before, the changes
    assert _diff_lines("a", "a") == []


def test_graph_labels_from_odd_first_lines(app):
    with app.app_context():
        a = dbmod.create_note("# " + "a long title " * 10, "2026-01-01")
        b = dbmod.create_note(f"#\n\n[[{a}]]", "2026-01-01")
        c = dbmod.create_note(f"\n\n\n[[{a}]]", "2026-01-01")
        labels = {n["id"]: n["label"] for n in dbmod.get_graph_data(a, 1)["nodes"]}
    assert labels[a].endswith("…") and len(labels[a]) < 60
    assert labels[b] == f"{b}. (untitled)"
    assert labels[c].startswith(f"{c}. [[")


@pytest.mark.parametrize("text,expected", [
    ("just https:// here", "just https:// here"),           # no address after the scheme
    ("odd \x00LINK99\x00 text", "odd \x00LINK99\x00 text"),  # looks like a placeholder, isn't
])
def test_renderer_leaves_odd_text_alone(text, expected):
    assert md.render(text) == f"<p>{expected}</p>"


def test_backlink_passage_keeps_inline_fenced_code():
    contexts = md.ref_contexts("# Title\n\nsee [[5]] then ```py x = 1```", 5)
    assert contexts[0]["after"] == " then x = 1"


def test_history_words_for_removed_labels_and_links():
    detail = {"lines_added": 0, "lines_removed": 0, "labels_removed": ["draft"], "links_removed": [3]}
    assert activity.describe("edited", json.dumps(detail), 1)["changes"] == [
        "removed #draft", "no longer links to [[3]]"]


def test_app_reads_its_folder_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("PAGE_SIZE", "7")
    app = create_app()
    assert app.config["DATABASE_PATH"].startswith(str(tmp_path / "elsewhere"))
    assert app.config["PAGE_SIZE"] == 7


def test_init_db_command(app):
    result = app.test_cli_runner().invoke(args=["init-db"])
    assert "Database initialized." in result.output
