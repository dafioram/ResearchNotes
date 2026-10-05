"""
Activity log helpers that don't touch the database: working out what an
edit changed, and turning a stored event into words for the History page.
Recording events (and merging saves into editing sessions) lives in db.py.
"""

from __future__ import annotations

import difflib
import json
from datetime import datetime, timedelta

from . import markdown as md

# Saves to the same note within this long of the previous save are merged
# into one "edited" entry. Sliding: each save extends the session.
SESSION_WINDOW = timedelta(minutes=15)


def _content_lines(body: str) -> list[str]:
    """Lines that count: blank lines are ignored (as in the line count shown
    on cards), and trailing whitespace doesn't make a line different."""
    return [line.rstrip() for line in body.splitlines() if line.strip()]


def line_changes(old_body: str, new_body: str) -> tuple[int, int]:
    """(lines added, lines removed), counted like `git diff --stat`: a line
    that changed counts as one removed plus one added."""
    old, new = _content_lines(old_body), _content_lines(new_body)
    added = removed = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            removed += i2 - i1
        if op in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def edit_detail(old_body: str, old_date: str, new_body: str, new_date: str) -> dict | None:
    """Everything an edit changed, or None if nothing that counts did
    (e.g. only blank lines or trailing spaces changed)."""
    added, removed = line_changes(old_body, new_body)
    old_labels, new_labels = md.extract_labels(old_body), md.extract_labels(new_body)
    old_links, new_links = md.extract_note_refs(old_body), md.extract_note_refs(new_body)
    detail = {
        "lines_added": added,
        "lines_removed": removed,
        "labels_added": sorted(new_labels - old_labels),
        "labels_removed": sorted(old_labels - new_labels),
        "links_added": sorted(new_links - old_links),
        "links_removed": sorted(old_links - new_links),
    }
    if old_date != new_date:
        detail["date_from"], detail["date_to"] = old_date, new_date
    changed = (
        added or removed
        or detail["labels_added"] or detail["labels_removed"]
        or detail["links_added"] or detail["links_removed"]
        or "date_to" in detail
    )
    return detail if changed else None


def touches_links(detail: dict) -> bool:
    return bool(detail.get("links_added") or detail.get("links_removed"))


def within_session(last_iso: str, now_iso: str) -> bool:
    return datetime.fromisoformat(now_iso) - datetime.fromisoformat(last_iso) <= SESSION_WINDOW


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def clock_time(dt: datetime) -> str:
    """12-hour local time, e.g. "9:05 AM" (no %-I: not available on Windows)."""
    local = dt.astimezone()
    return f"{local.hour % 12 or 12}:{local:%M} {'AM' if local.hour < 12 else 'PM'}"


def local_stamp(iso_utc: str) -> str:
    """A stored UTC time as local date and time, e.g. "3 Oct 2026, 2:14 PM"."""
    local = datetime.fromisoformat(iso_utc).astimezone()
    return f"{local.day} {local:%b %Y}, {clock_time(local)}"


VERBS = {
    "created": "Created",
    "edited": "Edited",
    "deleted": "Deleted",
    "restored": "Restored",
    "attached": "Attached",
    "detached": "Removed",
}


def describe(kind: str, detail_json: str, save_count: int) -> dict:
    """The words for one event: a verb, an optional filename (for
    attachments, shown between the verb and the note), and a list of short
    change descriptions (for edits)."""
    detail = json.loads(detail_json or "{}")
    changes: list[str] = []
    if kind == "edited":
        if detail.get("restored_from"):
            changes.append(f"restored the version from {local_stamp(detail['restored_from'])}")
        a, r = detail.get("lines_added", 0), detail.get("lines_removed", 0)
        if a and r:
            changes.append(f"+{a} / −{r} lines")
        elif a:
            changes.append(f"+{a} line{'s' if a != 1 else ''}")
        elif r:
            changes.append(f"−{r} line{'s' if r != 1 else ''}")
        for name in detail.get("labels_added", []):
            changes.append(f"added #{name}")
        for name in detail.get("labels_removed", []):
            changes.append(f"removed #{name}")
        for ref in detail.get("links_added", []):
            changes.append(f"now links to [[{ref}]]")
        for ref in detail.get("links_removed", []):
            changes.append(f"no longer links to [[{ref}]]")
        if "date_to" in detail:
            changes.append(f"sort date {detail['date_from']} → {detail['date_to']}")
        if save_count > 1:
            changes.append(f"{save_count} saves")
    return {
        "verb": VERBS.get(kind, kind.capitalize()),
        "filename": detail.get("filename"),
        "preposition": {"attached": "to", "detached": "from"}.get(kind),
        "changes": changes,
    }
