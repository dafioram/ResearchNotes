"""The combined note page (View / Edit), in-place saving, landing on a new
note in the feed, and graph access."""

import io
import re

from app import db as dbmod

FETCH = {"X-Requested-With": "fetch"}


def _create(app, body="# A note", date="2026-01-01"):
    with app.app_context():
        return dbmod.create_note(body, date)


# ---------------------------------------------------------------------
# One page, two modes
# ---------------------------------------------------------------------

def test_view_and_edit_urls_open_the_same_page_in_each_mode(client, app):
    note_id = _create(app, "# Title\nbody")
    view = client.get(f"/notes/{note_id}").data.decode()
    edit = client.get(f"/notes/{note_id}/edit").data.decode()
    assert '<body class="mode-view">' in view
    assert '<body class="mode-edit">' in edit
    for page in (view, edit):
        # both panes and the switch are always on the page; CSS shows one
        assert 'id="view-pane"' in page and 'id="body"' in page
        assert 'class="mode-switch not-new"' in page
    assert 'value="view" checked' in view and 'value="edit" checked' in edit


def test_new_note_page_is_edit_mode_without_switch_or_note_actions(client):
    body = client.get("/notes/new").data.decode()
    assert '<body class="mode-edit is-new">' in body
    assert 'data-note-id=""' in body and 'data-started-new="true"' in body
    assert 'id="graph-btn"' not in body and 'id="delete-form"' not in body
    assert "Save the note once to attach files to it." in body


def test_links_to_notes_open_in_view_mode(client, app):
    target = _create(app, "# Target")
    source = _create(app, f"# Source\nsee [[{target}]]")
    page = client.get(f"/notes/{source}").data.decode()
    assert f'href="/notes/{target}"' in page                     # [[link]] in the body
    backlinks = client.get(f"/notes/{target}").data.decode()
    assert f'class="backlink-row" href="/notes/{source}"' in backlinks
    assert f'href="/notes/{source}"' in client.get("/history").data.decode()


# ---------------------------------------------------------------------
# Saving in place
# ---------------------------------------------------------------------

def test_inplace_save_returns_fresh_view(client, app):
    note_id = _create(app, "# Old title")
    resp = client.post(f"/notes/{note_id}/edit",
                       data={"body": "# New title\nsecond line", "sort_date": "2026-02-02"}, headers=FETCH)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "<h1>New title</h1>" in data["view_html"]
    assert data["sort_date"] == "2026-02-02" and data["line_count"] == 2
    assert data["saved_at"].endswith(("AM", "PM"))
    assert "update_url" not in data  # only the creating save sends page pieces


def test_inplace_save_rejects_bad_date_without_saving(client, app):
    note_id = _create(app, "# Keep me")
    resp = client.post(f"/notes/{note_id}/edit", data={"body": "changed", "sort_date": ""}, headers=FETCH)
    assert resp.status_code == 400
    assert resp.get_json() == {"ok": False, "message": "Sort date must be a valid date (YYYY-MM-DD)."}
    with app.app_context():
        assert dbmod.get_note(note_id)["body"] == "# Keep me"


def test_first_save_of_new_note_creates_it_and_sends_page_pieces(client, app):
    resp = client.post("/notes/new", data={"body": "# Fresh", "sort_date": "2026-03-03"}, headers=FETCH)
    data = resp.get_json()
    note_id = data["note_id"]
    assert data["update_url"] == f"/notes/{note_id}/edit"
    assert data["edit_url"] == f"/notes/{note_id}/edit"
    assert data["view_url"] == f"/notes/{note_id}"
    assert 'id="attachment-upload"' in data["attachments_html"]
    assert f'action="/notes/{note_id}/attachments/upload"' in data["attachments_html"]
    assert 'id="graph-btn"' in data["common_html"] and 'id="delete-form"' in data["common_html"]
    assert data["feed_url"] == f"/?focus={note_id}"
    # and saving again updates the same note instead of creating another
    client.post(data["update_url"], data={"body": "# Fresh\nmore", "sort_date": "2026-03-03"}, headers=FETCH)
    with app.app_context():
        assert dbmod.get_db().execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
        assert dbmod.get_note(note_id)["body"] == "# Fresh\nmore"


