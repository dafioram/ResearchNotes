"""Version history (spec §11.5): one version per editing session, viewed
and restored from the note's Versions page."""

import re

import pytest

from app import db as dbmod
from tests.test_activity import Clock


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(dbmod, "now_iso", c)
    return c


@pytest.fixture
def note(app, clock):
    """A note written once, then edited in two later sittings (each with
    several saves): three versions of text, two of them earlier ones."""
    with app.app_context():
        nid = dbmod.create_note("# Draft\n\nfirst take", "2026-09-01")
        clock.advance(5)
        dbmod.update_note(nid, "# Draft\n\nfirst take, fixed", "2026-09-01")   # same sitting as creating
        clock.advance(60)
        dbmod.update_note(nid, "# Draft\n\nsecond", "2026-09-01")
        clock.advance(3)
        dbmod.update_note(nid, "# Draft\n\nsecond, more", "2026-09-02")
        clock.advance(60 * 24)
        dbmod.update_note(nid, "# Final\n\nthird", "2026-09-02")
    return nid


def _versions(app, nid):
    with app.app_context():
        return dbmod.note_versions(nid)


def test_one_version_per_sitting_newest_first(app, note):
    versions = _versions(app, note)
    assert [(v["body"], v["sort_date"]) for v in versions] == [
        ("# Draft\n\nsecond, more", "2026-09-02"),     # as the second sitting left it
        ("# Draft\n\nfirst take, fixed", "2026-09-01"),  # as creating it left it
    ]
    # when each text was last saved: the end of the sitting before
    assert versions[0]["saved_at"] == "2026-09-24T13:08:00+00:00"
    assert versions[1]["saved_at"] == "2026-09-24T12:05:00+00:00"
    assert versions[0]["replaced_at"] == "2026-09-25T13:08:00+00:00"


def test_saving_no_longer_scans_old_sessions(app, note, clock):
    # The per-save "clear old starting text" UPDATE is gone: versions stay.
    clock.advance(60)
    with app.app_context():
        dbmod.update_note(note, "# Final\n\nthird, again", "2026-09-02")
    assert len(_versions(app, note)) == 3


def test_restore_is_an_edit_of_its_own_and_can_be_undone(app, note, clock):
    oldest = _versions(app, note)[-1]
    clock.advance(1)   # well inside the last sitting's 15 minutes
    with app.app_context():
        assert dbmod.restore_version(note, oldest["id"])
        row = dbmod.get_note(note)
        assert (row["body"], row["sort_date"]) == (oldest["body"], oldest["sort_date"])
    versions = _versions(app, note)
    # the text the restore replaced is now the newest version
    assert versions[0]["body"] == "# Final\n\nthird"
    assert versions[0]["saved_at"] == "2026-09-25T13:08:00+00:00"
    with app.app_context():
        assert dbmod.restore_version(note, versions[0]["id"])   # undo
        assert dbmod.get_note(note)["body"] == "# Final\n\nthird"


def test_restoring_what_is_already_there_changes_nothing(app, note, clock):
    clock.advance(30)
    with app.app_context():
        dbmod.update_note(note, "# Draft\n\nsecond, more", "2026-09-02")   # by hand
        newest_before = _versions(app, note)
        assert not dbmod.restore_version(note, newest_before[1]["id"])
        assert _versions(app, note) == newest_before


def test_saves_after_a_restore_keep_it_in_history(client, app, note, clock):
    oldest = _versions(app, note)[-1]
    clock.advance(1)
    with app.app_context():
        dbmod.restore_version(note, oldest["id"])
        clock.advance(2)
        dbmod.update_note(note, oldest["body"] + "\nplus a line", oldest["sort_date"])
    page = client.get("/history").data.decode()
    assert "restored the version from" in page


def test_versions_and_version_pages(client, app, note):
    page = client.get(f"/notes/{note}/versions").data.decode()
    assert "2 earlier versions" in page
    links = re.findall(rf'href="(/notes/{note}/versions/\d+)"', page)
    assert len(links) == 2

    newest = client.get(links[0]).data.decode()
    assert "second, more" in newest and "Restore this version" in newest
    # what changed since, as a diff, and the date change
    assert '<span class="diff-remove">- second, more</span>' in newest
    assert '<span class="diff-add">+ third</span>' in newest
    assert "Sort date" not in newest   # same date as now

    oldest = client.get(links[1]).data.decode()
    assert "Sort date 2026-09-01 &rarr; 2026-09-02" in oldest

    r = client.post(links[1] + "/restore")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/notes/{note}")
    assert "first take, fixed" in client.get(f"/notes/{note}").data.decode()


def test_a_versions_link_on_the_note_and_history(client, app, note):
    assert f'href="/notes/{note}/versions"' in client.get(f"/notes/{note}").data.decode()
    history = client.get("/history").data.decode()
    assert history.count("see before") == 2


def test_version_text_is_escaped(client, app, clock):
    with app.app_context():
        nid = dbmod.create_note("# T\n\n<script>alert(1)</script>", "2026-01-01")
        clock.advance(60)
        dbmod.update_note(nid, "# T\n\nsafe", "2026-01-01")
        [v] = dbmod.note_versions(nid)
    page = client.get(f"/notes/{nid}/versions/{v['id']}").data.decode()
    assert "<script>alert" not in page and "&lt;script&gt;alert(1)" in page


def test_missing_and_trashed_give_404(client, app, note):
    [v, _] = _versions(app, note)
    assert client.get(f"/notes/{note}/versions/999").status_code == 404
    assert client.post(f"/notes/{note}/versions/999/restore").status_code == 404
    assert client.get(f"/notes/{note + 1}/versions").status_code == 404
    with app.app_context():
        dbmod.soft_delete_note(note)
    assert client.get(f"/notes/{note}/versions").status_code == 404
    assert client.get(f"/notes/{note}/versions/{v['id']}").status_code == 404


def test_a_note_never_edited_later_has_no_versions(client, app):
    with app.app_context():
        nid = dbmod.create_note("# New", "2026-01-01")
    assert "No earlier versions yet" in client.get(f"/notes/{nid}/versions").data.decode()
