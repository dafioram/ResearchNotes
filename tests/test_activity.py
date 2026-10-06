"""Activity log: which events get recorded, and how saves merge into
editing sessions. Uses a fake clock so session timing is exact."""

import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import activity
from app import db as dbmod


class Clock:
    def __init__(self):
        self.t = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t.isoformat(timespec="seconds")

    def advance(self, minutes):
        self.t += timedelta(minutes=minutes)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(dbmod, "now_iso", c)
    return c


def events(app, note_id=None):
    with app.app_context():
        sql = "SELECT * FROM activity"
        params = ()
        if note_id is not None:
            sql += " WHERE note_id = ?"
            params = (note_id,)
        rows = dbmod.get_db().execute(sql + " ORDER BY id", params).fetchall()
        return [dict(r, detail=json.loads(r["detail"])) for r in rows]


def create(app, body="# Note\nline one", date="2026-09-24"):
    with app.app_context():
        return dbmod.create_note(body, date)


def edit(app, note_id, body, date="2026-09-24"):
    with app.app_context():
        dbmod.update_note(note_id, body, date)


# ---------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------

def test_changed_line_counts_as_one_removed_one_added():
    assert activity.line_changes("a\nb\nc", "a\nB\nc") == (1, 1)


def test_blank_lines_and_trailing_spaces_dont_count():
    assert activity.line_changes("a\n\nb", "a\nb\n\n\n") == (0, 0)
    assert activity.line_changes("a  \nb", "a\nb") == (0, 0)
    assert activity.edit_detail("a\n\nb", "d", "a\nb", "d") is None


def test_edit_detail_captures_labels_links_and_date():
    d = activity.edit_detail("# T\nsee [[3]] #old", "2026-01-01",
                             "# T\nsee [[5]] #new\nmore", "2026-01-02")
    assert d == {
        "lines_added": 2, "lines_removed": 1,
        "labels_added": ["new"], "labels_removed": ["old"],
        "links_added": [5], "links_removed": [3],
        "date_from": "2026-01-01", "date_to": "2026-01-02",
    }


def test_describe_wording():
    assert activity.describe("edited", json.dumps({"lines_added": 6, "lines_removed": 7}), 1)["changes"] == ["+6 / −7 lines"]
    assert activity.describe("edited", json.dumps({"lines_added": 1, "lines_removed": 0}), 1)["changes"] == ["+1 line"]
    assert activity.describe("edited", json.dumps({"lines_added": 0, "lines_removed": 2}), 3)["changes"] == ["−2 lines", "3 saves"]
    d = activity.describe("attached", json.dumps({"filename": "fig.png"}), 1)
    assert (d["verb"], d["filename"], d["preposition"]) == ("Attached", "fig.png", "to")


# ---------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------

def test_create_logs_created(app, clock):
    note_id = create(app)
    [e] = events(app, note_id)
    assert e["kind"] == "created" and e["updated_at"] == "2026-09-24T12:00:00+00:00"


def test_edit_logs_counts(app, clock):
    note_id = create(app, "# T\none\ntwo")
    clock.advance(60)
    edit(app, note_id, "# T\nONE\ntwo\nthree\nfour")
    e = events(app, note_id)[-1]
    assert e["kind"] == "edited"
    assert (e["detail"]["lines_added"], e["detail"]["lines_removed"]) == (3, 1)


def test_saving_without_changes_logs_nothing(app, clock):
    note_id = create(app, "# T\nbody")
    clock.advance(60)
    edit(app, note_id, "# T\nbody")
    edit(app, note_id, "# T\n\nbody\n")  # only blank lines
    assert [e["kind"] for e in events(app, note_id)] == ["created"]


def test_saves_within_window_merge_and_measure_from_session_start(app, clock):
    note_id = create(app, "# T\nbase")
    clock.advance(60)
    edit(app, note_id, "# T\nbase\nadded one")          # session starts
    clock.advance(10)
    edit(app, note_id, "# T\nbase\nadded one\nadded two")
    clock.advance(10)                                     # 20 min after start, 10 after last: still open (sliding)
    edit(app, note_id, "# T\nbase\nadded two")            # "added one" was added then removed again
    edits = [e for e in events(app, note_id) if e["kind"] == "edited"]
    assert len(edits) == 1
    assert edits[0]["save_count"] == 3
    assert (edits[0]["detail"]["lines_added"], edits[0]["detail"]["lines_removed"]) == (1, 0)
    assert edits[0]["created_at"] == "2026-09-24T13:00:00+00:00"
    assert edits[0]["updated_at"] == "2026-09-24T13:20:00+00:00"


