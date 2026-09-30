import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import markdown as md


# ---------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------

def test_label_requires_no_space():
    assert md.extract_labels("this is #research today") == {"research"}


def test_header_requires_space_and_is_not_a_label():
    assert md.extract_labels("# Heading here") == set()


def test_double_hash_no_space_is_nothing():
    # "##word" is neither a header (no space) nor a label (preceded by '#')
    assert md.extract_labels("##word") == set()
    html = md.render("##word")
    assert "label-tag" not in html
    assert "<h" not in html
    assert "##word" in html


def test_double_hash_with_space_is_h2_header():
    html = md.render("## Section Title")
    assert "<h2>Section Title</h2>" in html


def test_label_can_appear_anywhere_inline():
    assert md.extract_labels("some text #alpha then #beta more") == {"alpha", "beta"}


def test_labels_are_case_insensitive_on_extraction():
    assert md.extract_labels("#Research and #research") == {"research"}


def test_label_not_extracted_from_code_span():
    assert md.extract_labels("use `#define X` in C") == set()


def test_label_not_extracted_from_fenced_code_block():
    body = "before\n```python\n#comment not a label\n```\nafter"
    assert md.extract_labels(body) == set()


def test_label_renders_as_link_to_feed_filter():
    html = md.render("hello #zettelkasten world")
    assert '<a class="label-tag" href="/?label=zettelkasten">#zettelkasten</a>' in html


# ---------------------------------------------------------------------
# Note references
# ---------------------------------------------------------------------

def test_note_ref_extraction():
    assert md.extract_note_refs("see [[42]] and [[7]]") == {42, 7}


def test_note_ref_renders_as_link_when_note_exists():
    html = md.render("see [[42]]", existing_ids={42})
    assert '<a class="note-ref" href="/notes/42">[[42]]</a>' in html


def test_note_ref_renders_as_ghost_when_missing():
    html = md.render("see [[999]]", existing_ids={42})
    assert "note-ref-ghost" in html
    assert "[[999]]" in html
    assert 'href="/notes/999"' not in html


def test_note_ref_not_extracted_from_code():
    assert md.extract_note_refs("`[[42]]`") == set()


# ---------------------------------------------------------------------
# Standard subset rendering
# ---------------------------------------------------------------------

def test_bold_and_italic():
    html = md.render("**bold** and *italic* and __also bold__ and _also italic_")
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<strong>also bold</strong>" in html
    assert "<em>also italic</em>" in html


def test_strikethrough():
    assert "<del>gone</del>" in md.render("~~gone~~")


def test_inline_code_escaped():
    html = md.render("`<script>`")
    assert "<code>&lt;script&gt;</code>" in html


def test_fenced_code_block():
    html = md.render("```python\nprint('hi')\n```")
    assert "<pre>" in html
    assert "language-python" in html
    assert "print(&#x27;hi&#x27;)" in html or "print('hi')" in html


def test_headers_all_levels():
    for level in range(1, 7):
        hashes = "#" * level
        html = md.render(f"{hashes} Title {level}")
        assert f"<h{level}>Title {level}</h{level}>" in html


def test_links():
    html = md.render("[Anthropic](https://anthropic.com)")
    assert '<a href="https://anthropic.com" rel="noopener">Anthropic</a>' in html


def test_unordered_list():
    html = md.render("- one\n- two\n- three")
    assert html == "<ul><li>one</li><li>two</li><li>three</li></ul>"


def test_ordered_list():
    html = md.render("1. one\n2. two")
    assert html == "<ol><li>one</li><li>two</li></ol>"


def test_blockquote():
    html = md.render("> a wise quote")
    assert "<blockquote>a wise quote</blockquote>" in html


def test_horizontal_rule():
    for hr in ["---", "***", "___"]:
        assert "<hr>" in md.render(hr)


def test_paragraph_with_soft_break():
    html = md.render("line one\nline two")
    assert html == "<p>line one<br>line two</p>"


def test_two_paragraphs_separated_by_blank_line():
    html = md.render("para one\n\npara two")
    assert html == "<p>para one</p>\n<p>para two</p>"


