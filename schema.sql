-- Research Notes schema
-- SQLite. Applied once at startup if tables are missing.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    body        TEXT NOT NULL DEFAULT '',
    sort_date   TEXT NOT NULL,          -- YYYY-MM-DD, user-set
    created_at  TEXT NOT NULL,          -- ISO datetime, set once
    updated_at  TEXT NOT NULL,          -- ISO datetime, bumped on every save
    deleted_at  TEXT                    -- ISO datetime, NULL unless soft-deleted
);

CREATE INDEX IF NOT EXISTS idx_notes_sort_date ON notes (sort_date, id);
CREATE INDEX IF NOT EXISTS idx_notes_deleted ON notes (deleted_at);

-- Which #labels each note uses. A derived index of notes.body, re-synced on
-- every save: a label "exists" exactly when at least one note uses it, so
-- there is no separate labels table and nothing to clean up. Names are
-- stored lowercase (extraction lowercases them). Same shape as note_links.
-- (Older databases had a labels table plus note_labels(note_id, label_id);
-- init_db migrates them before this file runs.)
CREATE TABLE IF NOT EXISTS note_labels (
    note_id     INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    name        TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (note_id, name)
);

CREATE INDEX IF NOT EXISTS idx_note_labels_name ON note_labels (name);

-- Explicit [[note-id]] references. to_note_id has no FK constraint since it
-- may point at a note number that does not (yet, or ever) exist -- that is
-- rendered as a "ghost" reference rather than treated as an error.
CREATE TABLE IF NOT EXISTS note_links (
    from_note_id    INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    to_note_id      INTEGER NOT NULL,
    PRIMARY KEY (from_note_id, to_note_id)
);

CREATE INDEX IF NOT EXISTS idx_note_links_to ON note_links (to_note_id);

-- Content-addressed attachments. Many-to-many with notes so the same file
-- (by hash) can be reused across multiple notes without duplicating bytes.
CREATE TABLE IF NOT EXISTS attachments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hash        TEXT NOT NULL UNIQUE,
    filename    TEXT NOT NULL,          -- original filename, for display
    extension   TEXT NOT NULL DEFAULT '',
    mime_type   TEXT NOT NULL,
    size        INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_attachments (
    note_id         INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    attachment_id   INTEGER NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    PRIMARY KEY (note_id, attachment_id)
);

CREATE INDEX IF NOT EXISTS idx_note_attachments_attachment ON note_attachments (attachment_id);

-- Full-text search over note bodies. An "external content" table: it
-- doesn't store the text itself, just the index, keyed to notes.id via
-- rowid. Porter stemming gives basic recall (e.g. "research" also turns
-- up "researching"). Punctuation used by #labels and [[refs]] is treated
-- as a token boundary by unicode61, so e.g. searching "zettelkasten"
-- still finds a note that only contains "#zettelkasten".
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    body,
    content='notes',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- Triggers keep the index in sync with notes as they're written. A
-- soft delete is just an UPDATE (deleted_at changes, body doesn't) --
-- deleted notes stay indexed but are excluded at query time by filtering
-- on deleted_at, same as everywhere else in this schema.
CREATE TRIGGER IF NOT EXISTS notes_fts_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts (rowid, body) VALUES (new.id, new.body);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts (notes_fts, rowid, body) VALUES ('delete', old.id, old.body);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts (notes_fts, rowid, body) VALUES ('delete', old.id, old.body);
    INSERT INTO notes_fts (rowid, body) VALUES (new.id, new.body);
END;

-- Backfill for notes that existed before the FTS table did (or a brand
-- new database's first startup). Safe to re-run: rowids already indexed
-- are skipped rather than erroring.
INSERT OR IGNORE INTO notes_fts (rowid, body) SELECT id, body FROM notes;

-- Activity log: what the person did, newest first on the History page.
-- One row per event; saves to the same note close together are merged
-- into a single 'edited' row (an editing session, see db.py).
--   kind: created | edited | deleted | restored | attached | detached
--   detail: JSON -- for edits the line counts and label/link/date changes,
--           for attachments the filename
--   base_body / base_sort_date: the note as it was when an editing session
--           began, kept only while that session can still be extended so
--           merged saves are measured against the true starting point.
--           Cleared as soon as the session closes; never shown.
CREATE TABLE IF NOT EXISTS activity (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,
    note_id         INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,          -- first save / event time (UTC)
    updated_at      TEXT NOT NULL,          -- last save merged in (UTC)
    save_count      INTEGER NOT NULL DEFAULT 1,
    touches_links   INTEGER NOT NULL DEFAULT 0,
    detail          TEXT NOT NULL DEFAULT '{}',
    base_body       TEXT,
    base_sort_date  TEXT
);

CREATE INDEX IF NOT EXISTS idx_activity_time ON activity (updated_at, id);
CREATE INDEX IF NOT EXISTS idx_activity_note ON activity (note_id, updated_at);

-- Backfill for notes that existed before the activity log did, so History
-- starts out reflecting what's already there. Safe to re-run: only notes
-- with no activity at all get a 'created' row, and only deleted notes
-- without a 'deleted' row get one.
INSERT INTO activity (kind, note_id, created_at, updated_at)
SELECT 'created', n.id, n.created_at, n.created_at
FROM notes n
WHERE NOT EXISTS (SELECT 1 FROM activity a WHERE a.note_id = n.id);

INSERT INTO activity (kind, note_id, created_at, updated_at)
SELECT 'deleted', n.id, n.deleted_at, n.deleted_at
FROM notes n
WHERE n.deleted_at IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM activity a WHERE a.note_id = n.id AND a.kind = 'deleted');
