"""The feed in the browser (spec §4, §6.4, §7): cards that expand in place,
the label chips, Jump to, and the search box on every page."""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def test_a_card_expands_in_place_and_collapses(page, live):
    target = live.note("# Target\n\nbody of target")
    note = live.note(f"# Card title\n\nthe full text, see [[{target}]]", "2026-02-01")
    page.goto("/")
    card = page.locator(f'.note-card[data-note-id="{note}"]')
    full = card.locator('[data-role="full"]')
    expect(full).to_be_hidden()
    card.locator(".snippet").click()
    expect(full).to_be_visible()
    expect(full).to_contain_text("the full text")
    expect(card.locator(".disclosure-btn")).to_have_attribute("aria-expanded", "true")
    card.locator(".disclosure-btn").click()
    expect(full).to_be_hidden()


def test_a_link_inside_a_card_navigates_instead_of_expanding(page, live):
    target = live.note("# Target", "2026-01-01")
    live.note(f"# Links to [[{target}]]", "2026-02-01")
    page.goto("/")
    page.locator(".snippet a.note-ref").first.click()
    expect(page).to_have_url(f"{live.url}/notes/{target}")


def test_search_box_on_a_note_page_searches_the_feed(page, live):
    note = live.note("# Spacing effect\n\nspaced repetition")
    live.note("# Other\n\nnothing")
    page.goto(f"/notes/{note}")
    page.fill(".top-search input", "repetition")
    page.press(".top-search input", "Enter")
    expect(page).to_have_url(re.compile(r"/\?q=repetition$"))
    expect(page.locator(".filter-banner")).to_contain_text("1 note matches")
    expect(page.locator(".search-match mark")).to_have_text("repetition")


@pytest.fixture
def many_labels(live):
    """40 plain labels plus a #physics-* namespace, used more as they go."""
    for i in range(40):
        for _ in range(i % 3 + 1):
            live.note(f"# n\n\n#topic{i:02d}")
    live.note("# p\n\n#physics #physics-quantum")
    live.note("# q\n\n#physics-optics")


def _visible_chips(page):
    return page.evaluate("""[...document.querySelectorAll('#label-chips .chip')]
        .filter(c => c.offsetParent).map(c => c.getAttribute('data-name') || c.textContent.trim())""")


def test_chips_are_capped_and_show_all_opens_the_rest(page, many_labels):
    page.goto("/")
    assert len(_visible_chips(page)) == 36
    page.click("#labels-show-all")
    expect(page.locator("#labels-show-all")).to_be_hidden()
    assert len(_visible_chips(page)) == 41          # 40 labels + the #physics-* group


def test_a_namespace_opens_in_place(page, many_labels):
    page.goto("/?q=%23physics-*")                  # pinned first, and open: it's searched
    caret = page.locator('.chip-caret[aria-controls="labels-physics"]')
    expect(caret).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#labels-physics")).to_contain_text("-quantum")
    caret.click()
    expect(page.locator("#labels-physics")).to_be_hidden()
    caret.click()
    expect(page.locator("#labels-physics .chip").first).to_have_text(re.compile(r"^#physics"))


def test_filter_box_narrows_and_restores(page, many_labels):
    page.goto("/")
    page.fill("#label-filter", "quant")
    expect(page.locator("#label-filter-count")).to_have_text("1 of 43 labels")
    # the group shows, opened to just its matching label
    assert _visible_chips(page) == ["#physics-*2", "physics-quantum"]
    expect(page.locator("#labels-show-all")).to_be_hidden()
    page.fill("#label-filter", "")
    assert len(_visible_chips(page)) == 36
    expect(page.locator("#labels-show-all")).to_be_visible()
    expect(page.locator("#labels-physics")).to_be_hidden()   # back as it was


def test_order_switch_is_remembered(page, many_labels):
    page.goto("/?q=%23topic05")
    page.click(".label-order >> text=A–Z")
    expect(page).to_have_url(re.compile(r"/\?q=%23topic05$"))   # back where it was set
    expect(page.locator(".label-order strong")).to_have_text("A–Z")
    chips = _visible_chips(page)
    # the searched label pinned first, then A–Z (#physics-* among the p's)
    assert chips[:4] == ["topic05", "#physics-*2", "topic00", "topic01"]
    expect(page.locator("#labels-show-all")).to_have_count(0)   # A–Z isn't capped
    page.goto("/")
    expect(page.locator(".label-order strong")).to_have_text("A–Z")


def test_jump_to_a_month_scrolls_to_its_day(page, live):
    for month in range(1, 11):
        for day in (3, 17):
            for _ in range(2):
                live.note(f"# {month}/{day}", f"2026-{month:02d}-{day:02d}")
    page.goto("/")
    page.fill("#jump-month", "2026-03")
    page.click(".jump-form button")
    expect(page).to_have_url(re.compile(r"#d-2026-03-17$"))
    heading = page.locator("#d-2026-03-17")
    expect(heading).to_be_in_viewport()
