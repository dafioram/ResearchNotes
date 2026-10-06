"""
Browser tests: the app served on a free port with a fresh database, driven
by Chromium through Playwright, for what only exists in the browser --
saving in place, autosave and drafts, the [[ and # suggestions, dropping
files, the feed's cards and label chips, the graph, confirmations.

Run with `pytest -m browser` (plain `pytest` leaves them out). They skip
themselves when Playwright or a Chromium build isn't installed; set
CHROMIUM_PATH to use an existing Chromium. Any JavaScript error on a page
fails the test that caused it.
"""

import os
import threading

import pytest
from werkzeug.serving import make_server

from app import db as dbmod

CHROMIUM_FALLBACK = "/opt/pw-browsers/chromium"


@pytest.fixture(scope="session")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright isn't installed (pip install -r requirements-dev.txt)")
    with sync_playwright() as p:
        launched = None
        for options in ({}, {"executable_path": os.environ.get("CHROMIUM_PATH") or CHROMIUM_FALLBACK}):
            try:
                launched = p.chromium.launch(**options)
                break
            except Exception:
                continue
        if launched is None:
            pytest.skip("no Chromium for Playwright (playwright install chromium, or set CHROMIUM_PATH)")
        yield launched
        launched.close()


class Live:
    """The running app, with direct database access for setting up and
    checking -- the server runs in this process, on the same file."""

    def __init__(self, app, url):
        self.app, self.url = app, url

    def note(self, body, date="2026-01-01"):
        with self.app.app_context():
            return dbmod.create_note(body, date)

    def body(self, note_id):
        with self.app.app_context():
            row = dbmod.get_note(note_id, include_deleted=True)
            return row["body"] if row else None

    def note_ids(self):
        with self.app.app_context():
            return [r[0] for r in dbmod.get_db().execute("SELECT id FROM notes ORDER BY id")]

    def run(self, fn):
        """Call fn() inside the app (for any other db.* call)."""
        with self.app.app_context():
            return fn()


@pytest.fixture
def live(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Live(app, f"http://127.0.0.1:{server.server_port}")
    server.shutdown()
    thread.join()


@pytest.fixture
def page(browser, live):
    context = browser.new_context(base_url=live.url, viewport={"width": 1440, "height": 900})
    pg = context.new_page()
    errors = []
    pg.on("pageerror", lambda error: errors.append(str(error)))
    yield pg
    context.close()
    assert not errors, f"JavaScript errors: {errors}"
