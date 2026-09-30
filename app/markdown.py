"""
A small, hand-rolled Markdown subset renderer for research notes.

This is deliberately NOT a full CommonMark implementation. It supports just
enough syntax for note-taking, plus two note-specific extensions:

    #label          -> a clickable tag (single '#' immediately followed by a
                        word character; requires NO space, otherwise it's an
                        ATX header). A leading '##label' (double hash, no
                        space) is neither a header nor a label -- it is left
                        as plain text.
    [[123]]         -> a reference to note #123. Renders as a link if note
                        123 exists (and is not deleted), otherwise as a
                        "ghost" reference so broken links are visible at a
                        glance.

Supported standard Markdown subset:
    # .. ######      ATX headers (must have a space after the hashes)
    **bold** / __bold__
    *italic* / _italic_
    ~~strikethrough~~
    `inline code`
    ```lang\n code \n```   fenced code blocks
    [text](url)       links
    - item / * item    unordered lists
    1. item            ordered lists
    > quote            blockquotes
    ---  ***  ___      horizontal rules
    blank-line-separated paragraphs, single '\n' -> <br>

Raw HTML is never passed through -- everything is escaped first, so the
supported syntax above is genuinely the entire vocabulary available.
No image syntax is supported by design; attachments are a separate feature.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Extraction (used both for metadata syncing at save time, and for rendering)
# ---------------------------------------------------------------------------

CODE_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_+-]*)\n?(.*?)```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")

# A '#' starts a label only if it is not itself preceded by a '#' (so a
# doubled '##word' is inert) and is immediately followed by a label
# character. This also naturally keeps ATX headers ("# Title", which have a
# space after the hashes) out of the label net.
LABEL_RE = re.compile(r"(?<!#)#([A-Za-z0-9_-]+)")
NOTE_REF_RE = re.compile(r"\[\[(\d+)\]\]")

_PLACEHOLDER_TMPL = "\x00{kind}{idx}\x00"
_PLACEHOLDER_RE = re.compile(r"\x00([A-Z]+)(\d+)\x00")


@dataclass
class _Stash:
    """Collects raw fragments (code, links, refs) pulled out of the text so
    later passes can't accidentally reparse markdown syntax inside them."""

    items: dict = field(default_factory=dict)
    counter: int = 0

    def store(self, kind: str, value):
        key = _PLACEHOLDER_TMPL.format(kind=kind, idx=self.counter)
        self.items[key] = value
        self.counter += 1
        return key


def _strip_code(text: str, stash: _Stash) -> str:
    """Pull fenced code blocks and inline code spans out into the stash,
    replacing them with placeholder tokens. Must run on RAW text so that
    labels/refs typed inside code are never extracted or linkified."""

    def _block(m: re.Match) -> str:
        lang, code = m.group(1), m.group(2)
        return stash.store("CODEBLOCK", (lang, code))

    text = CODE_BLOCK_RE.sub(_block, text)

    def _span(m: re.Match) -> str:
        return stash.store("CODESPAN", m.group(1))

    text = INLINE_CODE_RE.sub(_span, text)
    return text


def extract_labels(body: str) -> set[str]:
    """Return the set of distinct label names (lowercased) used in a note,
    ignoring anything inside code blocks/spans."""
    stash = _Stash()
    stripped = _strip_code(body, stash)
    return {m.group(1).lower() for m in LABEL_RE.finditer(stripped)}


def extract_note_refs(body: str) -> set[int]:
    """Return the set of distinct note ids referenced via [[id]] in a note,
    ignoring anything inside code blocks/spans."""
    stash = _Stash()
    stripped = _strip_code(body, stash)
    return {int(m.group(1)) for m in NOTE_REF_RE.finditer(stripped)}


def line_count(body: str) -> int:
    """Number of non-blank lines in a note body."""
    return sum(1 for line in body.splitlines() if line.strip())


# ---------------------------------------------------------------------------
# Inline rendering
# ---------------------------------------------------------------------------

BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
ITALIC_RE = re.compile(r"\*(.+?)\*|_(.+?)_", re.DOTALL)
STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")


