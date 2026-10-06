"""Attaching files in the browser (spec §9.3): drop anywhere while editing,
in place, without touching unsaved text; removing in place."""

import re

import pytest
from playwright.sync_api import expect

from app import db as dbmod

pytestmark = pytest.mark.browser

DROP = """([files, events]) => {
    const dt = new DataTransfer();
    for (const [name, text] of files) dt.items.add(new File([text], name, {type: 'text/plain'}));
    for (const type of events)
        window.dispatchEvent(new DragEvent(type, {dataTransfer: dt, bubbles: true, cancelable: true}));
}"""


def _drop(page, files, events=("dragenter", "dragover", "drop")):
    page.evaluate(DROP, [files, list(events)])


def test_dropped_files_attach_in_place_and_keep_unsaved_text(page, live):
    note = live.note("# With files")
    page.goto(f"/notes/{note}/edit")
    page.locator("#body").press("Control+End")
    page.locator("#body").type("\nnot saved yet")
    _drop(page, [("a.txt", "first"), ("b.txt", "second")])
    expect(page.locator("#attachment-status")).to_have_text("Attached 2 files.")
    expect(page.locator("#attachments-panel .attachment-list")).to_contain_text("a.txt")
    expect(page.locator("#attachments-panel .attachment-list")).to_contain_text("b.txt")
    expect(page.locator("#body")).to_have_value("# With files\nnot saved yet")   # untouched
    expect(page.locator("#save-status")).to_have_text("Unsaved changes")
    assert live.run(lambda: len(dbmod.list_note_attachments(note))) == 2


def test_dropping_in_view_explains_instead_of_attaching(page, live):
    note = live.note("# Read only now")
    page.goto(f"/notes/{note}")
    _drop(page, [("x.txt", "x")], events=("dragenter",))
    overlay = page.locator(".drop-overlay")
    expect(overlay).to_be_visible()
    expect(overlay).to_contain_text("Switch to Edit to attach files")
    expect(overlay).to_have_class(re.compile(r"\brefuse\b"))
    _drop(page, [("x.txt", "x")], events=("drop",))
    page.wait_for_timeout(300)
    assert live.run(lambda: dbmod.list_note_attachments(note)) == []


def test_a_new_note_must_be_saved_before_files(page, live):
    page.goto("/notes/new")
    _drop(page, [("x.txt", "x")], events=("dragenter",))
    expect(page.locator(".drop-overlay")).to_contain_text("Save the note first")


def test_remove_happens_in_place(page, live):
    note = live.note("# Has a file")
    page.goto(f"/notes/{note}/edit")
    _drop(page, [("gone.txt", "bye")])
    expect(page.locator("#attachments-panel .attachment-list")).to_contain_text("gone.txt")
    page.locator("#body").press("Control+End")
    page.locator("#body").type(" typing")
    page.locator("#attachments-panel .attachment-row button").click()
    expect(page.locator("#attachment-status")).to_contain_text("Removed gone.txt")
    expect(page.locator("#attachments-panel .attachment-list")).to_have_count(0)
    expect(page.locator("#body")).to_have_value("# Has a file typing")
    assert live.run(lambda: dbmod.list_note_attachments(note)) == []
