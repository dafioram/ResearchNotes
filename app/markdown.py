"""
A small, hand-rolled Markdown subset renderer for research notes.

This is deliberately NOT a full CommonMark implementation. It supports just
enough syntax for note-taking, plus two note-specific extensions:

    #label          -> a clickable tag. The '#' starts a line or follows
                        whitespace; then a letter (any language), then
                        letters, digits, '.', '-' or '_'. Trailing '.', '-'
                        and '_' aren't part of it ("#label." is #label).
                        "# Title" (with a space) is a header; '##label'
                        is neither -- it is left as plain text.
    [[123]]         -> a reference to note #123. Renders as a link if note
                        123 exists (and is not deleted) -- showing its
                        title when the caller passes titles -- otherwise as
                        a "ghost" reference so broken links are visible at
                        a glance.
    $x^2$           -> math (TeX), drawn by KaTeX in the browser: $...$
    $$...$$             inline, $$...$$ a display block (may span lines).
                        Prices stay text: "$5 and $10" isn't math (spec
                        §5.1). \\$ is a literal dollar sign.
    [[later]]       -> a link to fill in later, optionally with a hint:
    [[later: hint]]     [[later: Bjork 1994]]. Any capitalization. Shown as
                        a placeholder; it links nowhere, so it makes no
                        backlink or graph node.

Supported standard Markdown subset:
    # .. ######      ATX headers (must have a space after the hashes)
    **bold** / __bold__
    *italic* / _italic_
    ~~strikethrough~~
    `inline code`
    ```lang\n code \n```   fenced code blocks
    [text](url)       links (http, https, mailto or relative; any other
                      scheme, e.g. javascript:, stays plain text)
    https://...       a bare http(s) address becomes a link
    - item / * item    unordered lists
    1. item            ordered lists
    > quote            blockquotes
    ---  ***  ___      horizontal rules
    | a | b |          tables: a header row, then a |---|---| row (colons
    |---|--:|          align: :--- left, ---: right, :---: centered), then
    | 1 | 2 |          rows; \\| is a literal pipe in a cell
    blank-line-separated paragraphs, single '\n' -> <br>

Raw HTML is never passed through -- everything is escaped first, so the
supported syntax above is genuinely the entire vocabulary available.
Images (spec §9.5): ![caption](/files/<hash>) shows an attached image --
the first 12 or more characters of a stored file's hash; nothing from
other sites. Alone on its line it's a figure, the caption below it.
Math is passed to the browser as escaped TeX for KaTeX (trust off, so no
\\href or \\html... commands) -- the server never renders it.
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote
from dataclasses import dataclass, field

from markupsafe import Markup, escape

# ---------------------------------------------------------------------------
# Extraction (used both for metadata syncing at save time, and for rendering)
# ---------------------------------------------------------------------------

CODE_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_+-]*)\n?(.*?)```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
# Math (spec §5.1): $$display$$ (may span lines), then $inline$ -- pandoc's
# rules, so prices aren't math: no space just inside the dollars, and the
# closing one not followed by a digit ("$5 and $10" stays text), and the
# opening one not straight after a letter or digit (an address with "$" in
# it isn't math). \$ is a plain dollar sign. Never across a stashed code
# placeholder (\x00).
MATH_BLOCK_RE = re.compile(r"(?<!\\)\$\$(?=[^$])((?:(?!\$\$)[^\x00])*?\S(?:(?!\$\$)[^\x00])*?)\$\$")
MATH_INLINE_RE = re.compile(r"(?<![\w\\$])\$(?=[^\s$])([^$\n\x00]*?[^\s\\$])\$(?![\d$])")

# A label (spec §6.1): '#' at the start of a line or after whitespace --
# so URL fragments (page#part), C# and foo#bar aren't labels -- then a
# letter in any language ([^\W\d_]), so "#3" and "PR #42" aren't either,
# then letters, digits, '.', '-' or '_', ending on a letter or digit so
# that "#label." or "#label-" is just #label. "# Title" has a space after
# the '#', so stays a header; in '##word' neither '#' qualifies. And not
# where it runs into "://": "#http://x.org" is the address (which becomes
# a link), not a label "#http" that the note wouldn't show.
_LABEL_BODY = r"#([^\W\d_](?:[\w.-]*[^\W_])?)(?![\w.-]*://)"
# A '|' counts as the gap before a label too ("a|#b"), as a table cell
# written without spaces, "|#tag|", reads.
LABEL_RE = re.compile(r"(?:(?<!\S)|(?<=\|))" + _LABEL_BODY)
NOTE_REF_RE = re.compile(r"\[\[(\d+)\]\]")
# [[later]] / [[later: a hint]] -- a link to fill in later (spec §6.2).
LATER_RE = re.compile(r"\[\[\s*later\s*(?::\s*([^\[\]\n]*?)\s*)?\]\]", re.IGNORECASE)
# An image (spec §9.5): one of this app's stored files, by the first 12 or
# more characters of its hash -- never an address elsewhere.
IMAGE_RE = re.compile(r"!\[([^\]\n]*)\]\(/files/([0-9a-fA-F]{12,64})\)")
# Any link to a stored file, image or not: what a note refers to.
_FILE_REF_RE = re.compile(r"\]\(/files/([0-9a-fA-F]{12,64})\)")

_PLACEHOLDER_TMPL = "\x00{kind}{idx}\x00"
_PLACEHOLDER_RE = re.compile(r"\x00([A-Z]+)(\d+)\x00")


@dataclass
class _Stash:
    """Collects raw fragments (code, links, refs) pulled out of the text so
    later passes can't accidentally reparse markdown syntax inside them."""

    items: dict = field(default_factory=dict)
    counter: int = 0
    labels: set = field(default_factory=set)  # every label rendered, lowercased
    files: dict | None = None  # hash prefix -> stored file, for ![](/files/...) (render's `files`)

    def store(self, kind: str, value):
        key = _PLACEHOLDER_TMPL.format(kind=kind, idx=self.counter)
        self.items[key] = value
        self.counter += 1
        return key


