"""Tables in the browser (spec §5.2): pasting cells from a spreadsheet or
comma-separated data makes a pipe table; prose and code stay as pasted;
Ctrl+Z gives back exactly what was pasted."""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser

PASTE = """(text) => {
  const el = document.getElementById("body");
  el.focus();
  const data = new DataTransfer();
  data.setData("text/plain", text);
  const e = new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true });
  return el.dispatchEvent(e);   // false: the page turned it into a table
}"""


def _editor_at_end(page, live, body):
    note = live.note(body)
    page.goto(f"/notes/{note}/edit")
    page.locator("#body").press("Control+End")
    return note


def test_spreadsheet_cells_become_a_table(page, live):
    _editor_at_end(page, live, "# Results\n\nRuns:")
    assert page.evaluate(PASTE, "Model\tAccuracy\tNotes\r\nBase\t0.81\tfirst\r\n+aug\t0.86\t\"two\nlines\"\r\n") is False
    assert page.locator("#body").input_value() == (
        "# Results\n\nRuns:\n"
        "| Model | Accuracy | Notes     |\n"
        "|-------|---------:|-----------|\n"
        "| Base  |     0.81 | first     |\n"
        "| +aug  |     0.86 | two lines |"
    )


def test_comma_separated_data_becomes_a_table_with_quotes_and_pipes(page, live):
    _editor_at_end(page, live, "")
    assert page.evaluate(PASTE, 'name,score\n"Smith, J.",3\nx|y,4') is False
    assert page.locator("#body").input_value() == (
        "| name      | score |\n"
        "|-----------|------:|\n"
        "| Smith, J. |     3 |\n"
        "| x\\|y      |     4 |"
    )


@pytest.mark.parametrize("text", [
    "Hello, world\nThanks, Bob",          # prose: a space after the comma
    "one line\twith a tab",                # a single line
    "\tindented()\n\tcode()",              # tab-indented code: an empty header cell
    "a,b\nc,d,e",                          # rows of different widths
])
def test_other_pastes_are_left_alone(page, live, text):
    _editor_at_end(page, live, "x")
    assert page.evaluate(PASTE, text) is True
    assert page.locator("#body").input_value() == "x"


def test_no_table_inside_a_code_block(page, live):
    _editor_at_end(page, live, "```\n")
    assert page.evaluate(PASTE, "a\tb\nc\td") is True


def test_the_table_sits_on_its_own_lines_and_ctrl_z_gives_back_the_paste(page, live):
    _editor_at_end(page, live, "before")
    page.locator("#body").press("Home")       # caret in the middle of the line: "|before"
    page.locator("#body").press("ArrowRight")
    page.locator("#body").press("ArrowRight")
    page.locator("#body").press("ArrowRight")
    assert page.evaluate(PASTE, "a\tb\n1\t2") is False
    assert page.locator("#body").input_value() == "bef\n|   a |   b |\n|----:|----:|\n|   1 |   2 |\n\nore"
    page.locator("#body").press("Control+z")
    assert page.locator("#body").input_value() == "befa\tb\n1\t2ore"


def test_a_table_shows_in_view(page, live):
    note = live.note("# T\n\n| A | B |\n|---|--:|\n| 1 | 2 |")
    page.goto(f"/notes/{note}")
    table = page.locator("#view-pane table.note-table")
    expect(table.locator("th")).to_have_text(["A", "B"])
    expect(table.locator("td.align-right")).to_have_text("2")
