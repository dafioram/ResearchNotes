"""The note page in the browser (spec §4.3): saving in place, Done, Cancel,
the View / Edit switch, autosave, drafts, the leave-page warning, Delete."""

import re

import pytest
from playwright.sync_api import expect

from app import db as dbmod

pytestmark = pytest.mark.browser


def _type_at_end(page, text):
    page.locator("#body").press("Control+End")
    page.locator("#body").type(text)


def test_first_save_turns_a_new_note_into_its_page(page, live):
    page.goto("/notes/new")
    expect(page.locator("#save-status")).to_have_text("")
    page.locator("#body").type("# Fresh idea\n\nfirst words")
    expect(page.locator("#save-status")).to_have_text("Unsaved changes")
    page.keyboard.press("Control+s")
    expect(page.locator("#save-status")).to_have_text(re.compile(r"^Saved \d"))
    [note_id] = live.note_ids()
    expect(page).to_have_url(f"{live.url}/notes/{note_id}/edit")
    expect(page.locator("#note-title")).to_have_text(f"No. {note_id}")
    expect(page.locator("#attachments-section")).to_be_attached()   # files can be added now
    expect(page.locator("#graph-btn")).to_be_attached()
    assert live.body(note_id) == "# Fresh idea\n\nfirst words"


def test_done_on_an_existing_note_saves_and_shows_view(page, live):
    note = live.note("# Title\n\nold text")
    page.goto(f"/notes/{note}/edit")
    _type_at_end(page, " and more")
    page.click("#done-btn")
    expect(page.locator("body")).to_have_class(re.compile(r"\bmode-view\b"))
    expect(page).to_have_url(f"{live.url}/notes/{note}")
    expect(page.locator("#view-pane")).to_contain_text("old text and more")
    assert live.body(note) == "# Title\n\nold text and more"


def test_done_on_a_new_note_lands_on_it_in_the_feed(page, live):
    for i in range(3):
        live.note(f"# Older {i}", "2025-01-01")
    page.goto("/notes/new")
    page.locator("#body").type("# Just written")
    page.click("#done-btn")
    card = page.locator(".note-card.just-added")       # scrolled to and highlighted
    expect(card).to_have_count(1)
    assert card.get_attribute("data-note-id") == str(max(live.note_ids()))
    expect(page).not_to_have_url(re.compile(r"focus="))   # dropped so a refresh doesn't repeat it


def test_flipping_to_view_saves_first(page, live):
    note = live.note("# T\n\nbody")
    page.goto(f"/notes/{note}/edit")
    _type_at_end(page, "!")
    page.click('.mode-switch label:has(input[value="view"])')
    expect(page.locator("body")).to_have_class(re.compile(r"\bmode-view\b"))
    assert live.body(note) == "# T\n\nbody!"


def test_a_cleared_date_is_refused_and_nothing_is_saved(page, live):
    note = live.note("# T\n\nbody", "2026-03-04")
    page.goto(f"/notes/{note}/edit")
    _type_at_end(page, " changed")
    page.fill("#sort_date", "")
    page.keyboard.press("Control+s")
    page.wait_for_timeout(300)
    assert live.body(note) == "# T\n\nbody"
    expect(page.locator("body")).to_have_class(re.compile(r"\bmode-edit\b"))


def test_cancel_without_changes_just_goes_to_view(page, live):
    note = live.note("# T")
    page.goto(f"/notes/{note}/edit")
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    page.click("#cancel-btn")
    expect(page.locator("body")).to_have_class(re.compile(r"\bmode-view\b"))
    assert dialogs == []


def test_cancel_undoes_changes_autosave_already_saved(page, live):
    note = live.note("# Original\n\ntext")
    page.goto(f"/notes/{note}/edit")
    _type_at_end(page, "\nautosaved line")
    expect(page.locator("#save-status")).to_have_text(re.compile(r"^Saved"), timeout=6000)
    assert live.body(note).endswith("autosaved line")
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    page.click("#cancel-btn")
    expect(page.locator("body")).to_have_class(re.compile(r"\bmode-view\b"))
    assert dialogs == ["Undo your changes since you started editing?"]
    assert live.body(note) == "# Original\n\ntext"


