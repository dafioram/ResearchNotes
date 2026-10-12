"""Hover previews in the browser (spec §6.2): resting on a [[link]] shows
the linked note beside it; it goes on leaving, on Escape; links to notes
that don't exist show nothing."""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def test_hovering_a_link_previews_the_note(page, live):
    target = live.note("# Spacing effect\n\nRepetition over time, $R = e^{-t/S}$.")
    note = live.note(f"# Citing\n\nsee [[{target}]] and [[999]]")
    page.goto(f"/notes/{note}")
    preview = page.locator(".note-preview")
    page.hover(f'#view-pane a.note-ref[href="/notes/{target}"]')
    expect(preview).to_be_visible()
    expect(preview).to_contain_text("Repetition over time")
    expect(preview.locator(".math .katex")).to_have_count(1)        # math drawn in the preview too
    page.mouse.move(5, 5)                                           # away from link and card
    expect(preview).to_have_count(0)


def test_the_card_stays_while_the_pointer_is_on_it_and_escape_closes_it(page, live):
    target = live.note("# Target\n\n" + "\n\n".join(f"line {i}" for i in range(40)))
    note = live.note(f"# Citing\n\nsee [[{target}]]")
    page.goto(f"/notes/{note}")
    page.hover(f'#view-pane a.note-ref[href="/notes/{target}"]')
    preview = page.locator(".note-preview")
    expect(preview).to_be_visible()
    preview.hover()
    page.wait_for_timeout(500)
    expect(preview).to_be_visible()
    page.keyboard.press("Escape")
    expect(preview).to_have_count(0)


def test_a_link_to_a_missing_note_has_no_preview(page, live):
    note = live.note("# Citing\n\nsee [[999]]")
    page.goto(f"/notes/{note}")
    page.hover("#view-pane .note-ref-ghost")
    page.wait_for_timeout(700)
    expect(page.locator(".note-preview")).to_have_count(0)
