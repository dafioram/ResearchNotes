# Research Notes

[![Tests](https://github.com/dafioram/ResearchNotes/actions/workflows/tests.yml/badge.svg)](https://github.com/dafioram/ResearchNotes/actions/workflows/tests.yml)

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
  (numbered pages, not infinite scroll), under day headings ("Today",
  "Yesterday", "Monday 5 October"), with **Jump to** a month in the
  sidebar. Each entry shows a stamped header
  (note number, sort date, line count, plus file and backlink counts when
  non-zero) and a snippet that is the rendered markdown of just the first
  line. Click a card to expand it in place to the full rendered note, its
  attachments and its backlinks; its Edit button opens the note straight
  in Edit mode.
- **Search** from the box in the top bar, on every page. Plain words are
  enough (full-text over note bodies, SQLite FTS5, with stemming --
  "research" also finds "researching"); each result shows the passage
  that matched, highlighted. When you want more:

  | Type | To find notes |
  |---|---|
  | `"spaced repetition"` | with that exact phrase |
  | `retriev*` | with a word starting "retriev" |
  | `-flashcards` | without that word (or `-"a phrase"`) |
  | `#learning #memory` | with either label (any of the labels typed) |
  | `+#draft` / `-#draft` | that must have / mustn't have the label |
  | `#physics-*` | with `#physics` or any `#physics-...` label |
  | `is:unlinked` | with no `[[links]]` in or out |
  | `has:file` | with an attachment |
  | `has:later` | with a `[[later]]` link still to fill in |
  | `after:2025-03 before:2026` | by sort date (`YYYY`, `YYYY-MM` or `YYYY-MM-DD`) |
  | `1234` | numbered 1234, 12340, ... first, then ones mentioning it |

  Everything combines (`#memory is:unlinked after:2025`), and the feed's
  sidebar has the same list under "Search tips".
- **Desktop layout** that uses the width of the window (up to 1760px): a
  sticky left sidebar (views and labels on the feed; page details and
  actions elsewhere) beside a main column that takes the rest. Paragraphs
  of note text wrap at about 100 characters to stay readable.
- **One page per note** with a **View / Edit switch** that never leaves
  the page. View renders the note; Edit is the raw markdown in a plain
  textarea that fills the window, with the date and buttons in the
  sidebar. **Save** / **Ctrl+S** (⌘S on a Mac) save and keep you
  editing; **Done** (or flipping to View) saves and shows View; **Cancel**
  undoes your changes. Leaving with unsaved changes asks first. Links to a note open
  it in View; the feed's Edit button opens it in Edit.
- **Autosave and drafts**: once a note exists it saves itself 3 seconds
  after you stop typing (Save / Ctrl+S still save at once). Until text is
  saved it's also kept in your browser, so a crash or closed tab can't
  lose it: opening the note (or New note) again offers to restore it.
  **Cancel** undoes everything since you opened Edit, even if autosave
  had already saved some of it.
- **New notes**: the first save creates the note and keeps you writing
  (attachments available right away); **Done** returns to the feed on the
  page where the new note sits, scrolled to it with a brief highlight.
- **Hand-rolled markdown subset** (see below) -- no third-party markdown
  library. Deliberately not full CommonMark.
- **`#labels`** anywhere in a note's text: a `#` at the start of a line
  or after a space, then a letter (any language), then letters, digits,
  `.`, `-` or `_`; trailing `.`, `-` and `_` aren't included, so
  "about #physics." is `#physics`. `#3`, `C#` and URL fragments aren't
  labels. (`#label` needs no space; a header needs one, e.g. `# Title`;
  `##nospace` is inert -- neither a header nor a label.) Typing `#` and a
  letter in the editor suggests the labels you already use, most-used
  first; Tab or Enter completes one.
- **`[[note-number]]`** references to other notes, from anywhere in the
  text. You don't need to know the number: typing `[[` in the editor
  lists notes as you type a few words of a title (or a number), and
  picking one inserts `[[123]]`. A reference shows as the linked note's
  title (with its number small after it), or a dashed "ghost" marker if
  the note doesn't exist (broken references are visible at a glance,
  never silently swallowed).
- **`[[later]]`** (or `[[later: Bjork 1994]]`, with a hint) marks a link
  to fill in later, when the note doesn't exist yet. It's the last choice
  in the `[[` list, links nowhere, and `has:later` (the sidebar's *Links
  to fill in*) finds every note that still has one.
- **Backlinks** -- the notes that reference a note, listed on its own
  page and in its inline expansion on the feed (first 10 there, with a
  link to the full list), and counted in a badge on every card. Each
  backlink shows the linking note's header plus the passage around the
  `[[link]]` itself, so you can see why it links here.
- **Labels** in the feed's sidebar as compact chips with counts, ordered
  **Recent**, **Most used** or **A–Z** (your choice is remembered per
  browser), the first 36 then *Show all*, with a filter box. Labels that
  share a prefix -- `#physics-mechanics`, `#physics-quantum` -- collapse
  into one `#physics-*` chip that opens in place. Clicking a label adds
  `#label` to the search (clicking it again takes it out). Several labels
  show notes with any of them; `+#label` requires one, and labels combine
  with anything typed. A label inside a note
  links to the same search.
- **Graph of a note** (rendered with Cytoscape.js, bundled so it works
  offline), full window width: the notes related to that note, out to
  1–3 hops and at most 200 notes (when there are more, it shows the
  nearest and says how many it left out). Reached from the note's page
  only; there's no graph of everything. Greyed out for a note with no links. Scroll to zoom, hover
  a node for its title. Notes referenced but not found appear as dashed
  ghost nodes; edges pointing at them are colored red.
- **Views** in the feed's sidebar: *Unlinked notes* (`is:unlinked`, notes
  with no incoming or outgoing `[[links]]` -- ones that never got
  integrated into your web of ideas), *Notes with files* (`has:file`) and
  *Links to fill in* (`has:later`). All are just searches, so they combine with words and labels. The old
  `/orphans` and `/attachments` addresses redirect to them.
