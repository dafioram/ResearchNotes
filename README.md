# Research Notes

A single-user, no-auth Flask + SQLite app for atomic research notes,
built around the [Zettelkasten method](https://en.wikipedia.org/wiki/Zettelkasten):
short, linkable, taggable notes rather than long documents.

Built for a desktop browser (about 1280px wide and up). There's no phone
layout. Everything works offline except the web fonts, which fall back to
local fonts.

The full design record, including the reasoning behind each decision, is
in [spec.md](spec.md).

## Features

- **Feed** of all notes, most recent by *sort date* first, 50 per page
  (numbered pages, not infinite scroll). Each entry shows a stamped header
  (note number, sort date, line count, plus file and backlink counts when
  non-zero) and a snippet that is the rendered markdown of just the first
  line. Click a card to expand it in place to the full rendered note, its
  attachments and its backlinks; its Edit button opens the note straight
  in Edit mode.
- **Search** on the feed, full-text over note bodies (SQLite FTS5, with
  stemming -- searching "research" also finds "researching"). Typing a
  number additionally pulls in any note whose *id* starts with that
  number (e.g. "100" also surfaces notes 100, 1000, 1005, ...) and puts
  those at the top, ahead of the ordinary text matches. Combines with the
  label filter below. The Orphans view uses the same expandable-card feed
  component as the main feed (but isn't itself searched or filtered).
- **Desktop layout**: a sticky left sidebar (search and labels on the
  feed; page details and actions elsewhere) beside a reading-width main
  column.
- **One page per note** with a **View / Edit switch** that never leaves
  the page. View renders the note; Edit is the raw markdown in a plain
  textarea that fills the window, with the date, buttons and label picker
  in the sidebar. **Save** / **Ctrl+S** (⌘S on a Mac) save and keep you
  editing; **Done** (or flipping to View) saves and shows View; **Cancel**
  discards. Leaving with unsaved changes asks first. Links to a note open
  it in View; the feed's Edit button opens it in Edit.
- **New notes**: the first save creates the note and keeps you writing
  (attachments available right away); **Done** returns to the feed on the
  page where the new note sits, scrolled to it with a brief highlight.
- **Hand-rolled markdown subset** (see below) -- no third-party markdown
  library. Deliberately not full CommonMark.
- **`#labels`** anywhere in a note's text (`#label` needs no space; a
  header needs one, e.g. `# Title`; `##nospace` is inert -- neither a
  header nor a label).
- **`[[note-number]]`** references to other notes, from anywhere in the
  text. Renders as a link if the note exists, or a dashed "ghost" marker
  if it doesn't (broken references are visible at a glance, never silently
  swallowed).
- **Backlinks** -- the notes that reference a note, listed on its own
  page and in its inline expansion on the feed (first 10 there, with a
  link to the full list), and counted in a badge on every card. Each
  backlink shows the linking note's header plus the passage around the
  `[[link]]` itself, so you can see why it links here.
- **Label list** in the feed's sidebar with usage counts; click a label to
  filter the feed to just those notes.
- **Graph of a note** (rendered with Cytoscape.js, bundled so it works
  offline), full window width: the notes related to that note, out to
  1–5 hops. Reached from the note's page only; there's no graph of
  everything. Greyed out for a note with no links. Scroll to zoom, hover
  a node for its title. Notes referenced but not found appear as dashed
  ghost nodes; edges pointing at them are colored red.
- **Orphans view** -- notes with no incoming or outgoing `[[links]]`, useful
  for spotting notes that never got integrated into your web of ideas.
- **Random note** button, in the spirit of re-reading old notes to spark
  new connections.
- **History** -- an activity log of what you've done, newest first:
  "Created No. 11", "Edited No. 6: +6 / −7 lines, added #memory, now
  links to [[5]]", deleted/restored, files attached/removed. Saves to the
  same note within 15 minutes are grouped into one entry. Filter by kind
  in the sidebar. It records what changed, not the old text; there's no
  version history.
- **Soft delete** with a Trash view to restore notes.
- **Attachments**, stored in their own table (many-to-many with notes, so
  one uploaded file can be linked from several notes) and content-addressed
  by SHA-256 hash so identical files are only stored once -- uploading the
  same file to a second note links it instead of duplicating storage, no
  separate "attach an existing file" picker needed. No inline image
  markdown -- attachments show as a plain list on the note. On the edit
  page, adding or removing a file happens in place, without reloading,
  so unsaved text is never lost.
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
| `PAGE_SIZE`  | `50`    | Notes per page on the feed, Orphans and Attachments  |
| `TZ`         | UTC     | Docker only: time zone for dates, e.g. `America/New_York` |

## Project layout

```
run.py              standalone launcher (reads .env, starts the server)
schema.sql          SQLite schema, applied at every startup (idempotent)
spec.md             design record: what the app does and why
app/
  __init__.py       Flask app factory
  db.py             all database access, including recording activity
  activity.py       what an edit changed; wording for the History page
  markdown.py       the hand-rolled markdown/label/ref parser
  routes.py         routes
  templates/        Jinja templates (_macros.html holds shared pieces)
  static/           CSS and small vanilla-JS files
    vendor/         Cytoscape.js, bundled for offline use (MIT)
tests/              pytest suite (parser unit tests + route integration tests)
data/               SQLite DB + uploaded files (gitignored; created on first run)
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
