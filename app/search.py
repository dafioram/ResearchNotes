"""
The search box's syntax (spec §7): what someone types, turned into a
`Query` that db.search_page() runs. One box covers everything -- words for
a quick look, operators when they're wanted:

    memory retrieval     notes with both words (any form: "retrieving" too)
    "spaced repetition"  that exact phrase
    retriev*             words starting with "retriev"
    -flashcards          without that word (or -"a phrase")
    #learning #memory    carrying either label (any of those typed)
    #physics-*           carrying #physics or any #physics-... label
    +#draft              must carry it, whatever else (several: all of them)
    -#draft              not carrying it
    is:unlinked          no [[links]] in or out (what Orphans listed)
    has:file             with an attachment (what Attachments listed)
    has:later            with a [[later]] link still to fill in
    after:2025-03        sort date on or after the start of March 2025
    before:2026          sort date before 2026 (YYYY, YYYY-MM or YYYY-MM-DD)
    1234                 a number alone also lists notes whose number
                         starts with it, first

Anything typed is safe: words reach FTS5 only as quoted phrases, never as
its own query syntax. Something that looks like an operator but isn't one
(like "after:yesterday") is reported back rather than silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

_TOKEN_RE = re.compile(r'(-?)"([^"]*)"?|(\S+)')
_LABEL_RE = re.compile(r"#([^\W\d_](?:[\w.-]*[^\W_])?)")  # the label rule, spec §6.1
# A namespace of labels (spec §6.4): "#physics-*" is #physics and every
# #physics-... label. The prefix is a label's part before its first "-".
_NAMESPACE_RE = re.compile(r"#([^\W\d_][\w.]*)-\*")


def label_term(text: str) -> str | None:
    """A typed "#label" or "#namespace-*" as the term the query keeps
    ("label" / "namespace-*", lowercase), or None if it's neither."""
    m = _NAMESPACE_RE.fullmatch(text)
    if m:
        return m[1].lower() + "-*"
    m = _LABEL_RE.fullmatch(text)
    return m[1].lower() if m else None
_DATE_RE = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")
FILTERS = {"is:unlinked": "unlinked", "has:file": "has_file", "has:files": "has_file",
           "has:later": "has_later"}


@dataclass
class Query:
    words: list[str] = field(default_factory=list)          # FTS5 terms, all required
    exclude_words: list[str] = field(default_factory=list)  # FTS5 terms, none allowed
    # label terms: a name, or "prefix-*" for a namespace (label_term)
    labels: list[str] = field(default_factory=list)           # any of these
    required_labels: list[str] = field(default_factory=list)  # all of these
    exclude_labels: list[str] = field(default_factory=list)   # none of these
    unlinked: bool = False
    has_file: bool = False
    has_later: bool = False
    after: str | None = None    # sort_date >= this (YYYY-MM-DD)
    before: str | None = None   # sort_date < this
    number: str | None = None   # the whole query was this number
    problems: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.words or self.exclude_words or self.labels or self.required_labels
                    or self.exclude_labels
                    or self.unlinked or self.has_file or self.has_later or self.after or self.before)

    @property
    def in_date_order(self) -> bool:
        """Whether results come newest first (nothing to rank by), so the
        feed can show day headings and jump to a month."""
        return not self.words and not self.number

    @property
    def only_labels(self) -> list[str] | None:
        """The labels, when the query is nothing but #labels (any of them),
        which the feed has a quicker way to list (db.list_notes_page)."""
        if self.labels and Query(labels=self.labels) == self:
            return self.labels
        return None


def _phrase(text: str, prefix: bool = False) -> str | None:
    """A word or phrase as a quoted FTS5 phrase, or None if it holds no
    word characters at all (it couldn't match anything)."""
    if not re.search(r"\w", text):
        return None
    return '"' + text.replace('"', '""') + '"' + ("*" if prefix else "")


def period(text: str) -> tuple[str, str] | None:
    """'2025' / '2025-03' / '2025-03-14' -> (the first day of that period,
    the first day after it), or None if it isn't a real date."""
    m = _DATE_RE.fullmatch(text.strip())
    if not m:
        return None
    year, month, day = int(m[1]), int(m[2] or 1), int(m[3] or 1)
    try:
        start = date(year, month, day)
        if m[3]:
            end = start + timedelta(days=1)
        elif m[2]:
            end = date(year + month // 12, month % 12 + 1, 1)
        else:
            end = date(year + 1, 1, 1)
    except (ValueError, OverflowError):
        return None
    return start.isoformat(), end.isoformat()


def _period_start(text: str) -> str | None:
    bounds = period(text)
    return bounds[0] if bounds else None


def parse(raw: str) -> Query:
    q = Query()
    raw = (raw or "").strip()
    if raw.isdigit():
        q.number = raw
        q.words.append(_phrase(raw))
        return q
    for m in _TOKEN_RE.finditer(raw):
        if m[3] is None:  # "a phrase", maybe -"a phrase"
            term = _phrase(m[2])
            if term:
                (q.exclude_words if m[1] else q.words).append(term)
            continue
        token = m[3]
        negate = token.startswith("-") and len(token) > 1
        body = token[1:] if negate else token
        lower = body.lower()
        label = label_term(body)
        required = label_term(token[1:]) if token.startswith("+") else None
        if required:
            q.required_labels.append(required)
        elif label:
            (q.exclude_labels if negate else q.labels).append(label)
        elif lower in FILTERS and not negate:
            setattr(q, FILTERS[lower], True)
        elif lower.startswith(("after:", "before:")) and not negate:
            key, value = lower.split(":", 1)
            bound = _period_start(value)
            if bound is None:
                q.problems.append(f"couldn't read the date in “{token}” (use YYYY, YYYY-MM or YYYY-MM-DD)")
            else:
                setattr(q, key, bound)
        else:
            term = _phrase(body.rstrip("*"), prefix=body.endswith("*"))
            if term:
                (q.exclude_words if negate else q.words).append(term)
    return q


def toggle(raw: str, token: str) -> str:
    """The query with `token` (e.g. "#memory", "is:unlinked") added, or
    removed if it's already there -- for the sidebar's links."""
    words = (raw or "").split()
    kept = [w for w in words if w.lower() != token.lower()]
    if len(kept) == len(words):
        kept.append(token)
    return " ".join(kept)


def has_token(raw: str, token: str) -> bool:
    return token.lower() in (w.lower() for w in (raw or "").split())
