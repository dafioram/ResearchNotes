import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import click
from flask import current_app, g

from . import activity
from . import markdown as md


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE_PATH"],
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    db_path = Path(app.config["DATABASE_PATH"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _migrate_label_tables(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    schema_path = Path(app.root_path).parent / "schema.sql"
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    _rebuild_label_index_if_missing(conn)
    conn.close()

    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)


def _migrate_label_tables(conn: sqlite3.Connection) -> None:
    """Older databases stored labels in two tables: labels(id, name) and
    note_labels(note_id, label_id). Both are only an index of the #labels
    written in note text, so rather than converting rows, drop them; the
    schema then creates the one-table note_labels(note_id, name) and
    _rebuild_label_index_if_missing() refills it from the note text."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(note_labels)")}
    if "label_id" not in columns:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("DROP TABLE note_labels; DROP TABLE IF EXISTS labels;")


def _rebuild_label_index_if_missing(conn: sqlite3.Connection) -> None:
    """Refill note_labels from note text when it's empty but notes exist.
    Runs after the migration above, and also repairs a migration that was
    interrupted between dropping the old tables and refilling -- the check
    looks at the data, not at whether a migration just happened. Deleted
    notes are included, as on save, so restoring one brings its labels
    back. A database that simply has no labels anywhere costs one quick
    pass over the notes at startup."""
    if conn.execute("SELECT 1 FROM note_labels LIMIT 1").fetchone():
        return
    rows = conn.execute("SELECT id, body FROM notes").fetchall()
    with conn:  # one transaction
        conn.executemany(
            "INSERT OR IGNORE INTO note_labels (note_id, name) VALUES (?, ?)",
            [(r["id"], name) for r in rows for name in md.extract_labels(r["body"])],
        )


@click.command("init-db")
def init_db_command():
    """Initialize the database (safe to re-run; only creates missing tables)."""
    init_db(current_app)
    click.echo("Database initialized.")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def create_note(body: str, sort_date: str) -> int:
    db = get_db()
    ts = now_iso()
    cur = db.execute(
        "INSERT INTO notes (body, sort_date, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (body, sort_date, ts, ts),
    )
    note_id = cur.lastrowid
    _sync_note_metadata(note_id, body)
    _log_event("created", note_id, ts)
    db.commit()
    return note_id


def update_note(note_id: int, body: str, sort_date: str) -> None:
    db = get_db()
    before = db.execute("SELECT body, sort_date FROM notes WHERE id = ?", (note_id,)).fetchone()
    ts = now_iso()
    db.execute(
        "UPDATE notes SET body = ?, sort_date = ?, updated_at = ? WHERE id = ?",
        (body, sort_date, ts, note_id),
    )
    _sync_note_metadata(note_id, body)
    if before is not None:
        _log_edit(note_id, before["body"], before["sort_date"], body, sort_date, ts)
    db.commit()


def get_note(note_id: int, include_deleted: bool = False):
    db = get_db()
    row = db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        return None
    if not include_deleted and row["deleted_at"] is not None:
        return None
    return row


def _paged(sql: str, params: tuple, limit: int, offset: int):
    """Run a full, ORDER BY'd listing query one page at a time. Returns
    (rows for this page, total rows across all pages)."""
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) AS c FROM ({sql})", params).fetchone()["c"]
    rows = db.execute(f"{sql} LIMIT ? OFFSET ?", params + (limit, offset)).fetchall()
    return rows, total


def _list_notes_sql(label: str | None):
    if label:
        return (
            """
            SELECT n.* FROM notes n
            JOIN note_labels nl ON nl.note_id = n.id
            WHERE n.deleted_at IS NULL AND nl.name = ?
            ORDER BY n.sort_date DESC, n.id DESC
            """,
            (label.lower(),),
        )
    return (
        "SELECT * FROM notes WHERE deleted_at IS NULL ORDER BY sort_date DESC, id DESC",
        (),
    )


def list_notes(label: str | None = None):
    sql, params = _list_notes_sql(label)
    return get_db().execute(sql, params).fetchall()


def list_notes_page(label: str | None, limit: int, offset: int):
    sql, params = _list_notes_sql(label)
    return _paged(sql, params, limit, offset)


def _build_fts_query(raw: str) -> str:
    """Turn free-typed search text into a safe FTS5 query: each whitespace
    -separated token becomes its own quoted phrase (ANDed together), so
    characters that are meaningful to FTS5 syntax (*, -, ", parens, ...)
    are treated as literal text instead of query operators."""
    tokens = raw.split()
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def search_notes(query: str, label: str | None = None):
    """Search non-deleted notes by body text (SQLite FTS5), optionally
    restricted to notes carrying `label`.

    If `query` is made up of digits only, notes whose id STARTS WITH that
    number are also pulled in (e.g. "100" matches note 100, 1000, 1005,
    ...) and sorted to the very top -- exact id match first, then the
    rest ascending by id -- ahead of the ordinary text-relevance results.
    A note that matches both ways is only listed once, in the id group.
    """
    query = query.strip()
    if not query:
        return list_notes(label=label)

    db = get_db()

    label_join = ""
    label_where = ""
    label_params: tuple = ()
    if label:
        label_join = "JOIN note_labels nl ON nl.note_id = n.id"
        label_where = "AND nl.name = ?"
        label_params = (label.lower(),)

    id_matches = []
    if query.isdigit():
        sql = f"""
            SELECT n.* FROM notes n
            {label_join}
            WHERE n.deleted_at IS NULL
              AND CAST(n.id AS TEXT) LIKE ?
              {label_where}
            ORDER BY (n.id = ?) DESC, n.id ASC
        """
        params = (query + "%",) + label_params + (int(query),)
        id_matches = db.execute(sql, params).fetchall()

    id_match_ids = {r["id"] for r in id_matches}

    fts_query = _build_fts_query(query)
    sql = f"""
        SELECT n.* FROM notes n
        JOIN notes_fts ON notes_fts.rowid = n.id
        {label_join}
        WHERE n.deleted_at IS NULL
          AND notes_fts MATCH ?
          {label_where}
        ORDER BY bm25(notes_fts)
    """
    params = (fts_query,) + label_params
    fts_rows = db.execute(sql, params).fetchall()
    text_matches = [r for r in fts_rows if r["id"] not in id_match_ids]

    return list(id_matches) + text_matches


def list_deleted_notes():
    db = get_db()
    return db.execute(
        "SELECT * FROM notes WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
    ).fetchall()


def soft_delete_note(note_id: int) -> None:
    db = get_db()
    ts = now_iso()
    cur = db.execute(
        "UPDATE notes SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL", (ts, note_id)
    )
    if cur.rowcount:
        _log_event("deleted", note_id, ts)
    db.commit()


def restore_note(note_id: int) -> None:
    db = get_db()
    cur = db.execute(
        "UPDATE notes SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL", (note_id,)
    )
    if cur.rowcount:
        _log_event("restored", note_id, now_iso())
    db.commit()


def get_existing_note_ids() -> set[int]:
    db = get_db()
    rows = db.execute("SELECT id FROM notes WHERE deleted_at IS NULL").fetchall()
    return {r["id"] for r in rows}


def get_random_note_id():
    db = get_db()
    row = db.execute(
        "SELECT id FROM notes WHERE deleted_at IS NULL ORDER BY RANDOM() LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


# A note is connected if it links out to anything (a missing or deleted
# target still counts: the graph shows it as a ghost), or if a note that
# isn't in Trash links to it. The Orphans list and the note page's
# "View graph" button use this same rule.
_LINKED_FROM_LIVE_NOTE_SQL = """
    SELECT l.to_note_id FROM note_links l
    JOIN notes src ON src.id = l.from_note_id
    WHERE src.deleted_at IS NULL
"""

_ORPHANS_SQL = f"""
    SELECT n.* FROM notes n
    WHERE n.deleted_at IS NULL
      AND n.id NOT IN (SELECT from_note_id FROM note_links)
      AND n.id NOT IN ({_LINKED_FROM_LIVE_NOTE_SQL})
    ORDER BY n.sort_date DESC, n.id DESC
"""


def note_has_connections(note_id: int) -> bool:
    row = get_db().execute(
        f"""
        SELECT EXISTS (SELECT 1 FROM note_links WHERE from_note_id = ?)
            OR ? IN ({_LINKED_FROM_LIVE_NOTE_SQL}) AS connected
        """,
        (note_id, note_id),
    ).fetchone()
    return bool(row["connected"])


def feed_position(note_id: int) -> int:
    """How many notes come before this one in the default feed order
    (sort date newest first, then id), counting only notes not in Trash.
    Used to open the feed on the page a note is on."""
    db = get_db()
    row = db.execute("SELECT sort_date FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        return 0
    return db.execute(
        """
        SELECT COUNT(*) AS c FROM notes
        WHERE deleted_at IS NULL
          AND (sort_date > ? OR (sort_date = ? AND id > ?))
        """,
        (row["sort_date"], row["sort_date"], note_id),
    ).fetchone()["c"]

_WITH_ATTACHMENTS_SQL = """
    SELECT DISTINCT n.* FROM notes n
    JOIN note_attachments na ON na.note_id = n.id
    WHERE n.deleted_at IS NULL
    ORDER BY n.sort_date DESC, n.id DESC
"""


def get_orphan_notes_page(limit: int, offset: int):
    return _paged(_ORPHANS_SQL, (), limit, offset)


def get_notes_with_attachments_page(limit: int, offset: int):
    return _paged(_WITH_ATTACHMENTS_SQL, (), limit, offset)


def get_attachment_counts() -> dict:
    """note_id -> number of attachments, for every note that has at least
    one. Used to show a "N files" badge on feed-style cards without an
    N+1 query per note."""
    db = get_db()
    rows = db.execute(
        "SELECT note_id, COUNT(*) AS c FROM note_attachments GROUP BY note_id"
    ).fetchall()
    return {r["note_id"]: r["c"] for r in rows}


# ---------------------------------------------------------------------------
# Labels & links syncing
# ---------------------------------------------------------------------------

def _sync_note_metadata(note_id: int, body: str) -> None:
    """Re-derive labels and note-links from a note's body and update the
    join tables to match exactly (adds new rows, removes stale ones)."""
    db = get_db()
    label_names = md.extract_labels(body)
    ref_ids = md.extract_note_refs(body)

    current_labels = {
        r["name"]
        for r in db.execute(
            "SELECT name FROM note_labels WHERE note_id = ?", (note_id,)
        ).fetchall()
    }
    for name in label_names - current_labels:
        db.execute(
            "INSERT INTO note_labels (note_id, name) VALUES (?, ?)", (note_id, name)
        )
    for name in current_labels - label_names:
        db.execute(
            "DELETE FROM note_labels WHERE note_id = ? AND name = ?", (note_id, name)
        )

    current_ref_ids = {
        r["to_note_id"]
        for r in db.execute(
            "SELECT to_note_id FROM note_links WHERE from_note_id = ?", (note_id,)
        ).fetchall()
    }
    for rid in ref_ids - current_ref_ids:
        db.execute(
            "INSERT INTO note_links (from_note_id, to_note_id) VALUES (?, ?)",
            (note_id, rid),
        )
    for rid in current_ref_ids - ref_ids:
        db.execute(
            "DELETE FROM note_links WHERE from_note_id = ? AND to_note_id = ?",
            (note_id, rid),
        )


def get_labels_with_counts():
    """Every label used by at least one non-deleted note, with how many
    such notes use it, most-used first."""
    db = get_db()
    return db.execute(
        """
        SELECT nl.name AS name, COUNT(*) AS count
        FROM note_labels nl
        JOIN notes n ON n.id = nl.note_id
        WHERE n.deleted_at IS NULL
        GROUP BY nl.name
        ORDER BY count DESC, nl.name ASC
        """
    ).fetchall()


def get_backlinks(note_id: int):
    """Notes (non-deleted) that reference this note via [[note_id]]."""
    db = get_db()
    return db.execute(
        """
        SELECT n.* FROM notes n
        JOIN note_links l ON l.from_note_id = n.id
        WHERE l.to_note_id = ? AND n.deleted_at IS NULL
        ORDER BY n.sort_date DESC, n.id DESC
        """,
        (note_id,),
    ).fetchall()


def get_backlink_counts() -> dict:
    """note_id -> number of non-deleted notes referencing it. Counts the
    same thing get_backlinks() lists, so a card's badge always matches
    the list you see when you expand or open that note."""
    db = get_db()
    rows = db.execute(
        """
        SELECT l.to_note_id AS note_id, COUNT(*) AS c
        FROM note_links l
        JOIN notes n ON n.id = l.from_note_id
        WHERE n.deleted_at IS NULL
        GROUP BY l.to_note_id
        """
    ).fetchall()
    return {r["note_id"]: r["c"] for r in rows}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def _plain_snippet(body: str, length: int = 42) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line:
            line = line.lstrip("#").strip()
            line = line.lstrip("-*").strip()
            if len(line) > length:
                line = line[: length - 1].rstrip() + "\u2026"
            return line or "(untitled)"
    return "(empty note)"


def _graph_edges(db):
    return db.execute(
        """
        SELECT l.from_note_id, l.to_note_id
        FROM note_links l
        JOIN notes nf ON nf.id = l.from_note_id
        WHERE nf.deleted_at IS NULL
        """
    ).fetchall()


def get_graph_data(center_id: int, hops: int = 1):
    """The graph around one note: every note within `hops` links of it
    (following links in either direction), and the links among them.
    There is deliberately no whole-collection graph; a graph always
    belongs to a note."""
    db = get_db()
    edges = [(r["from_note_id"], r["to_note_id"]) for r in _graph_edges(db)]

    adjacency: dict[int, set[int]] = {}
    for a, b in edges:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    visited = {center_id}
    frontier = {center_id}
    for _ in range(max(hops, 0)):
        next_frontier = set()
        for node in frontier:
            next_frontier |= adjacency.get(node, set()) - visited
        visited |= next_frontier
        frontier = next_frontier
        if not frontier:
            break
    node_ids = visited
    chosen_edges = [(a, b) for a, b in edges if a in visited and b in visited]

    if not node_ids:
        return {"nodes": [], "edges": []}

    placeholders = ",".join("?" * len(node_ids))
    real_rows = db.execute(
        f"SELECT id, body FROM notes WHERE deleted_at IS NULL AND id IN ({placeholders})",
        tuple(node_ids),
    ).fetchall()
    real_notes = {r["id"]: r["body"] for r in real_rows}

    nodes = []
    for nid in node_ids:
        if nid in real_notes:
            nodes.append(
                {
                    "id": nid,
                    "label": f"{nid}. {_plain_snippet(real_notes[nid])}",
                    "ghost": False,
                }
            )
        else:
            nodes.append({"id": nid, "label": f"{nid} (missing)", "ghost": True})

    edge_list = [
        {"from": a, "to": b, "broken": b not in real_notes} for a, b in chosen_edges
    ]

    return {"nodes": nodes, "edges": edge_list}


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

def get_attachment_by_hash(file_hash: str):
    db = get_db()
    return db.execute("SELECT * FROM attachments WHERE hash = ?", (file_hash,)).fetchone()


def get_attachment(attachment_id: int):
    db = get_db()
    return db.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()


def create_attachment(file_hash: str, filename: str, extension: str, mime_type: str, size: int) -> int:
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO attachments (hash, filename, extension, mime_type, size, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (file_hash, filename, extension, mime_type, size, now_iso()),
    )
    db.commit()
    return cur.lastrowid


def link_attachment(note_id: int, attachment_id: int) -> None:
    db = get_db()
    cur = db.execute(
        "INSERT OR IGNORE INTO note_attachments (note_id, attachment_id) VALUES (?, ?)",
        (note_id, attachment_id),
    )
    if cur.rowcount:
        _log_attachment_event("attached", note_id, attachment_id)
    db.commit()


def unlink_attachment(note_id: int, attachment_id: int) -> None:
    db = get_db()
    cur = db.execute(
        "DELETE FROM note_attachments WHERE note_id = ? AND attachment_id = ?",
        (note_id, attachment_id),
    )
    if cur.rowcount:
        _log_attachment_event("detached", note_id, attachment_id)
    db.commit()


def _log_attachment_event(kind: str, note_id: int, attachment_id: int) -> None:
    row = get_db().execute(
        "SELECT filename FROM attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    detail = {"filename": row["filename"] if row else "a file"}
    _log_event(kind, note_id, now_iso(), detail)


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------

def _log_event(kind: str, note_id: int, ts: str, detail: dict | None = None) -> None:
    get_db().execute(
        "INSERT INTO activity (kind, note_id, created_at, updated_at, detail) VALUES (?, ?, ?, ?, ?)",
        (kind, note_id, ts, ts, json.dumps(detail or {})),
    )


def _log_edit(note_id: int, old_body: str, old_date: str,
              new_body: str, new_date: str, ts: str) -> None:
    """Record a save as part of an editing session.

    Saves to the same note within activity.SESSION_WINDOW of the previous
    one extend that session instead of adding a new entry. The entry's
    changes are always measured from the note as it was when the session
    began (base_body), not summed per save, so adding a line and deleting
    it again nets out to nothing. A session whose net change is nothing is
    removed. Edits right after creating a note fold into its "created"
    entry. A save that changes nothing that counts is not logged at all.
    """
    db = get_db()

    # Sessions that can no longer be extended don't need their starting
    # text any more; drop it so old versions don't accumulate.
    db.execute(
        "UPDATE activity SET base_body = NULL, base_sort_date = NULL "
        "WHERE base_body IS NOT NULL AND updated_at < ?",
        (activity.session_cutoff(ts),),
    )

    last = db.execute(
        """
        SELECT * FROM activity
        WHERE note_id = ? AND kind IN ('created', 'edited')
        ORDER BY updated_at DESC, id DESC LIMIT 1
        """,
        (note_id,),
    ).fetchone()

    if last is not None and activity.within_session(last["updated_at"], ts):
        if last["kind"] == "created":
            if old_body != new_body or old_date != new_date:
                db.execute(
                    "UPDATE activity SET updated_at = ?, save_count = save_count + 1 WHERE id = ?",
                    (ts, last["id"]),
                )
            return
        if last["base_body"] is not None:
            detail = activity.edit_detail(
                last["base_body"], last["base_sort_date"], new_body, new_date
            )
            if detail is None:
                db.execute("DELETE FROM activity WHERE id = ?", (last["id"],))
            else:
                db.execute(
                    """
                    UPDATE activity SET updated_at = ?, save_count = save_count + 1,
                        detail = ?, touches_links = ?
                    WHERE id = ?
                    """,
                    (ts, json.dumps(detail), int(activity.touches_links(detail)), last["id"]),
                )
            return

    detail = activity.edit_detail(old_body, old_date, new_body, new_date)
    if detail is None:
        return
    db.execute(
        """
        INSERT INTO activity (kind, note_id, created_at, updated_at, detail,
                              touches_links, base_body, base_sort_date)
        VALUES ('edited', ?, ?, ?, ?, ?, ?, ?)
        """,
        (note_id, ts, ts, json.dumps(detail), int(activity.touches_links(detail)),
         old_body, old_date),
    )


ACTIVITY_FILTERS = {
    "all": ("", "All activity"),
    "created": ("a.kind = 'created'", "New notes"),
    "edited": ("a.kind = 'edited'", "Edits"),
    "links": ("a.touches_links = 1", "Link changes"),
    "attachments": ("a.kind IN ('attached', 'detached')", "Attachments"),
    "deleted": ("a.kind IN ('deleted', 'restored')", "Deleted & restored"),
}


def activity_page(kind_filter: str, limit: int, offset: int):
    """One page of activity, newest first, with each event's note (title
    text and whether it's currently deleted). Returns (rows, total)."""
    where = ACTIVITY_FILTERS.get(kind_filter, ACTIVITY_FILTERS["all"])[0]
    sql = f"""
        SELECT a.id, a.kind, a.note_id, a.created_at, a.updated_at, a.save_count,
               a.detail, n.body AS note_body, n.deleted_at AS note_deleted_at
        FROM activity a
        JOIN notes n ON n.id = a.note_id
        {"WHERE " + where if where else ""}
        ORDER BY a.updated_at DESC, a.id DESC
    """
    return _paged(sql, (), limit, offset)


def list_note_attachments(note_id: int):
    db = get_db()
    return db.execute(
        """
        SELECT a.* FROM attachments a
        JOIN note_attachments na ON na.attachment_id = a.id
        WHERE na.note_id = ?
        ORDER BY a.created_at DESC
        """,
        (note_id,),
    ).fetchall()


