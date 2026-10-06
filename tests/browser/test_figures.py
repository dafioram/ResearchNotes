"""Images in the browser (spec §9.5): a dropped or pasted image goes into
the text at the cursor once uploaded; other files are only attached;
Insert and Copy put an attached file's text in; View shows the figure."""

import re

import pytest
from playwright.sync_api import expect

from app import db as dbmod

pytestmark = pytest.mark.browser

PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

# Files as [name, type, base64 or text]; events dispatched on the window
# (a drop) or the editor (a paste, with any `text` also on the clipboard).
DROP = """([files, kind, text]) => {
    const dt = new DataTransfer();
    if (text) dt.setData('text/plain', text);
    for (const [name, type, data] of files) {
        const bytes = type === 'image/png' ? Uint8Array.from(atob(data), c => c.charCodeAt(0)) : data;
        dt.items.add(new File([bytes], name, {type}));
    }
    if (kind === 'paste') {
        const el = document.getElementById('body');
        el.focus();
        el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
    } else {
        for (const type of ['dragenter', 'dragover', 'drop'])
            window.dispatchEvent(new DragEvent(type, {dataTransfer: dt, bubbles: true, cancelable: true}));
    }
}"""

FIGURE = re.compile(r"!\[([^\]]*)\]\(/files/([0-9a-f]{12})\)")


def _editing(page, live, body):
    note = live.note(body)
    page.goto(f"/notes/{note}/edit")
    return note


def _caret_after(page, text):
    page.evaluate("""(text) => {
        const el = document.getElementById('body');
        el.focus();
        const at = el.value.indexOf(text) + text.length;
        el.setSelectionRange(at, at);
    }""", text)


def test_a_dropped_image_goes_in_at_the_cursor_and_shows_in_view(page, live):
    note = _editing(page, live, "# Map\n\nbefore\nafter")
    _caret_after(page, "before")
    page.evaluate(DROP, [[["gull-rock_v4.png", "image/png", PNG_B64]], "drop"])
    expect(page.locator("#body")).to_have_value(re.compile(r"^# Map\n\nbefore\n!\[gull rock v4\]\(/files/[0-9a-f]{12}\)\nafter$"))
    expect(page.locator("#attachments-panel")).to_contain_text("gull-rock_v4.png")
    page.click("#done-btn")
    figure = page.locator("#view-pane figure")
    expect(figure.locator("figcaption")).to_have_text("gull rock v4")
    assert figure.locator("img").evaluate("img => img.complete && img.naturalWidth") == 1
    assert "![gull rock v4](/files/" in live.body(note)


def test_a_pasted_screenshot_is_named_by_when_and_captioned_figure(page, live):
    note = _editing(page, live, "# Shot")
    page.locator("#body").press("Control+End")
    page.evaluate(DROP, [[["image.png", "image/png", PNG_B64]], "paste"])
    expect(page.locator("#body")).to_have_value(re.compile(r"^# Shot\n!\[figure\]\(/files/[0-9a-f]{12}\)\n$"))
    [attached] = live.run(lambda: dbmod.list_note_attachments(note))
    assert re.fullmatch(r"pasted-\d{4}-\d\d-\d\d-\d{4}\.png", attached["filename"])


def test_a_paste_with_text_too_is_text(page, live):
    # A spreadsheet copies cells as text and a picture of them: the text wins.
    note = _editing(page, live, "")
    page.evaluate(DROP, [[["cells.png", "image/png", PNG_B64]], "paste", "a\tb\n1\t2"])
    expect(page.locator("#body")).to_have_value("|   a |   b |\n|----:|----:|\n|   1 |   2 |")
    assert live.run(lambda: dbmod.list_note_attachments(note)) == []


def test_only_images_go_into_the_text(page, live):
    note = _editing(page, live, "# Mixed")
    page.locator("#body").press("Control+End")
    page.evaluate(DROP, [[["notes.txt", "text/plain", "hello"], ["fig.png", "image/png", PNG_B64]], "drop"])
    expect(page.locator("#attachment-status")).to_have_text("Attached 2 files.")
    value = page.locator("#body").input_value()
    assert len(FIGURE.findall(value)) == 1 and "notes.txt" not in value and "Uploading" not in value
    assert len(live.run(lambda: dbmod.list_note_attachments(note))) == 2


def test_dropping_an_image_on_a_new_note_saves_it_first(page, live):
    page.goto("/notes/new")
    page.evaluate(DROP, [[["first.png", "image/png", PNG_B64]], "drop"])
    expect(page.locator("#attachment-status")).to_have_text("Attached first.png.")
    expect(page.locator("#body")).to_have_value(re.compile(r"^!\[first\]\(/files/[0-9a-f]{12}\)\n$"))
    [note] = live.note_ids()
    assert len(live.run(lambda: dbmod.list_note_attachments(note))) == 1


def test_insert_puts_an_attached_file_at_the_cursor(page, live):
    note = _editing(page, live, "# Doc\n\nend")
    page.evaluate(DROP, [[["paper.txt", "text/plain", "x"]], "drop"])
    expect(page.locator("#attachments-panel")).to_contain_text("paper.txt")
    _caret_after(page, "# Doc")
    page.locator("#attachments-panel button[data-insert]").click()
    expect(page.locator("#body")).to_have_value(re.compile(r"^# Doc\n\[paper\.txt\]\(/files/[0-9a-f]{12}\)\n\nend$"))


def test_copy_puts_the_text_on_the_clipboard(page, live):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    note = _editing(page, live, "# Copy me")
    page.evaluate(DROP, [[["chart.png", "image/png", PNG_B64]], "drop"])
    expect(page.locator("#body")).to_have_value(re.compile(r"/files/"))
    page.goto(f"/notes/{note}")
    button = page.locator("#view-pane button[data-copy]")
    button.click()
    expect(button).to_have_text("Copied")
    assert re.fullmatch(r"!\[chart\]\(/files/[0-9a-f]{12}\)", page.evaluate("navigator.clipboard.readText()"))