def _strip_code(text: str, stash: _Stash) -> str:
    """Pull fenced code blocks, inline code spans and math out into the
    stash, replacing them with placeholder tokens. Must run on RAW text so
    that labels/refs typed inside code or math are never extracted or
    linkified. Math is stored as typed, dollars and all."""

    def _block(m: re.Match) -> str:
        lang, code = m.group(1), m.group(2)
        return stash.store("CODEBLOCK", (lang, code))

    text = CODE_BLOCK_RE.sub(_block, text)

    def _span(m: re.Match) -> str:
        return stash.store("CODESPAN", m.group(1))

    text = INLINE_CODE_RE.sub(_span, text)
    text = MATH_BLOCK_RE.sub(lambda m: stash.store("MATHBLOCK", m.group(0)), text)
    text = MATH_INLINE_RE.sub(lambda m: stash.store("MATHSPAN", m.group(0)), text)
    return text


def extract_labels(body: str) -> set[str]:
    """Return the set of distinct label names (lowercased) used in a note:
    the labels rendering it shows, collected as it renders. So what's
    stored is exactly what shows, by construction -- nothing in code or
    math, in a link or address, in a [[later: hint]]; a label in a table
    cell or a quote is one (spec §6.1)."""
    return _render_body(body, set())[1].labels


def extract_note_refs(body: str) -> set[int]:
    """Return the set of distinct note ids referenced via [[id]] in a note,
    ignoring anything inside code blocks/spans."""
    stash = _Stash()
    stripped = _strip_code(body, stash)
    return {int(m.group(1)) for m in NOTE_REF_RE.finditer(stripped)}


def extract_file_refs(body: str) -> set[str]:
    """The stored files a note points at -- ![...](/files/<hash>) or
    [...](/files/<hash>), outside code and math -- as the (lowercased) hash
    prefixes typed. Saving a note attaches them to it (spec §9.5)."""
    stripped = _strip_code(body, _Stash())
    return {m.group(1).lower() for m in _FILE_REF_RE.finditer(stripped)}


def has_later(body: str) -> bool:
    """Whether a note has a [[later]] placeholder outside code."""
    return bool(LATER_RE.search(_strip_code(body, _Stash())))


def line_count(body: str) -> int:
    """Number of non-blank lines in a note body."""
    return sum(1 for line in body.splitlines() if line.strip())