def test_raw_html_is_escaped_not_executed():
    html = md.render("<script>alert(1)</script>")
    # '<' is always escaped so no tag can ever open from note content.
    assert "<script>" not in html
    assert "&lt;script" in html


def test_no_image_syntax_support():
    # image markdown is intentionally NOT a supported feature; it should
    # just render as literal (escaped) text, not an <img> tag.
    html = md.render("![alt text](https://example.com/pic.png)")
    assert "<img" not in html


# ---------------------------------------------------------------------
# Line count
# ---------------------------------------------------------------------

def test_line_count_skips_blank_lines():
    body = "line one\n\n\nline two\n   \nline three"
    assert md.line_count(body) == 3


def test_line_count_empty_body():
    assert md.line_count("") == 0
    assert md.line_count("   \n\n  ") == 0


# ---------------------------------------------------------------------
# Feed snippet (first line only)
# ---------------------------------------------------------------------

def test_render_first_line_uses_header_if_present():
    body = "# My Research Topic\n\nSome body text with #alabel and [[3]]."
    html = md.render_first_line(body, existing_ids={3})
    assert html == "<h1>My Research Topic</h1>"


def test_render_first_line_skips_leading_blank_lines():
    body = "\n\n  \nActual first line here"
    assert md.render_first_line(body) == "<p>Actual first line here</p>"


def test_render_first_line_empty_body():
    assert md.render_first_line("") == ""


# ---------------------------------------------------------------------
# Backlink context (ref_contexts)
# ---------------------------------------------------------------------

def _ctx(body, target):
    return [c["before"] + c["ref"] + c["after"] for c in md.ref_contexts(body, target)]


def test_ref_context_is_the_passage_around_the_link():
    body = "# Title\n\nSome text before [[5]] and after."
    [c] = md.ref_contexts(body, 5)
    assert c["before"] == "Some text before "
    assert c["ref"] == "[[5]]"
    assert c["after"] == " and after."
    assert not c["clipped_before"] and not c["clipped_after"]


def test_ref_context_skips_mention_in_title_line():
    assert md.ref_contexts("# About [[5]]\nmore text", 5) == []
    assert md.ref_contexts("single line with [[5]]", 5) == []


def test_ref_context_strips_markdown_and_ignores_code():
    body = "# T\nWe saw **big** effect in [[5]], see `code [[5]]`."
    assert _ctx(body, 5) == ["We saw big effect in [[5]], see code [[5]]."]


def test_ref_context_ignores_refs_inside_fenced_code():
    body = "# T\n```\n[[5]] in code\n```\nafter block [[5]] here"
    assert _ctx(body, 5) == ["after block [[5]] here"]


def test_ref_context_one_entry_per_mention():
    body = "# T\n\nA [[5]] b.\n\nC [[5]] d [[5]] e."
    assert len(md.ref_contexts(body, 5)) == 3


def test_ref_context_uses_the_list_item_not_the_whole_list():
    body = "# T\n- first [[5]] item\n- second item"
    assert _ctx(body, 5) == ["first [[5]] item"]


def test_ref_context_only_highlights_the_target():
    [c] = md.ref_contexts("# T\n[[4]] and [[5]] both #tag", 5)
    assert c["before"] == "[[4]] and "
    assert c["after"] == " both #tag"


def test_ref_context_is_clipped_to_a_window_around_the_link():
    body = "# T\n" + "word " * 80 + "[[5]]" + " tail" * 80
    [c] = md.ref_contexts(body, 5, width=160)
    assert c["clipped_before"] and c["clipped_after"]
    assert len(c["before"]) + len(c["ref"]) + len(c["after"]) <= 160
    assert not c["before"].startswith(("ord", "rd", "d ")) # starts on a word


def test_ref_context_gives_unused_budget_to_the_other_side():
    body = "# T\nShort [[5]] " + "long tail words " * 20
    [c] = md.ref_contexts(body, 5, width=160)
    assert c["before"] == "Short "
    assert len(c["after"]) > 100  # got the before-side's leftover budget


def test_ref_context_text_is_plain_not_html():
    [c] = md.ref_contexts("# T\nsee [site](http://x.com) & <b>[[5]]</b>", 5)
    assert c["before"] == "see site & <b>"
    assert c["after"] == "</b>"