def test_saves_after_window_start_a_new_entry_and_each_keeps_its_starting_text(app, clock):
    note_id = create(app, "# T\nbase")
    clock.advance(60)
    edit(app, note_id, "# T\nbase\nfirst")
    clock.advance(16)
    edit(app, note_id, "# T\nbase\nfirst\nsecond")
    edits = [e for e in events(app, note_id) if e["kind"] == "edited"]
    assert len(edits) == 2
    # each session's starting text is a version of the note (spec §11.5)
    assert edits[0]["base_body"] == "# T\nbase"
    assert edits[1]["base_body"] == "# T\nbase\nfirst"
    assert edits[1]["detail"]["lines_added"] == 1


def test_a_save_to_one_note_leaves_other_notes_entries_alone(app, clock):
    a = create(app, "# A")
    b = create(app, "# B")
    clock.advance(60)
    edit(app, a, "# A\nmore")
    before = [dict(e) for e in events(app, a)]
    clock.advance(30)
    edit(app, b, "# B\nmore")
    assert [dict(e) for e in events(app, a)] == before


def test_edit_reverted_within_session_disappears(app, clock):
    note_id = create(app, "# T\nbase")
    clock.advance(60)
    edit(app, note_id, "# T\nbase\ntypo")
    clock.advance(2)
    edit(app, note_id, "# T\nbase")
    assert [e["kind"] for e in events(app, note_id)] == ["created"]


def test_edits_right_after_creation_fold_into_created(app, clock):
    note_id = create(app, "# T")
    clock.advance(5)
    edit(app, note_id, "# T\nkept writing")
    clock.advance(5)
    edit(app, note_id, "# T\nkept writing\nmore")
    [e] = events(app, note_id)
    assert e["kind"] == "created" and e["save_count"] == 3
    assert e["updated_at"] == "2026-09-24T12:10:00+00:00"


def test_links_flag_and_detail(app, clock):
    target = create(app, "# Target")
    note_id = create(app, "# Source")
    clock.advance(60)
    edit(app, note_id, f"# Source\nsee [[{target}]]")
    e = events(app, note_id)[-1]
    assert e["touches_links"] == 1 and e["detail"]["links_added"] == [target]


def test_sort_date_change_alone_is_logged(app, clock):
    note_id = create(app, "# T", "2026-01-01")
    clock.advance(60)
    edit(app, note_id, "# T", "2026-02-01")
    e = events(app, note_id)[-1]
    assert (e["detail"]["date_from"], e["detail"]["date_to"]) == ("2026-01-01", "2026-02-01")


def test_delete_and_restore_logged_once(app, client, clock):
    note_id = create(app)
    client.post(f"/notes/{note_id}/delete")
    client.post(f"/notes/{note_id}/delete")     # already deleted: 404, nothing logged
    client.post(f"/notes/{note_id}/restore")
    client.post(f"/notes/{note_id}/restore")    # not deleted: nothing logged
    assert [e["kind"] for e in events(app, note_id)] == ["created", "deleted", "restored"]


def test_attach_and_remove_logged_with_filename(app, client, clock):
    note_id = create(app)
    upload = lambda: client.post(
        f"/notes/{note_id}/attachments/upload",
        data={"file": (io.BytesIO(b"bytes"), "fig.png")},
        content_type="multipart/form-data",
    )
    upload()
    upload()  # already attached: not logged again
    with app.app_context():
        att_id = dbmod.list_note_attachments(note_id)[0]["id"]
    client.post(f"/notes/{note_id}/attachments/{att_id}/remove")
    evs = events(app, note_id)
    assert [e["kind"] for e in evs] == ["created", "attached", "detached"]
    assert evs[1]["detail"] == {"filename": "fig.png"}


# ---------------------------------------------------------------------
# History page
# ---------------------------------------------------------------------

