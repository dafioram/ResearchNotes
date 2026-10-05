-- Research Notes schema
-- SQLite. Run at every startup: it only creates what's missing, and never
-- reads or rewrites existing data. (`flask reindex` rebuilds the derived
-- tables and the search index from note text, when that's ever wanted.)

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    body        TEXT NOT NULL DEFAULT '',
    sort_date   TEXT NOT NULL,          -- YYYY-MM-DD, user-set
    created_at  TEXT NOT NULL,          -- ISO datetime, set once
    updated_at  TEXT NOT NULL,          -- ISO datetime, bumped on every save
    deleted_at  TEXT                    -- ISO datetime, NULL unless soft-deleted
);

-- Feed order over the notes not in Trash. Every listing says "deleted_at
-- IS NULL", so it reads its page straight off this index and stops.
CREATE INDEX IF NOT EXISTS idx_notes_live ON notes (sort_date DESC, id DESC)
    WHERE deleted_at IS NULL;
-- Trash, most recently deleted first.
CREATE INDEX IF NOT EXISTS idx_notes_trash ON notes (deleted_at)
    WHERE deleted_at IS NOT NULL;
-- Replaced by the two above. With an index on deleted_at alone, SQLite read
-- and sorted every note to show a page of 50 (nearly every note matches
-- "deleted_at IS NULL"), so it mustn't linger in an existing database.
DROP INDEX IF EXISTS idx_notes_deleted;
DROP INDEX IF EXISTS idx_notes_sort_date;

-- Which #labels each note uses. A derived index of notes.body, re-synced on
-- every save: a label "exists" exactly when at least one note uses it, so
-- there is no separate labels table and nothing to clean up. Names are
-- stored lowercase (extraction lowercases them). Same shape as note_links.
-- Like note_links, it covers only notes not in Trash: moving a note to
-- Trash removes its rows and restoring it re-reads them from its text, so
-- nothing that reads these tables has to check for Trash.
-- WITHOUT ROWID: the primary key is the table, rather than a table plus a
-- copy of it as an index -- about 40% smaller. (Applies to new databases;
-- an existing one keeps its tables, which work the same.)
CREATE TABLE IF NOT EXISTS note_labels (
    note_id     INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    name        TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (note_id, name)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_note_labels_name ON note_labels (name);

-- Explicit [[note-id]] references. to_note_id has no FK constraint since it
-- may point at a note number that does not (yet, or ever) exist -- that is
-- rendered as a "ghost" reference rather than treated as an error.
CREATE TABLE IF NOT EXISTS note_links (
    from_note_id    INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    to_note_id      INTEGER NOT NULL,
    PRIMARY KEY (from_note_id, to_note_id)
) WITHOUT ROWID;

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

-- Triggers keep the index to exactly the notes not in Trash, in step with
-- their text. A save that doesn't change the text (a new sort date, say)
-- leaves the index alone. ("delete" has to be given the text as it was
-- indexed, hence old.body.)
CREATE TRIGGER IF NOT EXISTS notes_fts_insert AFTER INSERT ON notes
WHEN new.deleted_at IS NULL BEGIN
    INSERT INTO notes_fts (rowid, body) VALUES (new.id, new.body);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_edit AFTER UPDATE OF body ON notes
WHEN old.deleted_at IS NULL AND new.deleted_at IS NULL AND old.body IS NOT new.body BEGIN
    INSERT INTO notes_fts (notes_fts, rowid, body) VALUES ('delete', old.id, old.body);
    INSERT INTO notes_fts (rowid, body) VALUES (new.id, new.body);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_trash AFTER UPDATE OF deleted_at ON notes
WHEN old.deleted_at IS NULL AND new.deleted_at IS NOT NULL BEGIN
    INSERT INTO notes_fts (notes_fts, rowid, body) VALUES ('delete', old.id, old.body);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_restore AFTER UPDATE OF deleted_at ON notes
WHEN old.deleted_at IS NOT NULL AND new.deleted_at IS NULL BEGIN
    INSERT INTO notes_fts (rowid, body) VALUES (new.id, new.body);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_remove AFTER DELETE ON notes
WHEN old.deleted_at IS NULL BEGIN
    INSERT INTO notes_fts (notes_fts, rowid, body) VALUES ('delete', old.id, old.body);
END;

-- Replaced by the triggers above, which leave notes in Trash out of the
-- index and skip saves that don't change the text.
DROP TRIGGER IF EXISTS notes_fts_ai;
DROP TRIGGER IF EXISTS notes_fts_ad;
DROP TRIGGER IF EXISTS notes_fts_au;

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
