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

CREATE TABLE IF NOT EXISTS labels (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS note_labels (
    note_id     INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    label_id    INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
    PRIMARY KEY (note_id, label_id)
);

CREATE INDEX IF NOT EXISTS idx_note_labels_label ON note_labels (label_id);

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
