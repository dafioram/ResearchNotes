"""Math in the browser (spec §5): KaTeX draws the TeX the renderer left,
on a note's page, in a feed card opened in place, and after a save."""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser

BODY = "# Curve $R = e^{-t/S}$\n\nfit $\\lambda$ here\n\n$$\n\\sum_{i=1}^{n} x_i\n$$\n\nprices: $5 and $10"


def test_math_is_drawn_on_a_notes_page(page, live):
    note = live.note(BODY)
    page.goto(f"/notes/{note}")
    view = page.locator("#view-pane")
    expect(view.locator(".math .katex")).to_have_count(3)
    expect(view.locator(".math-display .katex-display")).to_have_count(1)
    expect(view.locator(".math-inline").first).to_have_attribute("title", "$R = e^{-t/S}$")
    expect(view).to_contain_text("prices: $5 and $10")


def test_bad_tex_is_shown_not_thrown(page, live):
    note = live.note("# T\n\nbroken $\\frac{1}{$ and $\\nosuchcommand$")
    page.goto(f"/notes/{note}")
    view = page.locator("#view-pane")
    expect(view.locator(".math")).to_have_count(2)
    expect(view.locator(".katex-error")).to_have_count(1)            # the unclosed \frac: its TeX, in red
    expect(view.locator(".katex-error")).to_contain_text("\\frac{1}{")
    expect(view.locator(".math").nth(1)).to_contain_text("\\nosuchcommand")   # drawn, in red


def test_math_is_drawn_in_an_opened_feed_card(page, live):
    note = live.note(BODY)
    page.goto("/")
    card = page.locator(f'.note-card[data-note-id="{note}"]')
    card.locator(".snippet").click()
    expect(card.locator('[data-role="full"] .math-display .katex')).to_have_count(1)


def test_math_is_drawn_after_a_save(page, live):
    note = live.note("# T\n\nbefore")
    page.goto(f"/notes/{note}/edit")
    page.locator("#body").press("Control+End")
    page.locator("#body").type(" and $x^2$")
    page.click("#done-btn")
    expect(page.locator("body")).to_have_class(re.compile(r"\bmode-view\b"))
    expect(page.locator("#view-pane .math .katex")).to_have_count(1)


def test_math_is_drawn_in_backlink_passages(page, live):
    target = live.note("# Target")
    live.note(f"# Citing\n\nthe model $p = 2^{{-\\Delta/h}}$ from [[{target}]]")
    page.goto(f"/notes/{target}")
    expect(page.locator(".backlink-context .math .katex")).to_have_count(1)
