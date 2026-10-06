"""The graph, Trash and versions in the browser (spec §10, §4, §11.5)."""

import re

import pytest
from playwright.sync_api import expect

from app import db as dbmod

pytestmark = pytest.mark.browser


def test_graph_draws_and_hops_change_it(page, live):
    a = live.note("# A")
    b = live.note(f"# B\n\n[[{a}]]")
    live.note(f"# C\n\n[[{b}]] [[999]]")          # a link to a note that doesn't exist
    page.goto(f"/notes/{a}")
    page.click("#graph-btn")
    expect(page).to_have_url(f"{live.url}/graph/{a}")
    expect(page.locator("#graph-canvas canvas").first).to_be_attached()
    page.select_option("#hops-select", "3")
    expect(page).to_have_url(f"{live.url}/graph/{a}?hops=3")
    expect(page.locator("#graph-canvas canvas").first).to_be_attached()
    expect(page.locator("#graph-capped")).to_be_hidden()


def test_a_graph_past_the_cap_says_what_it_left_out(page, live):
    hub = live.note("# Hub")
    live.run(lambda: [dbmod.create_note(f"# Spoke {i}\n\n[[{hub}]]", "2026-01-01")
                      for i in range(dbmod.GRAPH_MAX_NODES + 5)])
    page.goto(f"/graph/{hub}")
    capped = page.locator("#graph-capped")
    expect(capped).to_be_visible(timeout=15000)
    expect(capped).to_have_text(re.compile(r"^Showing the nearest 300 notes; 6 more at that distance"))


def test_unconnected_note_has_a_greyed_graph_button(page, live):
    note = live.note("# Alone")
    page.goto(f"/notes/{note}")
    button = page.locator("#graph-btn")
    expect(button).to_have_attribute("aria-disabled", "true")
    button.click(force=True)
    expect(page).to_have_url(f"{live.url}/notes/{note}")


def test_trash_permanent_delete_and_empty_ask_first(page, live):
    ids = [live.note(f"# Gone {i}") for i in range(3)]
    live.run(lambda: [dbmod.soft_delete_note(i) for i in ids])
    page.goto("/trash")
    answers = [False, True, True]
    page.on("dialog", lambda d: d.accept() if answers.pop(0) else d.dismiss())
    row = page.locator(".trash-row", has_text="Gone 2")
    row.get_by_role("button", name="Delete permanently").click()
    expect(page.locator(".trash-row")).to_have_count(3)            # declined
    row.get_by_role("button", name="Delete permanently").click()
    expect(page.locator(".flash")).to_have_text(f"Note {ids[2]} deleted permanently.")
    expect(page.locator(".trash-row")).to_have_count(2)
    page.get_by_role("button", name="Empty Trash").click()
    expect(page.locator(".empty-state")).to_have_text("Trash is empty.")
    assert live.note_ids() == []


def test_restore_a_version_from_its_page(page, live, monkeypatch):
    from tests.test_activity import Clock
    clock = Clock()
    monkeypatch.setattr(dbmod, "now_iso", clock)
    note = live.note("# Draft\n\nfirst")
    clock.advance(60)
    live.run(lambda: dbmod.update_note(note, "# Draft\n\nsecond", "2026-01-01"))
    page.goto(f"/notes/{note}")
    page.get_by_role("link", name="Versions").click()
    page.locator(".version-row:not(.current) .version-when").click()
    expect(page.locator(".diff-remove")).to_contain_text("- first")
    expect(page.locator(".diff-add")).to_contain_text("+ second")
    page.get_by_role("button", name="Restore this version").click()
    expect(page).to_have_url(f"{live.url}/notes/{note}")
    expect(page.locator("#view-pane")).to_contain_text("first")
    assert live.body(note) == "# Draft\n\nfirst"
