# Research Notes

A single-user, no-auth Flask + SQLite app for atomic research notes,
built around the [Zettelkasten method](https://en.wikipedia.org/wiki/Zettelkasten):
short, linkable, taggable notes rather than long documents.

## Features

- **Feed** of all notes, most recent by *sort date* first. Each entry shows
  a stamped header (note number, sort date, line count) and a snippet that
  is the rendered markdown of just the first line.
- **Single note view** renders the full note as HTML; **edit view** is the
  raw markdown source in a plain textarea.
- **Hand-rolled markdown subset** (see below) -- no third-party markdown
  library. Deliberately not full CommonMark.
- **`#labels`** anywhere in a note's text (`#label` needs no space; a
  header needs one, e.g. `# Title`; `##nospace` is inert -- neither a
  header nor a label).
- **`[[note-number]]`** references to other notes, from anywhere in the
  text. Renders as a link if the note exists, or a dashed "ghost" marker
  if it doesn't (broken references are visible at a glance, never silently
  swallowed).
- **Backlinks** -- every note view lists the notes that reference it.
- **Label cloud** on the feed with usage counts; click a label to filter
  the feed to just those notes.
- **Two graph views** (rendered with Cytoscape.js): a global graph of every
  linked note, and a local/ego graph centered on one note out to N hops.
  Notes referenced but not found appear as dashed ghost nodes; edges
  pointing at them are colored red.
- **Orphans view** -- notes with no incoming or outgoing `[[links]]`, useful
  for spotting notes that never got integrated into your web of ideas.
- **Random note** button, in the spirit of re-reading old notes to spark
  new connections.
- **Soft delete** with a Trash view to restore notes.
- **Attachments**, stored in their own table (many-to-many with notes, so
  one uploaded file can be linked from several notes) and content-addressed
  by SHA-256 hash so identical files are only stored once. No inline image
  markdown -- attachments show as a plain list on the note.
- Structure notes / MOCs and Folgezettel-style numbering are intentionally
  **not** special-cased -- a structure note is just an ordinary note whose
  body links to others.

## Markdown subset

Supported: `# .. ######` headers (space required), `**bold**` / `__bold__`,
`*italic*` / `_italic_`, `~~strikethrough~~`, `` `inline code` ``, fenced
code blocks, `[text](url)` links, `- ` / `* ` unordered lists, `1. ` ordered
lists, `> ` blockquotes, `---`/`***`/`___` horizontal rules, and paragraphs
(a single newline becomes `<br>`; a blank line starts a new paragraph).

Not supported by design: image syntax (`![]()`), raw HTML passthrough
(everything is escaped), nested/complex inline formatting inside link text,
and tables. Raw text always wins over ambiguous or malformed syntax rather
than throwing an error.

Known parser limitations (it's a small regex-based subset parser, not a
spec-compliant implementation): a literal `#word` inside running text (e.g.
a URL fragment or hex color) will be read as a label; a stray single `*`
used for multiplication next to another `*` on the same line can trigger
unintended italics. Wrap either in `` `code` `` to opt out.

## Getting started

### Option A: plain Python

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # edit PORT if you want something other than 5000
python run.py
```

Then open `http://localhost:5000` (or whatever `PORT` you set).

### Option B: Docker

```bash
cp .env.example .env             # edit PORT if you want something other than 5000
docker compose up --build
```

Notes and uploaded files persist in `./data` on the host (mounted into the
container), so they survive rebuilds and restarts.

To run with plain `docker` instead of compose:

```bash
docker build -t research-notes .
docker run -p 5000:5000 -e PORT=5000 -v "$(pwd)/data:/app/data" research-notes
```

## Configuration (`.env`)

| Variable     | Default | Purpose                                             |
|--------------|---------|------------------------------------------------------|
| `PORT`       | `5000`  | Port the app listens on (and the Docker host mapping) |
| `HOST`       | `0.0.0.0` | Interface to bind to                                |
| `SECRET_KEY` | random  | Signs the flash-message cookie; fine to leave unset  |
| `DATA_DIR`   | `./data`| Where `notes.db` and `uploads/` live                 |

## Project layout

```
run.py                  standalone launcher (reads .env, starts the server)
schema.sql               SQLite schema, applied once at startup
app/
  __init__.py             Flask app factory
  db.py                   all database access
  markdown.py             the hand-rolled markdown/label/ref parser
  routes.py                routes
  templates/                Jinja templates
  static/                    CSS, and small vanilla-JS files
tests/                    pytest suite (parser unit tests + route integration tests)
data/                     SQLite DB + uploaded files (gitignored; created on first run)
```

Attachments are stored on disk under
`data/uploads/<hash[0:2]>/<hash[2:4]>/<hash><ext>`, addressed by the SHA-256
of their contents.

## Running the tests

```bash
source .venv/bin/activate
pip install -r requirements.txt pytest
pytest tests/ -v
```
