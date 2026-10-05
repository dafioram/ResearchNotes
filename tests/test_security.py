"""Request guards (spec §14): the Host check, the cross-site check, and how
attachments are served."""

import hashlib
import io

import pytest

from app import create_app
from app import db as dbmod
from app import security


# ---------------------------------------------------------------------
# Host check
# ---------------------------------------------------------------------

@pytest.mark.parametrize("host", [
    "localhost", "localhost:5000", "127.0.0.1", "192.168.1.50:5000", "[::1]:5000",
    "myserver", "myserver:5000", "notes.lan", "box.local:5000", "nas.home.arpa",
    "notes.internal", "dev.localhost", "notes.lan.",
])
def test_local_addresses_are_allowed(host):
    assert security.host_allowed(host)


@pytest.mark.parametrize("host", ["evil.example.com", "notes.example.com:5000", "localhost.evil.com", ""])
def test_public_names_are_refused(host):
    assert not security.host_allowed(host)


def test_allowed_hosts_adds_names_and_suffixes():
    extra = security.parse_allowed_hosts(" Notes.Example.com , .mydomain.org,")
    assert extra == ("notes.example.com", ".mydomain.org")
    assert security.host_allowed("notes.example.com:5000", extra)
    assert security.host_allowed("a.mydomain.org", extra)
    assert not security.host_allowed("other.example.com", extra)


def test_requests_to_a_public_name_get_400(client):
    resp = client.get("/", base_url="http://evil.example.com")
    assert resp.status_code == 400
    assert b"ALLOWED_HOSTS" in resp.data


def test_requests_to_a_lan_address_work(client):
    assert client.get("/", base_url="http://192.168.1.50:5000").status_code == 200
    assert client.get("/", base_url="http://notes.lan").status_code == 200


def test_allowed_hosts_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLOWED_HOSTS", "notes.example.com")
    app = create_app({
        "TESTING": True,
        "DATABASE_PATH": str(tmp_path / "notes.db"),
        "UPLOAD_DIR": str(tmp_path / "uploads"),
    })
    assert app.test_client().get("/", base_url="http://notes.example.com").status_code == 200


# ---------------------------------------------------------------------
# Cross-site check
# ---------------------------------------------------------------------

NEW_NOTE = {"body": "# Hello", "sort_date": "2026-01-01"}


def _note_count(app):
    with app.app_context():
        return dbmod.get_db().execute("SELECT COUNT(*) FROM notes").fetchone()[0]


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "http://localhost:8080"},            # another app on the same machine
    {"Origin": "null"},                              # sandboxed frame, file:// page
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
])
def test_changes_from_other_sites_are_refused(client, app, headers):
    resp = client.post("/notes/new", data=NEW_NOTE, headers=headers)
    assert resp.status_code == 403
    assert _note_count(app) == 0


@pytest.mark.parametrize("headers", [
    {},                                              # no browser headers (curl, scripts)
    {"Origin": "http://localhost"},
    {"Origin": "http://localhost:80"},               # default port written out
    {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},                      # typed or bookmarked
])
def test_changes_from_the_app_itself_are_allowed(client, app, headers):
    resp = client.post("/notes/new", data=NEW_NOTE, headers=headers)
    assert resp.status_code == 302
    assert _note_count(app) == 1


def test_origin_must_match_the_address_used(client, app):
    lan = "http://192.168.1.50:5000"
    resp = client.post("/notes/new", data=NEW_NOTE, base_url=lan, headers={"Origin": lan})
    assert resp.status_code == 302
    resp = client.post("/notes/new", data=NEW_NOTE, base_url=lan, headers={"Origin": "http://192.168.1.50"})
    assert resp.status_code == 403


def test_cross_site_check_covers_every_changing_route(client, app):
    with app.app_context():
        note_id = dbmod.create_note("original text", "2026-01-01")
    evil = {"Origin": "http://evil.example"}
    for url in (f"/notes/{note_id}/edit", f"/notes/{note_id}/delete", f"/notes/{note_id}/restore",
                f"/notes/{note_id}/attachments/upload", f"/notes/{note_id}/attachments/1/remove"):
        assert client.post(url, data={"body": "", "sort_date": "2026-01-01"}, headers=evil).status_code == 403
    with app.app_context():
        assert dbmod.get_note(note_id)["body"] == "original text"


def test_reading_is_not_affected_by_origin(client):
    assert client.get("/", headers={"Origin": "http://evil.example"}).status_code == 200


# ---------------------------------------------------------------------
# Serving attachments
# ---------------------------------------------------------------------

def _serve(client, app, content, name, mime):
    with app.app_context():
        note_id = dbmod.create_note("n", "2026-01-01")
    client.post(
        f"/notes/{note_id}/attachments/upload",
        data={"file": (io.BytesIO(content), name, mime)},
        content_type="multipart/form-data",
    )
    return client.get(f"/files/{hashlib.sha256(content).hexdigest()}")


def test_html_attachments_download_instead_of_opening(client, app):
    resp = _serve(client, app, b"<script>alert(1)</script>", "page.html", "text/html")
    assert resp.status_code == 200
    assert resp.headers["Content-Disposition"].startswith("attachment")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Content-Security-Policy"] == "sandbox"


def test_svg_attachments_download_instead_of_opening(client, app):
    resp = _serve(client, app, b"<svg onload='alert(1)'/>", "fig.svg", "image/svg+xml")
    assert resp.headers["Content-Disposition"].startswith("attachment")


@pytest.mark.parametrize("name,mime", [
    ("fig.png", "image/png"), ("photo.jpg", "image/jpeg"), ("notes.txt", "text/plain"),
    ("clip.mp4", "video/mp4"),
])
def test_safe_types_open_in_the_browser_sandboxed(client, app, name, mime):
    resp = _serve(client, app, name.encode() * 3, name, mime)
    assert resp.headers["Content-Disposition"].startswith("inline")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Content-Security-Policy"] == "sandbox"


def test_pdfs_open_in_the_browser_without_a_sandbox(client, app):
    resp = _serve(client, app, b"%PDF-1.4 test", "paper.pdf", "application/pdf")
    assert resp.headers["Content-Disposition"].startswith("inline")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" not in resp.headers


def test_a_misdeclared_type_cannot_turn_into_a_page(client, app):
    # Bytes that are HTML but declared as an image: served as an image with
    # nosniff, so the browser won't treat them as a page.
    resp = _serve(client, app, b"<script>alert(1)</script>!", "fake.png", "image/png")
    assert resp.mimetype == "image/png"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