def test_declining_cancel_keeps_editing(page, live):
    note = live.note("# T")
    page.goto(f"/notes/{note}/edit")
    _type_at_end(page, " x")
    page.on("dialog", lambda d: d.dismiss())
    page.click("#cancel-btn")
    expect(page.locator("body")).to_have_class(re.compile(r"\bmode-edit\b"))
    expect(page.locator("#body")).to_have_value("# T x")


def test_cancelling_an_unsaved_new_note_creates_nothing(page, live):
    page.goto("/notes/new")
    page.locator("#body").type("# Never mind")
    page.on("dialog", lambda d: d.accept())
    page.click("#cancel-btn")
    expect(page).to_have_url(f"{live.url}/")
    assert live.note_ids() == []


def test_autosave_runs_only_once_a_note_exists(page, live):
    page.goto("/notes/new")
    page.locator("#body").type("# Not yet")
    page.wait_for_timeout(3600)
    assert live.note_ids() == []                       # a new note waits for Save
    page.keyboard.press("Control+s")
    expect(page.locator("#save-status")).to_have_text(re.compile(r"^Saved"))
    [note_id] = live.note_ids()
    _type_at_end(page, "\nthen typed on")
    page.wait_for_timeout(1000)
    assert live.body(note_id) == "# Not yet"           # not before the pause
    expect(page.locator("#save-status")).to_have_text(re.compile(r"^Saved"), timeout=6000)
    assert live.body(note_id) == "# Not yet\nthen typed on"


def test_a_draft_survives_a_closed_tab(page, live, browser):
    note = live.note("# Saved text")
    page.route(f"**/notes/{note}/edit", lambda r: r.abort() if r.request.method == "POST" else r.continue_())
    page.goto(f"/notes/{note}/edit")
    _type_at_end(page, "\nwritten while the app was unreachable")
    expect(page.locator("#save-status")).to_contain_text("Couldn’t reach the app", timeout=6000)
    context = page.context
    page.close(run_before_unload=False)

    again = context.new_page()
    again.goto(f"/notes/{note}")
    banner = again.locator(".draft-banner")
    expect(banner).to_contain_text("Unsaved changes to this note from")
    banner.get_by_role("button", name="Restore").click()
    expect(again.locator("body")).to_have_class(re.compile(r"\bmode-edit\b"))
    expect(again.locator("#body")).to_have_value("# Saved text\nwritten while the app was unreachable")
    expect(again.locator("#save-status")).to_have_text(re.compile(r"^Saved"), timeout=6000)
    assert live.body(note).endswith("unreachable")
    assert again.evaluate(f"localStorage.getItem('rn-draft:{note}')") is None


def test_a_new_notes_draft_can_be_discarded(page, live):
    page.goto("/notes/new")
    page.locator("#body").type("# Lost idea")
    page.wait_for_timeout(500)
    context = page.context
    page.close(run_before_unload=False)
    again = context.new_page()
    again.goto("/notes/new")
    expect(again.locator(".draft-banner")).to_contain_text("A new note you hadn’t saved")
    again.get_by_role("button", name="Discard").click()
    expect(again.locator(".draft-banner")).to_have_count(0)
    assert again.evaluate("localStorage.getItem('rn-draft:new')") is None


def test_leaving_with_unsaved_changes_asks_first(page, live):
    note = live.note("# T")
    page.goto(f"/notes/{note}/edit")
    _type_at_end(page, " unsaved")
    kinds = []
    page.on("dialog", lambda d: (kinds.append(d.type), d.dismiss()))
    page.close(run_before_unload=True)
    page.wait_for_timeout(300)
    assert kinds == ["beforeunload"]


def test_delete_asks_and_moves_the_note_to_trash(page, live):
    note = live.note("# Doomed")
    page.goto(f"/notes/{note}")
    answers = [False, True]
    page.on("dialog", lambda d: d.accept() if answers.pop(0) else d.dismiss())
    page.click("#delete-form button")
    expect(page).to_have_url(f"{live.url}/notes/{note}")      # declined: still here
    page.click("#delete-form button")
    expect(page).to_have_url(f"{live.url}/")
    assert live.run(lambda: dbmod.get_note(note)) is None