- **Random note** button, in the spirit of re-reading old notes to spark
  new connections.
- **History** -- an activity log of what you've done, newest first:
  "Created No. 11", "Edited No. 6: +6 / −7 lines, added #memory, now
  links to [[5]]", deleted/restored, files attached/removed. Saves to the
  same note within 15 minutes are grouped into one entry. Filter by kind
  in the sidebar.
- **Versions** -- each time you come back to edit a note, the text it had
  is kept: one version per sitting, however often you save. A note's
  *Versions* button lists them; each shows the old text and what's
  changed since, and **Restore** brings it back as a new edit (so the
  text it replaces is kept too, and a restore can be undone). About 1 MB
  a year at 10 notes a day.
- **Soft delete** with a Trash view (titles, pages, Restore, **Delete
  permanently** and **Empty Trash**) to restore notes. A note in Trash
  drops out of labels, links and search until it's restored.
- **Attachments**, stored in their own table (many-to-many with notes, so
  one uploaded file can be linked from several notes) and content-addressed
  by SHA-256 hash so identical files are only stored once -- uploading the
  same file to a second note links it instead of duplicating storage, no
  separate "attach an existing file" picker needed. Attachments show as a
  list on the note (with Insert and Copy, to put one into the text);
  images can also show in the text itself (see Images below). To attach,
  drop files anywhere on the note while editing, or paste a screenshot
  (several at once is fine; a new note is saved first); adding or
  removing a file happens in place, without reloading, so unsaved text
  is never lost. Images, PDFs, plain text, audio and
  video open in the browser; anything else (HTML and SVG included)
  downloads. A file is deleted from disk once no note uses it (removed
  from its last note, or its last note deleted permanently; a note in
  Trash still keeps its files).
- Structure notes / MOCs and Folgezettel-style numbering are intentionally
  **not** special-cased -- a structure note is just an ordinary note whose
  body links to others.

## Markdown subset

Supported: `# .. ######` headers (space required), `**bold**` / `__bold__`,
`*italic*` / `_italic_`, `~~strikethrough~~`, `` `inline code` ``, fenced
code blocks, `[text](url)` links (http, https, mailto or relative
addresses; other schemes such as `javascript:` stay plain text), bare
`https://...` addresses (linked as they are),
`- ` / `* ` unordered lists, `1. ` ordered
lists, `> ` blockquotes, `---`/`***`/`___` horizontal rules, and paragraphs
(a single newline becomes `<br>`; a blank line starts a new paragraph).

**Math**: `$E = mc^2$` inline and `$$ … $$` as a centered block (it can
span lines), drawn by KaTeX, bundled so it works offline. Prices are
left alone -- "$5 and $10" stays text -- and `\$` is a plain dollar sign.
Hover over a formula to see its TeX; bad TeX shows in red.

**Tables**: pipe tables -- a header row, a `|---|---:|` row (colons
align: `:---` left, `---:` right, `:---:` centered), then rows. Cells can
hold bold, code, math, links, `#labels` and `[[refs]]`; `\|` is a literal
pipe. Pasting cells copied from a spreadsheet, or comma-separated data,
makes a table for you (Ctrl+Z gives back the plain paste).

**Images**: drop an image (or paste a screenshot) while editing and it
goes into the text at the cursor as `![caption](/files/191ff6f6b235)` --
a stored file, by the start of its hash. Alone on a line it's a figure
with its caption below. Any note can show any stored image (Copy beside
an attachment gives you the text), and doing so attaches it to that note
too. Other files are only attached; `[name](/files/<hash>)` links to one.

Not supported by design: images from other websites, raw HTML passthrough
(everything is escaped), and nested/complex inline formatting inside link
text. Raw text always wins over ambiguous or malformed syntax rather
than throwing an error.