# ---------------------------------------------------------------------------
# Inline rendering
# ---------------------------------------------------------------------------

# Underscores only emphasise at word boundaries (as in CommonMark), so
# max_batch_size or results_2024_final.csv stay as typed; asterisks work
# anywhere. (?<!\w) = "not after a letter, digit or underscore", any language.
BOLD_RE = re.compile(r"\*\*(.+?)\*\*|(?<!\w)__(.+?)__(?!\w)", re.DOTALL)
ITALIC_RE = re.compile(r"\*(.+?)\*|(?<!\w)_(.+?)_(?!\w)", re.DOTALL)
STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")

# Only these schemes become clickable; javascript:, data: and the rest stay
# as the text that was typed.
SAFE_SCHEMES = frozenset({"http", "https", "mailto"})
_SCHEME_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*):")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def safe_href(url: str) -> str | None:
    """`url` (as typed) if it's fine to link to -- http(s), mailto, or an
    address with no scheme (relative, #fragment, //host) -- else None.
    Anything containing control characters is refused too: browsers strip
    them, so they could hide a scheme from this check."""
    if _CONTROL_RE.search(url):
        return None
    m = _SCHEME_RE.match(url)
    if m and m.group(1).lower() not in SAFE_SCHEMES:
        return None
    return url


# A bare http(s) address in running text becomes a link. Runs on escaped
# text, so it stops at an escaped "<" too.
AUTOLINK_RE = re.compile(r"(?<![\w/])https?://(?:(?!&lt;)[^\s\"\x00])+", re.IGNORECASE)
_URL_TRAILING = ".,:;!?'\"*_~>"


def _autolink(m: re.Match, stash: _Stash) -> str:
    """Link a bare address, leaving out what belongs to the sentence:
    trailing punctuation, and a ")" that doesn't close a "(" inside the
    address -- so "(see https://x.org/a)" links https://x.org/a, while
    https://en.wikipedia.org/wiki/Foo_(bar) keeps its brackets."""
    url = html.unescape(m.group(0))
    end = len(url)
    while end:
        c = url[end - 1]
        if c in _URL_TRAILING or (c == ")" and url[:end].count(")") > url[:end].count("(")):
            end -= 1
        else:
            break
    url, rest = url[:end], url[end:]
    if safe_href(url) is None or len(url) <= len("https://"):
        return m.group(0)
    link = stash.store(
        "LINK", f'<a href="{html.escape(url, quote=True)}" rel="noopener">{html.escape(url, quote=False)}</a>'
    )
    return link + _escape_text(rest)


# Longer titles are cut in a [[ref]]; the full title is in its tooltip.
REF_TITLE_MAX = 80


def _ref_html(note_id: int, existing) -> str:
    """A [[ref]]: a link if the note exists (in `existing`), shown by its
    title when `existing` maps ids to titles; else a ghost."""
    if note_id not in existing:
        return f'<span class="note-ref note-ref-ghost" title="Note {note_id} does not exist">[[{note_id}]]</span>'
    title = existing.get(note_id) if isinstance(existing, dict) else None
    if not title:
        return f'<a class="note-ref" href="/notes/{note_id}">[[{note_id}]]</a>'
    shown = _shown_title(title)
    return (
        f'<a class="note-ref titled" href="/notes/{note_id}" data-id="{note_id}" '
        f'title="No. {note_id}: {html.escape(title, quote=True)}">{html.escape(shown, quote=False)}</a>'
    )


def _shown_title(title: str) -> str:
    return title if len(title) <= REF_TITLE_MAX else title[:REF_TITLE_MAX - 1].rstrip() + "\u2026"


def refs_as_titles(text: str, titles: dict) -> Markup:
    """Plain text -- a title line, a backlink passage, a History line -- as
    HTML with each [[id]] of a note in `titles` (id -> title) shown as its
    title with the number small after it, like a [[ref]] in a rendered
    note, but as text: these sit inside links of their own. Refs to notes
    that don't exist (or have no title) stay "[[id]]"."""
    parts, pos = [], 0
    for m in NOTE_REF_RE.finditer(text):
        parts.append(escape(text[pos:m.start()]))
        note_id, title = int(m.group(1)), titles.get(int(m.group(1)))
        parts.append(
            Markup('<span class="ref-title" title="No. {0}: {1}">{2}<span class="ref-no">{0}</span></span>')
            .format(note_id, title, _shown_title(title))
            if title else escape(m.group(0))
        )
        pos = m.end()
    parts.append(escape(text[pos:]))
    return Markup("").join(parts)