def _render_inline(text: str, stash: _Stash, existing_ids: set[int]) -> str:
    """Render inline markdown (bold/italic/strike/links/labels/note-refs)
    on text that has ALREADY been HTML-escaped and had code spans stashed.
    Links and note-refs are stashed first so bold/italic/label passes can't
    reach into a URL or a ref's rendered HTML."""

    def _link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        safe_url = html.escape(url, quote=True)
        return stash.store("LINK", f'<a href="{safe_url}" rel="noopener">{label}</a>')

    text = LINK_RE.sub(_link, text)

    def _ref(m: re.Match) -> str:
        note_id = int(m.group(1))
        if note_id in existing_ids:
            frag = f'<a class="note-ref" href="/notes/{note_id}">[[{note_id}]]</a>'
        else:
            frag = f'<span class="note-ref note-ref-ghost" title="Note {note_id} does not exist">[[{note_id}]]</span>'
        return stash.store("REF", frag)

    text = NOTE_REF_RE.sub(_ref, text)

    text = BOLD_RE.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = STRIKE_RE.sub(lambda m: f"<del>{m.group(1)}</del>", text)
    text = ITALIC_RE.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)

    def _label(m: re.Match) -> str:
        name = m.group(1)
        return stash.store(
            "LABELTAG",
            f'<a class="label-tag" href="/?label={html.escape(name.lower(), quote=True)}">#{html.escape(name)}</a>',
        )

    text = LABEL_RE.sub(_label, text)

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


def _render_lines(lines: list[str], stash: _Stash, existing_ids: set[int]) -> list[str]:
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
        while i < n and not BLANK_RE.match(lines[i]) and not (
            HEADER_RE.match(lines[i])
            or HR_RE.match(lines[i])
            or BLOCKQUOTE_RE.match(lines[i])
            or UL_RE.match(lines[i])
            or OL_RE.match(lines[i])
            or (lines[i].strip().startswith("\x00CODEBLOCK"))
        ):
            buf.append(lines[i])
            i += 1
        out.append("<p>" + "<br>".join(inline(b) for b in buf) + "</p>")

    return out


def _escape_text(text: str) -> str:
    """Escape only '&' and '<'. A stray '>' cannot open a tag on its own,
    so leaving it alone is still safe, and doing so lets the block parser
    recognize '>' blockquote markers on the (already escaped) text."""
    return text.replace("&", "&amp;").replace("<", "&lt;")


def render(body: str, existing_ids: set[int] | None = None) -> str:
    """Render a full note body to HTML."""
    existing_ids = existing_ids or set()
    stash = _Stash()
    stripped = _strip_code(body, stash)
    escaped = _escape_text(stripped)
    # Placeholder tokens contain no '&' or '<' so they pass through intact.
    lines = escaped.split("\n")
    blocks = _render_lines(lines, stash, existing_ids)
    html_out = "\n".join(blocks)
    return _resolve_placeholders(html_out, stash)


def render_first_line(body: str, existing_ids: set[int] | None = None) -> str:
    """Render just the first non-blank line of a note, for feed snippets."""
    for line in body.splitlines():
        if line.strip():
            return render(line, existing_ids)
    return ""


_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_TAG_RE = re.compile(r"</?(?:p|h[1-6]|li|ul|ol|blockquote|pre|br|hr)\b[^>]*>")


def _to_plain(rendered_html: str) -> str:
    """Rendered HTML -> plain text on one line. Block tags become a space
    (so separate blocks don't run together); inline tags (<strong>,
    <code>, <a> ...) vanish without adding space before punctuation."""
    text = _BLOCK_TAG_RE.sub(" ", rendered_html)
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
    treats as blocks: each header line and each list item on its own,
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

    for line in text.split("\n"):
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
        if isinstance(value, tuple):
            lang, code = value
            return f"```{lang}\n{code}```"
        return f"`{value}`"

    return _PLACEHOLDER_RE.sub(_sub, text)


def _clip_before(s: str, n: int) -> tuple[str, bool]:
    if len(s) <= n:
        return s, False
    cut = s[-n:]
    space = cut.find(" ")
    if 0 <= space < 20:  # start on a word boundary if one is close by
        cut = cut[space + 1:]
    return cut, True


def _clip_after(s: str, n: int) -> tuple[str, bool]:
    if len(s) <= n:
        return s, False
    cut = s[:n]
    space = cut.rfind(" ")
    if space > n - 20:
        cut = cut[:space]
    return cut, True


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
      stay as their literal text.
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
