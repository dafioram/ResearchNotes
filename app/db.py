import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import click
from flask import current_app, g

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
    conn.execute("PRAGMA foreign_keys = ON")
    schema_path = Path(app.root_path).parent / "schema.sql"
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)


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
    db.commit()
    _sync_note_metadata(note_id, body)
    db.commit()
    return note_id


def update_note(note_id: int, body: str, sort_date: str) -> None:
    db = get_db()
    db.execute(
        "UPDATE notes SET body = ?, sort_date = ?, updated_at = ? WHERE id = ?",
        (body, sort_date, now_iso(), note_id),
    )
    db.commit()
    _sync_note_metadata(note_id, body)
    db.commit()


def get_note(note_id: int, include_deleted: bool = False):
    db = get_db()
    row = db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        return None
    if not include_deleted and row["deleted_at"] is not None:
        return None
    return row


def list_notes(label: str | None = None):
    db = get_db()
    if label:
        rows = db.execute(
            """
            SELECT n.* FROM notes n
            JOIN note_labels nl ON nl.note_id = n.id
            JOIN labels l ON l.id = nl.label_id
            WHERE n.deleted_at IS NULL AND l.name = ?
            ORDER BY n.sort_date DESC, n.id DESC
            """,
            (label.lower(),),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM notes WHERE deleted_at IS NULL ORDER BY sort_date DESC, id DESC"
        ).fetchall()
    return rows


def list_deleted_notes():
    db = get_db()
    return db.execute(
        "SELECT * FROM notes WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
    ).fetchall()


def soft_delete_note(note_id: int) -> None:
    db = get_db()
    db.execute("UPDATE notes SET deleted_at = ? WHERE id = ?", (now_iso(), note_id))
    db.commit()


def restore_note(note_id: int) -> None:
    db = get_db()
    db.execute("UPDATE notes SET deleted_at = NULL WHERE id = ?", (note_id,))
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


def get_orphan_notes():
    db = get_db()
    return db.execute(
        """
        SELECT n.* FROM notes n
        WHERE n.deleted_at IS NULL
          AND n.id NOT IN (SELECT from_note_id FROM note_links)
          AND n.id NOT IN (SELECT to_note_id FROM note_links)
        ORDER BY n.sort_date DESC, n.id DESC
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# Labels & links syncing
# ---------------------------------------------------------------------------

def _sync_note_metadata(note_id: int, body: str) -> None:
    """Re-derive labels and note-links from a note's body and update the
    join tables to match exactly (adds new rows, removes stale ones)."""
    db = get_db()
    label_names = md.extract_labels(body)
    ref_ids = md.extract_note_refs(body)

    label_ids = set()
    for name in label_names:
        db.execute("INSERT OR IGNORE INTO labels (name) VALUES (?)", (name,))
        row = db.execute("SELECT id FROM labels WHERE name = ?", (name,)).fetchone()
        label_ids.add(row["id"])

    current_label_ids = {
        r["label_id"]
        for r in db.execute(
            "SELECT label_id FROM note_labels WHERE note_id = ?", (note_id,)
        ).fetchall()
    }
    for lid in label_ids - current_label_ids:
        db.execute(
            "INSERT INTO note_labels (note_id, label_id) VALUES (?, ?)", (note_id, lid)
        )
    for lid in current_label_ids - label_ids:
        db.execute(
            "DELETE FROM note_labels WHERE note_id = ? AND label_id = ?", (note_id, lid)
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

    # Labels that are no longer used by any note are pruned so the label
    # cloud doesn't accumulate dead tags.
    db.execute(
        """
        DELETE FROM labels
        WHERE id NOT IN (SELECT DISTINCT label_id FROM note_labels)
        """
    )


def get_labels_with_counts():
    db = get_db()
    return db.execute(
        """
        SELECT l.name AS name, COUNT(*) AS count
        FROM labels l
        JOIN note_labels nl ON nl.label_id = l.id
        JOIN notes n ON n.id = nl.note_id
        WHERE n.deleted_at IS NULL
        GROUP BY l.name
        ORDER BY count DESC, l.name ASC
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


def get_graph_data(center_id: int | None = None, hops: int = 1):
    db = get_db()
    edges = [(r["from_note_id"], r["to_note_id"]) for r in _graph_edges(db)]

    if center_id is None:
        node_ids = {n for e in edges for n in e}
        chosen_edges = edges
    else:
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
    db.execute(
        "INSERT OR IGNORE INTO note_attachments (note_id, attachment_id) VALUES (?, ?)",
        (note_id, attachment_id),
    )
    db.commit()


def unlink_attachment(note_id: int, attachment_id: int) -> None:
    db = get_db()
    db.execute(
        "DELETE FROM note_attachments WHERE note_id = ? AND attachment_id = ?",
        (note_id, attachment_id),
    )
    db.commit()


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


def search_attachments(query: str, limit: int = 15):
    db = get_db()
    return db.execute(
        "SELECT * FROM attachments WHERE filename LIKE ? ORDER BY created_at DESC LIMIT ?",
        (f"%{query}%", limit),
    ).fetchall()