def _later_html(m: re.Match) -> str:
    # The text arrives escaped already, hint included.
    hint = _PLACEHOLDER_RE.sub("", m.group(1) or "").strip()  # code in a hint: dropped
    tip = "A link to fill in later" + (f": {hint}" if hint else "")
    shown = f"[[later: {hint}]]" if hint else "[[later]]"
    return f'<span class="note-ref note-ref-later" title="{tip.replace(chr(34), "&quot;")}">{shown}</span>'


def _image_html(m: re.Match, files, figure: bool = False) -> str:
    """An image reference (on escaped text) as HTML. `files` maps hash
    prefixes to stored files ({"hash", "filename", "image"}); None when
    the caller didn't look them up (plain-text uses), which gives a plain
    link. A stored image shows (a figure, captioned, when `figure`); any
    other stored file is a link to it; a prefix that matches no file, or
    more than one, is shown as typed, marked missing."""
    alt, prefix = m.group(1), m.group(2).lower()
    caption = html.unescape(alt).strip()
    if files is None:
        return f'<a class="file-link" href="/files/{prefix}">{alt or "file"}</a>'
    found = files.get(prefix)
    if not found:
        why = "No stored file starts with" if found is None else "More than one stored file starts with"
        return f'<span class="file-missing" title="{why} {prefix}">{m.group(0)}</span>'
    url = f'/files/{found["hash"]}'
    if found["image"] and not found.get("present", True):
        # Stored, but its file isn't on disk (uploads/ not copied along):
        # say so, rather than a broken picture.
        name = html.escape(found["filename"], quote=True)
        return (f'<span class="file-missing" title="{name} isn\'t in the uploads folder">'
                f'Missing image: {alt or html.escape(found["filename"])}</span>')
    if not found["image"]:
        return f'<a class="file-link" href="{url}">{alt or html.escape(found["filename"])}</a>'
    img = (f'<a class="figure-link" href="{url}"><img src="{url}" '
           f'alt="{html.escape(caption, quote=True)}" loading="lazy"></a>')
    if figure:
        return f"<figure>{img}" + (f"<figcaption>{alt}</figcaption>" if caption else "") + "</figure>"
    return img


def _render_inline(text: str, stash: _Stash, existing_ids) -> str:
    """Render inline markdown (bold/italic/strike/links/labels/note-refs)
    on text that has ALREADY been HTML-escaped and had code spans stashed.
    Links, note-refs and labels are stashed first so the bold/italic passes
    can't reach into a URL, a ref or a label. `existing_ids` holds the
    referenced notes that exist: a set, or a dict of id -> title."""

    text = text.replace("\\$", "$")  # an escaped dollar sign (math is stashed already)

    # First, so a hint is shown as typed: [[later: https://...]] isn't a link.
    text = LATER_RE.sub(lambda m: stash.store("REF", _later_html(m)), text)

    # Before links, which would otherwise take the "[caption](...)" part.
    text = IMAGE_RE.sub(lambda m: stash.store("LINK", _image_html(m, stash.files)), text)

    def _link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        # The text arrives escaped; check the URL as typed, then escape it
        # once for the attribute (escaping the escaped text turned & into
        # &amp;amp; and broke query strings).
        href = safe_href(html.unescape(url))
        if href is None:
            return stash.store("LINK", m.group(0))  # shown as typed, not a link
        return stash.store(
            "LINK", f'<a href="{html.escape(href, quote=True)}" rel="noopener">{label}</a>'
        )

    text = LINK_RE.sub(_link, text)
    text = AUTOLINK_RE.sub(lambda m: _autolink(m, stash), text)

    text = NOTE_REF_RE.sub(lambda m: stash.store("REF", _ref_html(int(m.group(1)), existing_ids)), text)

    # Labels are set aside before bold/italic too, so emphasis can't reach
    # into one ("#snake_case_" would otherwise lose "_case_" to italics),
    # and they're recorded here: extract_labels() stores what this shows.
    def _label(m: re.Match) -> str:
        name = m.group(1)
        stash.labels.add(name.lower())
        return stash.store(
            "LABELTAG",
            f'<a class="label-tag" href="/?q={quote("#" + name.lower())}">#{html.escape(name)}</a>',
        )

    text = LABEL_RE.sub(_label, text)

    text = BOLD_RE.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = STRIKE_RE.sub(lambda m: f"<del>{m.group(1)}</del>", text)
    text = ITALIC_RE.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)

    return text


