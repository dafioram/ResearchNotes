import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import click
from flask import current_app, g
from flask.cli import with_appcontext

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
    """Create whatever tables are missing. Runs at every startup, so it
    must stay cheap: it never reads or rewrites existing data (spec §13)."""
    db_path = Path(app.config["DATABASE_PATH"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    schema_path = Path(app.root_path).parent / "schema.sql"
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(reindex_command)


@click.command("init-db")
def init_db_command():
    """Initialize the database (safe to re-run; only creates missing tables)."""
    init_db(current_app)
    click.echo("Database initialized.")


@click.command("reindex")
@click.option("--vacuum", is_flag=True,
              help="Then compact the database file (rewrites all of it, once).")
@with_appcontext
def reindex_command(vacuum):
    """Rebuild labels, links and the search index from the notes' text."""
    started = time.perf_counter()
    count = reindex()
    click.echo(f"Rebuilt labels, links and the search index for {count} "
               f"note{'' if count == 1 else 's'} in {time.perf_counter() - started:.1f} s.")
    if vacuum:
        get_db().execute("VACUUM")
        click.echo("Compacted the database file.")


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


def _marks(ids) -> str:
    return ",".join("?" * len(ids))


def _chunks(ids, size: int = 500):
    """`ids` in lists small enough for one IN (...) each: older SQLite
    builds allow only 999 parameters per statement."""
    ids = list(ids)
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def _paged(select_sql: str, count_sql: str, params: tuple, limit: int, offset: int):
    """One page of an ORDER BY'd listing, and the total across all pages.
    The count is a query of its own: wrapping the listing in COUNT(*) made
    SQLite sort every row just to count them."""
    db = get_db()
    total = db.execute(count_sql, params).fetchone()[0]
    rows = db.execute(f"{select_sql} LIMIT ? OFFSET ?", params + (limit, offset)).fetchall()
    return rows, total


# Every list of notes is in feed order: sort date newest first, then id.
# Each listing is written as "FROM notes n ... WHERE n.deleted_at IS NULL
# ...": that condition is what lets SQLite read the order straight off the
# live-notes index (schema.sql) and stop after one page, instead of sorting
# every note.
_FEED_ORDER = "ORDER BY n.sort_date DESC, n.id DESC"


def _paged_notes(from_where: str, params: tuple, limit: int, offset: int):
    return _paged(
        f"SELECT n.* {from_where} {_FEED_ORDER}", f"SELECT COUNT(*) {from_where}",
        params, limit, offset,
    )


def _list_notes_from(label: str | None):
    if label:
        return (
            "FROM notes n JOIN note_labels nl ON nl.note_id = n.id "
            "WHERE n.deleted_at IS NULL AND nl.name = ?",
            (label.lower(),),
        )
    return "FROM notes n WHERE n.deleted_at IS NULL", ()


def list_notes(label: str | None = None):
    from_where, params = _list_notes_from(label)
    return get_db().execute(f"SELECT n.* {from_where} {_FEED_ORDER}", params).fetchall()


def list_notes_page(label: str | None, limit: int, offset: int):
    if not label:
        return _paged_notes("FROM notes n WHERE n.deleted_at IS NULL", (), limit, offset)
    db = get_db()
    name = label.lower()
    total = label_note_count(name)
    live = db.execute("SELECT COUNT(*) FROM notes WHERE deleted_at IS NULL").fetchone()[0]
    # A label's page can be read two ways, and which is quick depends on
    # how common the label is. Walking the feed in order, checking each
    # note's labels, stops as soon as the page is full: quick for a common
    # label. Taking the label's notes and sorting them reads every one:
    # quick for a rare label. A step of the walk costs about a tenth of
    # reading a note, so walk when it should take fewer than ten steps per
    # note the label has. (CROSS JOIN fixes the order SQLite joins in.)
    if (offset + limit) * live < 10 * total * total:
        tables = "notes n CROSS JOIN note_labels nl ON nl.note_id = n.id"
    else:
        tables = "note_labels nl CROSS JOIN notes n ON n.id = nl.note_id"
    rows = db.execute(
        f"SELECT n.* FROM {tables} WHERE n.deleted_at IS NULL AND nl.name = ? "
        f"{_FEED_ORDER} LIMIT ? OFFSET ?",
        (name, limit, offset),
    ).fetchall()
    return rows, total


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


def existing_note_ids(ids) -> set[int]:
    """Which of `ids` are notes that exist and aren't in Trash, i.e. which
    [[refs]] render as links rather than ghosts. Asks about just these ids
    -- the notes on screen reference a handful -- rather than every note."""
    db = get_db()
    found: set[int] = set()
    for chunk in _chunks(ids):
        found.update(
            r["id"] for r in db.execute(
                f"SELECT id FROM notes WHERE deleted_at IS NULL AND id IN ({_marks(chunk)})", chunk
            )
        )
    return found


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
def _linked_from_live_note(note_id_sql: str) -> str:
    """SQL that's true when a note not in Trash links to `note_id_sql`."""
    return f"""EXISTS (
        SELECT 1 FROM note_links l JOIN notes src ON src.id = l.from_note_id
        WHERE l.to_note_id = {note_id_sql} AND src.deleted_at IS NULL)"""


_ORPHANS_FROM = f"""
    FROM notes n
    WHERE n.deleted_at IS NULL
      AND NOT EXISTS (SELECT 1 FROM note_links WHERE from_note_id = n.id)
      AND NOT {_linked_from_live_note("n.id")}
"""


def note_has_connections(note_id: int) -> bool:
    row = get_db().execute(
        f"""
        SELECT EXISTS (SELECT 1 FROM note_links WHERE from_note_id = ?)
            OR {_linked_from_live_note("?")} AS connected
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
        "SELECT COUNT(*) FROM notes WHERE deleted_at IS NULL AND (sort_date, id) > (?, ?)",
        (row["sort_date"], note_id),
    ).fetchone()[0]


_WITH_ATTACHMENTS_FROM = """
    FROM notes n
    WHERE n.deleted_at IS NULL
      AND EXISTS (SELECT 1 FROM note_attachments na WHERE na.note_id = n.id)
"""


def get_orphan_notes_page(limit: int, offset: int):
    return _paged_notes(_ORPHANS_FROM, (), limit, offset)


def get_notes_with_attachments_page(limit: int, offset: int):
    return _paged_notes(_WITH_ATTACHMENTS_FROM, (), limit, offset)


def get_attachment_counts(note_ids) -> dict:
    """note_id -> number of attachments, for those of `note_ids` that have
    any. Feeds the "N files" badge: one query for a page of cards, about
    just those cards."""
    db = get_db()
    counts: dict = {}
    for chunk in _chunks(note_ids):
        counts.update(
            (r["note_id"], r["c"]) for r in db.execute(
                f"SELECT note_id, COUNT(*) AS c FROM note_attachments "
                f"WHERE note_id IN ({_marks(chunk)}) GROUP BY note_id",
                chunk,
            )
        )
    return counts


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


def reindex() -> int:
    """Rebuild everything derived from note text -- labels, links and the
    search index -- from scratch, in one transaction, and return how many
    notes there are. Saving keeps all of it current, so nothing calls this
    automatically; it's `flask reindex`, for after the rules for reading
    labels or links change, or to compact a search index that has grown."""
    db = get_db()
    rows = db.execute("SELECT id, body FROM notes").fetchall()
    with db:
        db.execute("DELETE FROM note_labels")
        db.execute("DELETE FROM note_links")
        db.executemany(
            "INSERT INTO note_labels (note_id, name) VALUES (?, ?)",
            [(r["id"], name) for r in rows for name in md.extract_labels(r["body"])],
        )
        db.executemany(
            "INSERT INTO note_links (from_note_id, to_note_id) VALUES (?, ?)",
            [(r["id"], ref) for r in rows for ref in md.extract_note_refs(r["body"])],
        )
        # FTS5's own rebuild from the notes table, which also resets the
        # index's statistics.
        db.execute("INSERT INTO notes_fts (notes_fts) VALUES ('rebuild')")
    return len(rows)


# Label counts leave out notes in Trash. Rather than look up every labelled
# note to see whether it's in Trash, they count all of a label's rows
# straight from the label index and subtract those of notes in Trash,
# found through the small Trash index -- 5 ms on ten years of notes, where
# checking each note took over 100.
_TRASHED_LABELS = """
    FROM notes n CROSS JOIN note_labels nl ON nl.note_id = n.id
    WHERE n.deleted_at IS NOT NULL
"""


def get_labels_with_counts():
    """Every label used by at least one note not in Trash, with how many
    such notes use it, most-used first."""
    return get_db().execute(
        f"""
        SELECT name, SUM(c) AS count FROM (
            SELECT name, COUNT(*) AS c FROM note_labels GROUP BY name
            UNION ALL
            SELECT nl.name, -COUNT(*) {_TRASHED_LABELS} GROUP BY nl.name
        )
        GROUP BY name HAVING SUM(c) > 0
        ORDER BY count DESC, name ASC
        """
    ).fetchall()


def label_note_count(name: str) -> int:
    """How many notes not in Trash carry the label `name` (lowercase)."""
    return get_db().execute(
        f"""
        SELECT (SELECT COUNT(*) FROM note_labels WHERE name = ?)
             - (SELECT COUNT(*) {_TRASHED_LABELS} AND nl.name = ?)
        """,
        (name, name),
    ).fetchone()[0]


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


def get_backlink_counts(note_ids) -> dict:
    """note_id -> number of notes not in Trash referencing it, for those of
    `note_ids` that have any. Counts the same thing get_backlinks() lists,
    so a card's badge always matches the list you see when you expand or
    open that note."""
    db = get_db()
    counts: dict = {}
    for chunk in _chunks(note_ids):
        counts.update(
            (r["note_id"], r["c"]) for r in db.execute(
                f"""
                SELECT l.to_note_id AS note_id, COUNT(*) AS c
                FROM note_links l
                JOIN notes n ON n.id = l.from_note_id
                WHERE n.deleted_at IS NULL AND l.to_note_id IN ({_marks(chunk)})
                GROUP BY l.to_note_id
                """,
                chunk,
            )
        )
    return counts


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


def _live_links_touching(db, note_ids) -> set[tuple[int, int]]:
    """Every (from, to) link with either end in `note_ids`, counting only
    links written in notes that aren't in Trash."""
    links: set[tuple[int, int]] = set()
    for chunk in _chunks(note_ids):
        marks = _marks(chunk)
        links.update(
            (r[0], r[1]) for r in db.execute(
                f"""
                SELECT l.from_note_id, l.to_note_id FROM note_links l
                JOIN notes nf ON nf.id = l.from_note_id
                WHERE nf.deleted_at IS NULL AND l.from_note_id IN ({marks})
                UNION
                SELECT l.from_note_id, l.to_note_id FROM note_links l
                JOIN notes nf ON nf.id = l.from_note_id
                WHERE nf.deleted_at IS NULL AND l.to_note_id IN ({marks})
                """,
                chunk + chunk,
            )
        )
    return links


def get_graph_data(center_id: int, hops: int = 1):
    """The graph around one note: every note within `hops` links of it
    (following links in either direction), and the links among them.
    There is deliberately no whole-collection graph; a graph always
    belongs to a note.

    Works outward from the note one hop at a time, asking only for the
    links of the notes just reached, rather than loading every link in
    the database."""
    db = get_db()
    visited = {center_id}
    frontier = {center_id}
    links: set[tuple[int, int]] = set()
    for _ in range(max(hops, 0)):
        touching = _live_links_touching(db, frontier)
        links |= touching
        frontier = {n for link in touching for n in link} - visited
        visited |= frontier
        if not frontier:
            break
    # Links between two notes of the outermost ring belong in the picture
    # too, and the loop above never asked about those notes' links.
    if frontier:
        links |= _live_links_touching(db, frontier)
    node_ids = sorted(visited)
    chosen_edges = sorted((a, b) for a, b in links if a in visited and b in visited)

    real_notes: dict[int, str] = {}
    for chunk in _chunks(node_ids):
        real_notes.update(
            (r["id"], r["body"]) for r in db.execute(
                f"SELECT id, body FROM notes WHERE deleted_at IS NULL AND id IN ({_marks(chunk)})",
                chunk,
            )
        )

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
    where_sql = f"WHERE {where}" if where else ""
    select_sql = f"""
        SELECT a.id, a.kind, a.note_id, a.created_at, a.updated_at, a.save_count,
               a.detail, n.body AS note_body, n.deleted_at AS note_deleted_at
        FROM activity a
        JOIN notes n ON n.id = a.note_id
        {where_sql}
        ORDER BY a.updated_at DESC, a.id DESC
    """
    # Every entry belongs to a note that exists (entries go with their
    # note), so counting needs no join.
    return _paged(select_sql, f"SELECT COUNT(*) FROM activity a {where_sql}", (), limit, offset)


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