Known parser limitations (it's a small regex-based subset parser, not a
spec-compliant implementation): a hex color like `#fff` after a space will
be read as a label; a stray single `*`
used for multiplication next to another `*` on the same line can trigger
unintended italics; `__init__.py` reads as bold "init" (underscores
inside words, like `max_batch_size`, are fine). Wrap any of these in
`` `code` `` to opt out.

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
| `PAGE_SIZE`  | `50`    | Notes per page on the feed and in search results     |
| `TZ`         | UTC     | Docker only: time zone for dates, e.g. `America/New_York` |
| `ALLOWED_HOSTS` | none | Public domain names the app may be reached by (see below) |

## Network safety

There's no login, by design: run it on your own machine or a trusted
network. Two invisible checks stop *other web pages* from using a browser
on that network to reach the app (details in spec §14):

- Changes are only accepted from the app's own pages; a form or script on
  another site gets a 403.
- The app only answers to IP addresses, `localhost`, and local names
  such as `myserver`, `notes.lan` or `nas.local`. To reach it by a
  public domain name, add the name to `ALLOWED_HOSTS` in `.env`
  (comma-separated; `.example.com` allows every name under it).

## Rebuilding labels, links and search

All three are derived from the text of the notes not in Trash and kept
current as you save; startup never touches them. To rebuild them from
scratch, for example after an update that changes how labels are read
(or, once, on a database from before notes in Trash were left out):

```bash
flask --app app reindex                                      # plain Python, from this folder
docker compose exec research-notes flask --app app reindex   # Docker
```

Add `--vacuum` to also compact the database file afterwards. That
rewrites the whole file once, so a deduplicating backup copies it in
full that one time.

Files no note uses are deleted as they stop being used. Earlier versions
kept them; to clear out any left behind (once):

```bash
flask --app app prune-files                                      # plain Python
docker compose exec research-notes flask --app app prune-files   # Docker
```

## Project layout

```
run.py              standalone launcher (reads .env, starts the server)
schema.sql          SQLite schema; startup creates whatever's missing, nothing more
spec.md             design record: what the app does and why
app/
  __init__.py       Flask app factory
  db.py             all database access, including recording activity
  activity.py       what an edit changed; wording for the History page
  markdown.py       the hand-rolled markdown/label/ref parser
  routes.py         routes
  security.py       request guards: same-site changes, local addresses
  templates/        Jinja templates (_macros.html holds shared pieces)
  static/           CSS and small vanilla-JS files
    vendor/         Cytoscape.js and KaTeX, bundled for offline use (MIT)
tests/              pytest suite (parser unit tests + route integration tests)
  browser/          browser tests: the app driven in Chromium (pytest -m browser)
scripts/benchmark.py  scale check: times the main pages on ten years of notes
data/               SQLite DB + uploaded files (gitignored; created on first run)
```

Attachments are stored on disk under
`data/uploads/<hash[0:2]>/<hash[2:4]>/<hash><ext>`, addressed by the SHA-256
of their contents.

## Running the tests

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                      # everything but the browser tests, ~15 s
```

The **browser tests** (`tests/browser/`) run the app on a spare port with
a fresh database and drive it in Chromium: saving, Done and Cancel,
autosave and drafts, the `[[` and `#` suggestions, dropping files, the
feed's cards and label chips, the graph, Trash and versions. Any
JavaScript error on a page fails the test. They take about a minute:

```bash
playwright install chromium          # once (or set CHROMIUM_PATH to an existing Chromium)
pytest -m browser
```

Without Playwright or a Chromium they skip themselves. A failing browser
test leaves a screenshot of its page in `test-results/`.

**CI** (`.github/workflows/tests.yml`) runs on every pull request and every
push to `main`: the fast suite, the browser tests (keeping screenshots of
any that fail), and a Docker check that builds the image, starts it, and
saves and finds a note.

Beyond tests of each feature, the fast suite also has:

- `test_invariants.py` -- hundreds of random create / edit / trash /
  restore / delete-forever / attach / detach / restore-version steps,
  checking after each that labels, links, the search index, files on
  disk, History and versions are exactly what the notes say.
- `test_properties.py` -- generated input (Hypothesis): rendering never
  lets HTML or `javascript:` links through, what shows as a label or link
  is what's stored, any search parses and runs, the network checks hold.
- `test_query_plans.py` -- every page's queries read through an index
  (checked with `EXPLAIN QUERY PLAN`), so a lost index fails a test
  instead of quietly slowing pages down.

To check that pages still cost the same however many notes there are,
time them on a throwaway database of ten years of notes (36,500 at 10 a
day; your own `data/` is never touched):

```bash
python scripts/benchmark.py                 # ten years, about a minute
python scripts/benchmark.py --notes 3650    # one year
```