def _resolve_placeholders(text: str, stash: _Stash) -> str:
    """Repeatedly substitute placeholder tokens back to their stored HTML
    until none remain (code spans/blocks resolve to escaped HTML fragments
    that contain no further placeholders, so this terminates)."""

    def _sub(m: re.Match) -> str:
        key = m.group(0)
        value = stash.items.get(key)
        if value is None:
            return key
        if key.startswith("\x00MATH"):
            # The TeX as typed, for KaTeX to render in the browser (and to
            # read as-is without it, or as plain text).
            kind = "display" if key.startswith("\x00MATHBLOCK") else "inline"
            return f'<span class="math math-{kind}">{html.escape(value, quote=False)}</span>'
        if isinstance(value, tuple):  # CODEBLOCK -> (lang, code)
            lang, code = value
            lang_class = f' class="language-{html.escape(lang)}"' if lang else ""
            return f"<pre><code{lang_class}>{html.escape(code)}</code></pre>"
        if key.startswith("\x00CODESPAN"):
            return f"<code>{html.escape(value)}</code>"
        return value  # already-rendered HTML fragment (link/ref/label)

    prev = None
    while prev != text:
        prev = text
        text = _PLACEHOLDER_RE.sub(_sub, text)
    return text


# ---------------------------------------------------------------------------
# Block-level rendering
# ---------------------------------------------------------------------------

HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
HR_RE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$")
UL_RE = re.compile(r"^\s*[-*]\s+(.*)$")
OL_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
BLANK_RE = re.compile(r"^\s*$")
# A table (spec §5.2): a header row, a separator row of dashes (colons for
# alignment) with as many cells, then rows until a blank line or another
# block. Cells split on '|' that isn't escaped; code and math are already
# stashed, so a '|' inside them never splits.
_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")
_SEPARATOR_CELL_RE = re.compile(r"^\s*(:?)-+(:?)\s*$")


def _table_cells(line: str) -> list[str]:
    """A table row's cells: outer pipes dropped, split on unescaped '|',
    each trimmed, with \\| left as a plain '|'."""
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [c.strip().replace("\\|", "|") for c in _CELL_SPLIT_RE.split(row)]


def _table_alignments(line: str):
    """The separator row's alignments ('left', 'right', 'center' or ''),
    or None if `line` isn't a separator row."""
    if "-" not in line or not _CELL_SPLIT_RE.search(line):
        return None
    aligns = []
    for cell in _table_cells(line):
        m = _SEPARATOR_CELL_RE.match(cell)
        if not m:
            return None
        left, right = m.group(1), m.group(2)
        aligns.append("center" if left and right else "right" if right else "left" if left else "")
    return aligns


def _table_starts(lines: list[str], i: int):
    """The alignments if a table starts at lines[i] (a header row with a
    '|' followed by a separator row with as many cells), else None."""
    if i + 1 >= len(lines) or not _CELL_SPLIT_RE.search(lines[i]):
        return None
    aligns = _table_alignments(lines[i + 1])
    if aligns is None or len(aligns) != len(_table_cells(lines[i])):
        return None
    return aligns


def _starts_block(lines: list[str], i: int) -> bool:
    """Whether lines[i] starts a block other than a paragraph (so ends one)."""
    line = lines[i]
    return bool(
        HEADER_RE.match(line)
        or HR_RE.match(line)
        or BLOCKQUOTE_RE.match(line)
        or UL_RE.match(line)
        or OL_RE.match(line)
        or line.strip().startswith("\x00CODEBLOCK")
        or _table_starts(lines, i) is not None
    )