def test_attachment_change_sends_fresh_view_too(client, app):
    note_id = _create(app, "# With file")
    data = client.post(f"/notes/{note_id}/attachments/upload",
                       data={"file": (io.BytesIO(b"x"), "fig.png")},
                       content_type="multipart/form-data", headers=FETCH).get_json()
    assert "fig.png" in data["view_html"]


def test_pages_are_never_cached_but_static_files_can_be(client, app):
    note_id = _create(app)
    for url in ["/", f"/notes/{note_id}", f"/notes/{note_id}/edit", "/history"]:
        assert client.get(url).headers["Cache-Control"] == "no-store", url
    assert client.get("/static/style.css").headers.get("Cache-Control") != "no-store"


# ---------------------------------------------------------------------
# Landing on a new note in the feed
# ---------------------------------------------------------------------

def test_feed_url_points_at_the_page_the_note_is_on(client, app):
    app.config["PAGE_SIZE"] = 2
    for day in range(10, 16):                       # six notes, newest 2026-01-15
        _create(app, f"# n{day}", f"2026-01-{day}")
    data = client.post("/notes/new", data={"body": "# Old one", "sort_date": "2026-01-01"},
                       headers=FETCH).get_json()
    # six newer notes ahead of it, two per page -> fourth page
    assert data["feed_url"] == f"/?page=4&focus={data['note_id']}"
    page = client.get(data["feed_url"]).data.decode()
    assert f'data-note-id="{data["note_id"]}"' in page
    assert f'data-focus="{data["note_id"]}"' in page


def test_feed_without_focus_has_empty_focus(client):
    assert 'data-focus=""' in client.get("/").data.decode()


# ---------------------------------------------------------------------
# Graph: per note only, unavailable without connections
# ---------------------------------------------------------------------

def test_graph_button_disabled_for_unconnected_note(client, app):
    note_id = _create(app, "# Alone")
    page = client.get(f"/notes/{note_id}").data.decode()
    assert 'id="graph-btn" class="btn-quiet disabled"' in page
    assert 'aria-disabled="true" title="No connections yet' in page
    assert not re.search(rf'(?<![\w-])href="/graph/{note_id}"', page)  # data-href is fine
    assert f'data-href="/graph/{note_id}"' in page                      # used to re-enable it


def test_graph_button_enabled_for_linking_and_linked_notes(client, app):
    a = _create(app, "# A")
    b = _create(app, f"# B\n[[{a}]]")
    broken = _create(app, "# Broken\n[[999]]")     # links out to a missing note: still a graph
    for note_id in (a, b, broken):
        page = client.get(f"/notes/{note_id}").data.decode()
        assert f'href="/graph/{note_id}"' in page, note_id
        assert 'class="btn-quiet disabled"' not in page, note_id


def test_links_from_trashed_notes_dont_count_as_connections(client, app):
    a = _create(app, "# A")
    b = _create(app, f"# B\n[[{a}]]")
    client.post(f"/notes/{b}/delete")
    assert 'class="btn-quiet disabled"' in client.get(f"/notes/{a}").data.decode()
    assert f'data-note-id="{a}"' in client.get("/orphans").data.decode()  # same rule


def test_save_reports_connection_changes(client, app):
    a = _create(app, "# A")
    b = _create(app, "# B")
    post = lambda body: client.post(f"/notes/{b}/edit", data={"body": body, "sort_date": "2026-01-01"},
                                    headers=FETCH).get_json()
    assert post(f"# B\n[[{a}]]")["has_connections"] is True
    assert post("# B\nno links")["has_connections"] is False


def test_graph_page_for_unconnected_note_explains_instead_of_drawing(client, app):
    note_id = _create(app, "# Alone")
    page = client.get(f"/graph/{note_id}").data.decode()
    assert "has no connections yet" in page
    assert 'id="graph-canvas"' not in page and "cytoscape" not in page
    assert f'href="/notes/{note_id}"' in page       # back to the note


def test_graph_api_404_for_missing_note(client):
    assert client.get("/api/graph/12345").status_code == 404
