"""The editor's #label suggestions (spec §6.1) replace the label picker."""

from app import db as dbmod


def test_api_labels_lists_labels_in_use_most_used_first(client, app):
    with app.app_context():
        dbmod.create_note("#memory #learning", "2026-01-01")
        dbmod.create_note("#memory #draft", "2026-01-02")
        gone = dbmod.create_note("#trashed", "2026-01-03")
        dbmod.soft_delete_note(gone)
    labels = client.get("/api/labels").get_json()["labels"]
    assert labels[0] == {"name": "memory", "count": 2}
    assert {l["name"] for l in labels} == {"memory", "learning", "draft"}


def test_note_pages_no_longer_load_every_label(client, app, monkeypatch):
    with app.app_context():
        note = dbmod.create_note("# A note #memory", "2026-01-01")

    def fail():
        raise AssertionError("a note page counted labels")
    monkeypatch.setattr(dbmod, "get_labels_with_counts", fail)
    for url in (f"/notes/{note}", f"/notes/{note}/edit", "/notes/new"):
        page = client.get(url).data.decode()
        assert "label-picker" not in page and "click to insert" not in page