def _render_lines(lines: list[str], stash: _Stash, existing_ids) -> list[str]:
    """Turn a list of (already HTML-escaped, code-stashed) lines into a list
    of block-level HTML strings."""
    out: list[str] = []
    i = 0
    n = len(lines)

    def inline(s: str) -> str:
        return _render_inline(s, stash, existing_ids)

    while i < n:
        line = lines[i]

        # A stashed fenced code block sits alone on its own line.
        m_code = _PLACEHOLDER_RE.fullmatch(line.strip())
        if m_code and line.strip().startswith("\x00CODEBLOCK"):
            out.append(line.strip())
            i += 1
            continue

        if BLANK_RE.match(line):
            i += 1
            continue

        m = HEADER_RE.match(line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{inline(m.group(2).strip())}</h{level}>")
            i += 1
            continue

        if HR_RE.match(line):
            out.append("<hr>")
            i += 1
            continue

        aligns = _table_starts(lines, i)
        if aligns is not None:
            def row_html(cells, tag):
                # A short row is padded to the header's width. A long one
                # keeps its extra cells: cutting them would hide labels
                # and links that are still stored from the text.
                cells = cells + [""] * (len(aligns) - len(cells))
                cell_aligns = aligns + [""] * (len(cells) - len(aligns))
                return "<tr>" + "".join(
                    f'<{tag}{f" class={chr(34)}align-{a}{chr(34)}" if a else ""}>{inline(c)}</{tag}>'
                    for c, a in zip(cells, cell_aligns)) + "</tr>"

            head = row_html(_table_cells(line), "th")
            i += 2
            body = []
            while i < n and not BLANK_RE.match(lines[i]) and not _starts_block(lines, i):
                body.append(row_html(_table_cells(lines[i]), "td"))
                i += 1
            out.append('<table class="note-table"><thead>' + head + "</thead>"
                       + ("<tbody>" + "".join(body) + "</tbody>" if body else "") + "</table>")
            continue

        if BLOCKQUOTE_RE.match(line):
            buf = []
            while i < n and BLOCKQUOTE_RE.match(lines[i]):
                buf.append(BLOCKQUOTE_RE.match(lines[i]).group(1))
                i += 1
            out.append(f"<blockquote>{inline(' '.join(buf))}</blockquote>")
            continue

        if UL_RE.match(line):
            items = []
            while i < n and UL_RE.match(lines[i]):
                items.append(f"<li>{inline(UL_RE.match(lines[i]).group(1))}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue

        if OL_RE.match(line):
            items = []
            while i < n and OL_RE.match(lines[i]):
                items.append(f"<li>{inline(OL_RE.match(lines[i]).group(1))}</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue

        # Paragraph: consume until a blank line or a line that starts a new
        # block type.
        buf = [line]
        i += 1
        while i < n and not BLANK_RE.match(lines[i]) and not _starts_block(lines, i):
            buf.append(lines[i])
            i += 1
        # A line that's just an image is a figure, its caption below it;
        # the lines around it stay paragraphs.
        para: list[str] = []
        for b in buf:
            m = IMAGE_RE.fullmatch(b.strip())
            found = (stash.files or {}).get(m.group(2).lower()) if m else None
            if found and found["image"] and found.get("present", True):
                if para:
                    out.append("<p>" + "<br>".join(inline(x) for x in para) + "</p>")
                    para = []
                out.append(stash.store("LINK", _image_html(m, stash.files, figure=True)))
            else:
                para.append(b)
        if para:
            out.append("<p>" + "<br>".join(inline(x) for x in para) + "</p>")

    return out


def _escape_text(text: str) -> str:
    """Escape only '&' and '<'. A stray '>' cannot open a tag on its own,
    so leaving it alone is still safe, and doing so lets the block parser
    recognize '>' blockquote markers on the (already escaped) text."""
    return text.replace("&", "&amp;").replace("<", "&lt;")


def render(body: str, existing_ids=None, files=None) -> str:
    """Render a full note body to HTML. `existing_ids`: the referenced
    notes that exist, as a set, or a dict of id -> title to show each
    [[ref]] by its note's title (db.note_titles). `files`: the stored files
    its images point at, by hash prefix (db.files_by_prefix); without it,
    images are plain links."""
    blocks, stash = _render_body(body, existing_ids or set(), files)
    return _resolve_placeholders("\n".join(blocks), stash)


def _render_body(body: str, existing_ids, files=None):
    """The block-level HTML (placeholders unresolved) and the stash, which
    holds what was set aside and every label shown."""
    stash = _Stash(files=files)
    stripped = _strip_code(body, stash)
    escaped = _escape_text(stripped)
    # Placeholder tokens contain no '&' or '<' so they pass through intact.
    return _render_lines(escaped.split("\n"), stash, existing_ids), stash


def render_first_line(body: str, existing_ids=None, files=None) -> str:
    """Render just the first non-blank line of a note, for feed snippets."""
    for line in body.splitlines():
        if line.strip():
            return render(line, existing_ids, files)
    return ""


_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_TAG_RE = re.compile(r"</?(?:p|h[1-6]|li|ul|ol|blockquote|pre|br|hr|table|thead|tbody|tr|th|td|figure)\b[^>]*>")


def _to_plain(rendered_html: str) -> str:
    """Rendered HTML -> plain text on one line. Block tags become a space
    (so separate blocks don't run together); inline tags (<strong>,
    <code>, <a> ...) vanish without adding space before punctuation."""
    text = re.sub(r"<figcaption>.*?</figcaption>", "", rendered_html)   # the image's alt says it
    text = re.sub(r'<img [^>]*alt="([^"]*)"[^>]*>', r"\1", text)
    text = _BLOCK_TAG_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def first_line_text(body: str) -> str:
    """The first non-blank line as plain text, with markdown syntax
    removed (e.g. "# Intro to #physics" -> "Intro to #physics"). For places
    where the snippet sits inside a link, since rendered HTML can hold its
    own links (labels, [[refs]]) and links can't be nested."""
    return html.unescape(_TAG_RE.sub("", render_first_line(body))).strip()


# ---------------------------------------------------------------------------
# Backlink context: the passage around each [[ref]] to a given note
# ---------------------------------------------------------------------------

_SENTINEL = "\x01"  # marks the mention being extracted; survives rendering


def _context_units(text: str) -> list[str]:
    """Split (code-stashed) note text into the same units the renderer
    treats as blocks: each header line, list item and table row on its own,
    consecutive blockquote lines together, and consecutive plain lines
    together as a paragraph. The context for a mention is the unit it's in
    -- so a link in one bullet of a list shows that bullet, not the list."""
    units: list[str] = []
    group: list[str] = []
    group_kind = None

    def flush():
        nonlocal group, group_kind
        if group:
            units.append("\n".join(group))
        group, group_kind = [], None

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if _table_starts(lines, i) is not None:
            # Each row is its own unit, as its cells: "Base | 0.81 | see [[12]]".
            flush()
            units.append(" | ".join(_table_cells(line)))
            i += 2
            while i < len(lines) and not BLANK_RE.match(lines[i]) and not _starts_block(lines, i):
                units.append(" | ".join(_table_cells(lines[i])))
                i += 1
            continue
        i += 1
        if BLANK_RE.match(line) or HR_RE.match(line):
            flush()
        elif line.strip().startswith("\x00CODEBLOCK") and _PLACEHOLDER_RE.fullmatch(line.strip()):
            flush()  # a fenced block is its own thing, never prose context
        elif HEADER_RE.match(line) or UL_RE.match(line) or OL_RE.match(line):
            flush()
            units.append(line)
        else:
            kind = "quote" if BLOCKQUOTE_RE.match(line) else "para"
            if kind != group_kind:
                flush()
                group_kind = kind
            group.append(line)
    flush()
    return units


def _restore_code_source(text: str, stash: _Stash) -> str:
    """Put stashed code back as its original markdown source, so it can be
    rendered (and shown as plain text) along with the rest of the unit."""

    def _sub(m: re.Match) -> str:
        value = stash.items.get(m.group(0))
        if value is None:
            return m.group(0)
        if m.group(0).startswith("\x00MATH"):
            return value  # stored as typed
        if isinstance(value, tuple):
            lang, code = value
            return f"```{lang}\n{code}```"
        return f"`{value}`"

    return _PLACEHOLDER_RE.sub(_sub, text)


# Math in a plain-text passage, as typed: $$display$$ or $inline$.
_PASSAGE_MATH_RE = re.compile(MATH_BLOCK_RE.pattern + "|" + MATH_INLINE_RE.pattern)


def _inside_math(s: str, pos: int):
    """The (start, end) of the formula in `s` that `pos` falls strictly
    inside, if any -- a passage is never cut through one."""
    for m in _PASSAGE_MATH_RE.finditer(s):
        if m.start() < pos < m.end():
            return m.start(), m.end()
    return None


def _clip_before(s: str, n: int) -> tuple[str, bool]:
    if len(s) <= n:
        return s, False
    cut = s[-n:]
    space = cut.find(" ")
    if 0 <= space < 20:  # start on a word boundary if one is close by
        cut = cut[space + 1:]
    inside = _inside_math(s, len(s) - len(cut))
    if inside:  # don't start halfway through a formula: drop it
        cut = s[inside[1]:].lstrip()
    return cut, True


def _clip_after(s: str, n: int) -> tuple[str, bool]:
    if len(s) <= n:
        return s, False
    cut = s[:n]
    space = cut.rfind(" ")
    if space > n - 20:
        cut = cut[:space]
    inside = _inside_math(s, len(cut))
    if inside:  # don't end halfway through a formula: stop before it
        cut = s[:inside[0]].rstrip()
    return cut, True


def passage_html(text: str, titles: dict) -> Markup:
    """A backlink passage (plain text) as HTML: [[refs]] as their titles
    (refs_as_titles), and math -- $...$ or $$...$$ as typed -- marked for
    KaTeX to draw, inline either way so the row stays one line of text."""
    parts, pos = [], 0
    for m in _PASSAGE_MATH_RE.finditer(text):
        parts.append(refs_as_titles(text[pos:m.start()], titles))
        tex = m.group(1) if m.group(1) is not None else m.group(2)
        parts.append(Markup('<span class="math math-inline">${}$</span>').format(tex))
        pos = m.end()
    parts.append(refs_as_titles(text[pos:], titles))
    return Markup("").join(parts)


def ref_contexts(body: str, target_id: int, width: int = 240) -> list[dict]:
    """Every mention of [[target_id]] in `body`, each as the plain-text
    passage around it, trimmed to about `width` characters centered on the
    link. Returns dicts with `before`, `ref`, `after`, and `clipped_before`
    / `clipped_after` flags (for drawing an ellipsis).

    Rules:
    - A mention in the note's first non-blank line is skipped: that line is
      already shown as the backlink's title.
    - Mentions inside code are not links, so they're never returned
      (same rule as extract_note_refs).
    - Markdown syntax is removed; other [[refs]] and #labels in the passage
      stay as their literal text, math as typed (passage_html marks it to
      be drawn), an image as "Figure: <caption>".
    - The trim never cuts through a formula.
    """
    stash = _Stash()
    stripped = _strip_code(body, stash)
    ref_text = f"[[{target_id}]]"
    out: list[dict] = []

    for unit_index, unit in enumerate(_context_units(stripped)):
        first_line_end = unit.find("\n") if unit_index == 0 else -1
        for m in NOTE_REF_RE.finditer(unit):
            if int(m.group(1)) != target_id:
                continue
            if unit_index == 0 and (first_line_end == -1 or m.start() < first_line_end):
                continue  # it's in the title line

            marked = unit[: m.start()] + _SENTINEL + unit[m.end():]
            # An image reads as "Figure: <caption>" -- just the caption
            # wouldn't say there was a picture.
            marked = IMAGE_RE.sub(lambda i: f"Figure: {i.group(1)}" if i.group(1).strip() else "Figure", marked)
            plain = _to_plain(render(_restore_code_source(marked, stash)))
            if _SENTINEL not in plain:
                continue
            before, after = plain.split(_SENTINEL, 1)

            # Split the width around the link, giving any budget one side
            # doesn't need to the other.
            half = (width - len(ref_text)) // 2
            before_budget = half + max(0, half - len(after))
            after_budget = half + max(0, half - len(before))
            before, clipped_before = _clip_before(before, before_budget)
            after, clipped_after = _clip_after(after, after_budget)

            out.append({
                "before": before,
                "ref": ref_text,
                "after": after,
                "clipped_before": clipped_before,
                "clipped_after": clipped_after,
            })
    return out
