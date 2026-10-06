"""Tables (spec §5.2): pipe tables with a header and separator row,
alignment, inline markdown in cells, and labels and links in cells stored
the same way they're shown."""

import pytest

from app import db as dbmod
from app import markdown as md

TABLE = "| Model | Acc | Notes |\n|:--|--:|:-:|\n| Base | 0.81 | see [[12]] |\n| +aug | **0.86** | $p<1$ |"


def test_a_table_with_alignment_and_inline_markdown():
    html = md.render(TABLE, {12: "Twelve"})
    assert html.startswith('<table class="note-table"><thead><tr><th class="align-left">Model</th>'
                           '<th class="align-right">Acc</th><th class="align-center">Notes</th></tr></thead><tbody>')
    assert '<td class="align-right"><strong>0.86</strong></td>' in html
    assert ">Twelve</a></td>" in html and '<span class="math math-inline">$p&lt;1$</span>' in html
    assert html.count("<tr>") == 3 and html.endswith("</tbody></table>")


@pytest.mark.parametrize("text", [
    "A | B\n--- | ---\n1 | 2",            # outer pipes are optional
    "| A | B |\n|---|---|\n| 1 | 2 |",
    "|A|B|\n|-|-|\n|1|2|",
])
def test_outer_pipes_and_spacing_are_optional(text):
    assert md.render(text) == ('<table class="note-table"><thead><tr><th>A</th><th>B</th></tr></thead>'
                               "<tbody><tr><td>1</td><td>2</td></tr></tbody></table>")


@pytest.mark.parametrize("text", [
    "a | b\nc | d",                       # no separator row
    "| A | B |\n|---|",                   # separator with the wrong number of cells
    "| A | B |\n|---|abc|",               # not a separator
    "A\n---",                             # a rule, not a table
    "x | y",
])
def test_not_a_table(text):
    assert "<table" not in md.render(text)


def test_rows_are_padded_or_cut_to_the_header():
    html = md.render("| A | B |\n|---|---|\n| 1 |\n| 1 | 2 | 3 |")
    assert "<tr><td>1</td><td></td></tr>" in html and "<tr><td>1</td><td>2</td></tr>" in html


def test_pipes_in_code_math_and_escapes_dont_split_cells():
    html = md.render("| A | B |\n|---|---|\n| `a|b` | x\\|y $a|b$ |")
    assert "<td><code>a|b</code></td>" in html
    assert '<td>x|y <span class="math math-inline">$a|b$</span></td>' in html


def test_a_table_ends_at_a_blank_line_or_another_block_and_ends_a_paragraph():
    html = md.render("Intro\n| A | B |\n|---|---|\n| 1 | 2 |\n# Next\n\n| C | D |\n|---|---|\n\nafter")
    assert html.startswith("<p>Intro</p>\n<table")
    assert "</table>\n<h1>Next</h1>" in html
    assert '<table class="note-table"><thead><tr><th>C</th><th>D</th></tr></thead></table>\n<p>after</p>' in html


@pytest.mark.parametrize("text,labels", [
    ("|#tag|x|\n|-|-|", {"tag"}),
    ("| a |#tag|\n|---|---|", {"tag"}),
    ("a|#tag in prose", {"tag"}),         # the same rule outside tables
])
def test_a_label_right_after_a_pipe_is_shown_and_stored(text, labels):
    assert md.extract_labels(text) == labels
    assert 'class="label-tag" href="/?q=%23tag"' in md.render(text)


def test_a_link_in_a_table_shows_its_row_as_the_backlink_passage():
    body = "# T\n\n| Model | Notes |\n|---|---|\n| Base | see [[5]] here |\n| Aug | none |"
    [c] = md.ref_contexts(body, 5)
    assert (c["before"], c["after"]) == ("Base | see ", " here")


def test_plain_text_of_a_table_separates_its_cells():
    assert md._to_plain(md.render("| A | B |\n|---|---|\n| 1 | 2 |")) == "A B 1 2"


def test_labels_and_links_in_cells_are_stored(client, app):
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        nid = dbmod.create_note(f"# Results\n\n| Run | Notes |\n|---|---|\n| 1 |#baseline see [[{target}]]|",
                                "2026-01-02")
        assert [r["name"] for r in dbmod.get_labels_with_counts()] == ["baseline"]
        assert [b["id"] for b in dbmod.get_backlinks(target)] == [nid]
    page = client.get(f"/notes/{nid}").data.decode()
    assert '<table class="note-table">' in page and "#baseline</a>" in page