def _local_now_utc_iso(**delta):
    """A UTC timestamp for 'now in local time, shifted by delta'."""
    t = datetime.now().astimezone() + timedelta(**delta)
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def test_history_nav_link(client):
    assert b'href="/history"' in client.get("/").data


def test_history_empty_state(client):
    assert "Nothing yet." in client.get("/history").data.decode()


def test_history_groups_by_local_day_newest_first(app, client, monkeypatch):
    stamps = iter([
        _local_now_utc_iso(days=-3),
        _local_now_utc_iso(days=-1),
        _local_now_utc_iso(minutes=-1),
    ])
    monkeypatch.setattr(dbmod, "now_iso", lambda: next(stamps))
    with app.app_context():
        dbmod.create_note("# Oldest", "2026-01-01")
        dbmod.create_note("# Yesterday's", "2026-01-01")
        dbmod.create_note("# Today's", "2026-01-01")
    body = client.get("/history").data.decode()
    assert body.index("Today") < body.index("Today&#39;s") < body.index("Yesterday") < body.index("Oldest")
    three_days_ago = datetime.now().astimezone() - timedelta(days=3)
    assert f"{three_days_ago:%A} {three_days_ago.day} {three_days_ago:%B %Y}" in body


def test_history_entry_wording(app, client, clock):
    target = create(app, "# Target")
    note_id = create(app, "# Source\none")
    clock.advance(60)
    edit(app, note_id, f"# Source\none\ntwo [[{target}]] #idea")
    body = client.get("/history").data.decode()
    assert 'class="history-verb">Edited</span>' in body
    assert f'<a class="history-note" href="/notes/{note_id}"><span class="history-no">No. {note_id}</span> Source</a>' in body
    for change in ["+1 line", "added #idea",
                   # the linked note by its title, number small after it
                   f'now links to <span class="ref-title" title="No. {target}: Target">Target'
                   f'<span class="ref-no">{target}</span></span>']:
        assert f'<span class="change">{change}</span>' in body


def test_history_shows_attachment_filename(app, client, clock):
    note_id = create(app, "# With file")
    client.post(f"/notes/{note_id}/attachments/upload",
                data={"file": (io.BytesIO(b"x"), "fig.png")}, content_type="multipart/form-data")
    body = client.get("/history").data.decode()
    assert '<span class="history-file">fig.png</span> to' in body


def test_history_marks_notes_in_trash_without_linking(app, client, clock):
    note_id = create(app, "# Doomed")
    client.post(f"/notes/{note_id}/delete")
    body = client.get("/history").data.decode()
    assert 'class="history-verb">Deleted</span>' in body
    assert f'href="/notes/{note_id}"' not in body       # its page would 404
    assert 'class="history-flag" href="/trash">in Trash</a>' in body


def test_history_filters(app, client, clock):
    target = create(app, "# Target")
    plain = create(app, "# Plain")
    clock.advance(60)
    edit(app, plain, "# Plain\nmore text")
    clock.advance(60)
    linker = create(app, "# Linker")
    clock.advance(60)
    edit(app, linker, f"# Linker\n[[{target}]]")

    def ids(url):
        import re
        return re.findall(r'class="history-verb">(\w+)</span>.*?No\. (\d+)', client.get(url).data.decode(), re.S)

    assert ids("/history?show=created") == [("Created", str(linker)), ("Created", str(plain)), ("Created", str(target))]
    assert ids("/history?show=edited") == [("Edited", str(linker)), ("Edited", str(plain))]
    assert ids("/history?show=links") == [("Edited", str(linker))]
    assert "Nothing under &ldquo;Attachments&rdquo; yet." in client.get("/history?show=attachments").data.decode()
    assert client.get("/history?show=bogus").status_code == 200  # unknown filter: shows all


def test_history_paginates_and_keeps_filter(app, client, clock):
    app.config["PAGE_SIZE"] = 2
    for i in range(5):
        create(app, f"# N{i}")
        clock.advance(1)
    body = client.get("/history?show=created").data.decode()
    assert body.count('class="history-item') == 2
    assert 'href="/history?show=created&amp;page=2"' in body or 'href="/history?page=2&amp;show=created"' in body
    assert client.get("/history?page=99").status_code == 302
