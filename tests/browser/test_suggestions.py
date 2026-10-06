"""Suggestions while typing (spec §6.1, §6.2): [[ lists notes to link to,
# lists labels in use."""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def _options(page):
    popup = page.locator("#ref-popup")
    expect(popup).to_be_visible()
    return [re.sub(r"\s+", " ", t).strip() for t in popup.locator(".ref-option").all_inner_texts()]


@pytest.fixture
def notes(live):
    ids = {
        "spacing": live.note("# Spacing effect\n\n#memory #learning", "2025-01-01"),
        "retrieval": live.note("# Retrieval practice\n\nwith spacing too #memory", "2025-02-01"),
        "flash": live.note("# Flashcards\n\n#machine-learning", "2025-03-01"),
    }
    return ids


def test_double_bracket_lists_the_latest_notes_then_narrows(page, notes):
    page.goto("/notes/new")
    page.locator("#body").type("see [[")
    options = _options(page)
    assert options[0].startswith(f"No. {notes['flash']} Flashcards")      # latest first
    assert options[-1] == "later Link later: [[later]]"                   # always last
    page.locator("#body").type("spac")
    expect(page.locator("#ref-popup .ref-option").first).to_contain_text("Spacing effect")  # title match first
    assert _options(page)[1].startswith(f"No. {notes['retrieval']}")
    assert _options(page)[-1] == "later Link later: [[later: spac]]"


def test_enter_inserts_the_link_and_ctrl_z_undoes_it(page, notes):
    page.goto("/notes/new")
    box = page.locator("#body")
    box.type("see [[spac")
    expect(page.locator("#ref-popup .ref-option").first).to_contain_text("Spacing effect")
    box.press("Enter")
    expect(box).to_have_value(f"see [[{notes['spacing']}]]")
    expect(page.locator("#ref-popup")).to_be_hidden()
    box.press("Control+z")
    expect(box).to_have_value("see [[spac")


def test_arrows_tab_and_an_existing_closing_bracket(page, notes):
    page.goto("/notes/new")
    box = page.locator("#body")
    box.type("x []]")
    box.press("ArrowLeft")
    box.press("ArrowLeft")      # caret between [ and ]]
    box.type("[spac")
    expect(page.locator("#ref-popup .ref-option").first).to_contain_text("Spacing effect")
    box.press("ArrowDown")
    box.press("Tab")
    expect(box).to_have_value(f"x [[{notes['retrieval']}]]")   # the ]] already there is reused


def test_link_later_with_a_hint(page, notes):
    page.goto("/notes/new")
    box = page.locator("#body")
    box.type("cf. [[Bjork 1994")
    expect(page.locator("#ref-popup")).to_contain_text("No notes match")
    box.press("Enter")          # the only choice left: link later
    expect(box).to_have_value("cf. [[later: Bjork 1994]]")


def test_escape_closes_until_the_next_double_bracket(page, notes):
    page.goto("/notes/new")
    box = page.locator("#body")
    box.type("a [[sp")
    expect(page.locator("#ref-popup")).to_be_visible()
    box.press("Escape")
    expect(page.locator("#ref-popup")).to_be_hidden()
    box.type("a")
    page.wait_for_timeout(300)
    expect(page.locator("#ref-popup")).to_be_hidden()
    box.type("]] b [[")
    expect(page.locator("#ref-popup")).to_be_visible()


def test_the_note_being_edited_is_not_offered(page, notes):
    page.goto(f"/notes/{notes['spacing']}/edit")
    page.locator("#body").press("Control+End")
    page.locator("#body").type(" [[Spacing")
    page.wait_for_timeout(400)
    assert not any(o.startswith(f"No. {notes['spacing']} ") for o in _options(page))


def test_hash_suggests_labels_in_use(page, notes):
    page.goto("/notes/new")
    box = page.locator("#body")
    box.type("about #lea")
    options = _options(page)
    assert options[:2] == ["#learning 1 note", "#machine-learning 1 note"]   # starts-with, then contains
    box.press("Tab")
    expect(box).to_have_value("about #learning ")


def test_enter_on_a_label_typed_in_full_is_a_new_line(page, notes):
    page.goto("/notes/new")
    box = page.locator("#body")
    box.type("#memory")
    expect(page.locator("#ref-popup")).to_be_visible()
    box.press("Enter")
    expect(box).to_have_value("#memory\n")


@pytest.mark.parametrize("typed", ["# Title", "page#mem", "#brandnew"])
def test_no_label_list_where_it_isnt_a_known_label(page, notes, typed):
    page.goto("/notes/new")
    page.locator("#body").type(typed)
    page.wait_for_timeout(300)
    expect(page.locator("#ref-popup")).to_be_hidden()
