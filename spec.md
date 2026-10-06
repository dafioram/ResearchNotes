# Research Notes — Design Spec

Status: reflects the app as built. This is the running design record —
when a behavior changes, this file changes with it in the same commit.

## 1. Purpose

A single-user, no-auth, self-hosted note-taking app for atomic research
notes, built around the [Zettelkasten method](https://en.wikipedia.org/wiki/Zettelkasten):
short, linkable, taggable notes that accumulate into a web of ideas,
rather than long-form documents organized into folders.

Single-user and no-auth are deliberate, not a deferred feature: the app
assumes localhost or a trusted network, and nothing in the design should
add friction in service of multi-user support. It still refuses requests
that a web page elsewhere could make through a browser on that network --
changes sent from other sites, and requests addressed to public domain
names -- in ways that are invisible in normal use (§14).

**Desktop is the target platform.** The app is designed for a desktop
browser window (roughly 1280px wide and up) with a mouse and keyboard.
Phones and narrow windows are not a goal: there is no responsive or
touch layout, and no design decision should trade away anything on
desktop to accommodate one. It works offline except for the web fonts,
which fall back to local serif/monospace fonts (§2).

## 2. Stack

- **Flask**, not FastAPI. This is a page-rendering app (feed, forms,
  redirects, server-rendered filtering) — Flask + Jinja2 fits directly.
  FastAPI's strengths (async I/O, pydantic validation, auto-generated
  OpenAPI) don't pay off for a single-user app with no separate JSON API
  consumer.
- **SQLite**, one file, via the Python standard library's `sqlite3`
  (WAL journal mode). No ORM.
- **Vanilla JS**, no framework, no bundler. A handful of small
  page-specific scripts (`feed.js`, `graph.js`, `app.js`) loaded as
  plain `<script>` tags, plus `math.js` on every page (§5.1).
- **Cytoscape.js** for the two graph views — a third-party library that
  earns its place. Bundled in `app/static/vendor/`
  (MIT licensed, license header kept in the file) rather than loaded
  from a CDN, so the graph works without a network connection. Upgrading
  means replacing that file and the version in its filename.
- **KaTeX** draws math (§5.1) in the browser -- the second library, since
  typesetting TeX is a project of its own. Bundled the same way, in
  `app/static/vendor/katex-<version>/` (the minified script and CSS,
  only the `.woff2` fonts, and its MIT `LICENSE`), and loaded on every
  page (~300 KB, cached; the fonts load only when math is shown).
- **Web fonts** (Source Serif 4, IBM Plex Mono) come from Google Fonts;
  offline, the CSS falls back to Georgia and the system monospace font,
  so nothing breaks, it just looks slightly different.
- **No third-party markdown library.** The markdown renderer is
  hand-rolled (§5) because the spec calls for a deliberately restricted
  subset with two note-specific extensions (labels, note references)
  that don't exist in any off-the-shelf parser.

## 3. Data model

```
notes(id PK, body, sort_date, created_at, updated_at, deleted_at)
note_labels(note_id, name)                           -- derived, resynced on save; not for Trash
note_links(from_note_id, to_note_id)                 -- derived, resynced on save; not for Trash
attachments(id PK, hash UNIQUE, filename, extension, mime_type, size, created_at)
note_attachments(note_id, attachment_id)             -- many-to-many
notes_fts(body)                                       -- FTS5 virtual table, external content
activity(id PK, kind, note_id, created_at, updated_at, save_count,
         touches_links, detail JSON, base_body, base_sort_date)   -- History, §11
```

`note_labels` and `note_links` are **not** the source of truth — they're
a denormalized index over `notes.body`, rebuilt every time a note is
saved (§6.3). The source of truth for labels and note-references is
always the literal text of the note.

There is deliberately **no separate `labels` table**. A label has no
properties of its own — no description, color or alias — only a name,
and it exists exactly when some note's text uses it. So `note_labels`
stores the name directly, like `note_links` stores note ids: counts are
a `GROUP BY name`, filtering is `WHERE name = ?`, and there can never be
a leftover label that no note uses. (Earlier versions had
`labels(id, name)` plus `note_labels(note_id, label_id)`; the extra table
cost an id lookup per label on every save, a cleanup query to delete
unused labels, and an extra join in every label query, for no benefit.
Revisit only if labels gain properties of their own; renaming a label
wouldn't need one either, since that means rewriting note text.)

`note_links.to_note_id` has no foreign key constraint, because it may
point at a note number that doesn't exist (yet, or ever) — that's a
valid, expected state (a "ghost" reference, §6.2), not a data error.

Soft delete is a nullable `deleted_at` timestamp on `notes`, not a
separate table or a boolean. Every query that lists notes for normal use
filters `WHERE deleted_at IS NULL`; the Trash view is the one place that
filters the opposite way.

**What's derived covers only notes not in Trash.** Moving a note to Trash
removes its rows from `note_labels` and its outgoing rows from
`note_links`, and triggers take it out of the search index (§7);
restoring it re-reads all three from its text, exactly as saving does
(§6.3). So nothing that reads labels, links or search has to check for
Trash: counting labels is a plain count over `note_labels`, a backlink
is any `note_links` row, and so on — each was a join to `notes` per row
before. Links *to* a note in Trash, written in notes that aren't, stay
put (they show as ghosts meanwhile, §6.2).

`note_labels` and `note_links` are `WITHOUT ROWID` tables: the primary
key *is* the table, instead of a table plus a copy of it as an index —
about 40% smaller (2.8 MB less over ten years of notes). That shape
applies to databases created since; an existing database keeps its
tables, which behave the same.

## 4. Notes: feed, view, edit

- Each note has a unique auto-incrementing integer **id**, a
  user-set **sort date** (date-only, no time component — set explicitly
  by the person when creating or editing a note, never derived from
  `created_at`), and raw markdown-subset **body** text.
- **Line count** = number of non-blank lines in the raw body
  (`sum(1 for line in body.splitlines() if line.strip())`). Blank lines
  are not counted; an all-blank or empty body counts as 0.
- The **feed** (`/`) lists all non-deleted notes ordered by sort date
  descending, most recent first, ties broken by id descending, one page
  at a time (§4.2), under **day headings**: "Today", "Yesterday", then
  "Monday 5 October" (with the year when it isn't this one), from each
  note's sort date and the server's local date. A day that a page break
  cuts gets its heading on both pages. Lists filtered by labels,
  `is:`/`has:` filters or dates (§7) keep the headings, since they're in
  the same order; a search with words is ranked by relevance, so it has
  none.
- **Jump to a month**: on a dated list the sidebar has a month field
  (`<input type="month">`; `YYYY-MM` or `YYYY` typed where browsers show
  a plain box). Go opens the list on the page where that month's notes
  start, at the heading of its newest day (`/?month=2025-03` redirects to
  `/?page=N#d-2025-03-31`), keeping the search. A month with no notes
  lands on the next older day; one newer than everything, at the top;
  one older than everything, on the last page. It counts the notes dated
  after the month in SQL (`db.date_position`, 3–9 ms on ten years of
  notes). Each entry is a card showing a stamped meta row
  (`No. <id>` / sort date / line count, plus `N files` when it has
  attachments, §9.4, and `N backlinks` when other notes reference it,
  §6.5 — each badge omitted entirely when its count is zero) and a
  **snippet**: the rendered markdown of just the note's first non-blank
  line (typically a header, since that's what a header is for).
- Clicking a card **expands it in place** to the full rendered note,
  its attachments, and its backlinks (fetched once from
  `/notes/<id>/fragment` — a bare HTML fragment, no page chrome — then
  cached client-side) rather than navigating away. Clicking again
  collapses it. A disclosure chevron gives the same toggle with
  `aria-expanded` state for keyboard/AT users.
- Each card also has an **Edit** button, which opens the note's page
  straight in Edit mode (§4.3). There is no separate "view" link on a
  card: expanding it already shows the rendered note, and the `No. <id>`
  stamp is plain text. From Edit, the switch goes to View if wanted.
- Each note has **one page** with a **View / Edit switch** — see §4.3
  for everything about viewing, editing and saving.
- **Soft delete**: a Delete button sets `deleted_at`; the note
  disappears from the feed, search results (every filter included), and
  label counts, and its page returns 404. While it's deleted, `[[id]]`
  references to it render as ghosts (§6.2) and it appears as a ghost
  node in the graph (§10) — the same as a note that never existed. Its
  id is never reused, and the referencing notes' `note_links` rows are
  untouched, so restoring it brings every link back exactly as it was.
  Its own labels, links and search entry are removed while it's in
  Trash and re-read from its text when it's restored (§3).
  A **Trash** view (`/trash`) lists deleted notes, most recently deleted
  first and paginated like the feed (§4.2): each row shows the note's
  number, title (first line), line count and deletion date, with
  **Restore** (clears `deleted_at`) and **Delete permanently**. **Empty
  Trash** in the sidebar deletes them all. Both ask first ("This can't
  be undone").
- **Deleting permanently** removes the note row; its History entries and
  versions (§11) go with it (`ON DELETE CASCADE`), as do its file links
  — and any file no other note uses, in Trash or not, is deleted from
  disk (§9.2). Links to it from other notes stay, rendering as ghosts
  (§6.2), and its number is never reused (`AUTOINCREMENT`), so a ghost
  can't later point at an unrelated note. Only notes already in Trash
  can be deleted permanently.

Every list of notes is the feed, with or without a search (§7): what
used to be separate Orphans and Attachments pages are now the filters
`is:unlinked` (§8) and `has:file` (§9.4), so they combine with words,
labels and dates like any other part of a search. Cards are one shared
Jinja macro (`_macros.html`), so expand/collapse, Edit, the badges, and
pagination behave identically wherever a card is shown.

### 4.1 Layout

A two-column desktop layout under a full-width top bar. The top bar
holds the wordmark, **the search box** (on every page, so a search is
one step from anywhere; it submits to the feed, §7), and the nav: New
note, Random, History, Trash (Graph is reached from a note).

- **Left sidebar** (270px, sticky — it stays in view while the page
  scrolls, and scrolls on its own if it's taller than the window). Its
  contents depend on the page:
  - Feed: Views (shortcuts that add `is:unlinked` / `has:file` /
    `has:later` to the search), the label chips with their order switch
    and filter box (§6.4), Jump to (a month), and a folded "Search tips"
    with the syntax (§7).
  - Trash / History: the page name and what it lists.
  - A note's page: its number and the View / Edit switch; then in View
    its date and line count, in Edit the date picker, Save / Done /
    Cancel, save status and shortcut hint; View graph, Versions and Delete in both
    (§4.3).
- **Main column**: all the width beside the sidebar — cards, lists,
  headings and the editor use it fully. Only **paragraphs of note text**
  stop at about 100 characters (`--prose-measure: 100ch`), since much
  longer lines get hard to read; on a narrower window they simply wrap
  sooner. (This replaced an earlier fixed 760px reading column, which
  left most of a desktop monitor empty.) Because every list page uses
  the same grid, switching between the feed, Trash and History never
  moves the list sideways.
- **Type**: 18px body text (serif), with every other size one step up
  from the original design to suit desktop reading distance.
- **Graph**: no sidebar; the graph takes the full window width and
  height below the top bar (§10).
- The top bar and the content share one app width — the whole window
  up to 1760px, with 40px margins, centered beyond that — so the
  wordmark lines up with the sidebar and the nav never shifts between
  pages, including the graph, whose content alone breaks out to full
  width. At 1870px wide the main column is about 1,360px; at 1440px,
  about 1,040px.

### 4.2 Pagination

The feed — searched or not — and Trash are paginated, **not** infinite-
scroll: `PAGE_SIZE` notes per page (default 50, configurable, §13),
selected with `?page=N`. Page 1 has a clean URL (no `page` param).

- Chosen over infinite scroll because each page is a real URL: the back
  button returns to the same place after opening a note, a position can
  be bookmarked, the DOM never grows without bound, and it stays
  server-rendered like everything else.
- Chosen over "load everything" (the original behavior) because every
  card renders markdown for its snippet, so an unpaginated feed's cost
  and page weight grow linearly with note count.
- Implemented with `LIMIT`/`OFFSET` plus a separate `COUNT(*)` with the
  same conditions (wrapping the ordered listing in a count made SQLite
  sort everything just to count it). Keyset/cursor pagination would
  scale further but can't jump to an arbitrary page number; offset is
  plenty for one person's notes.
- **A page costs the same however many notes there are.** The order
  comes straight off an index of the notes not in Trash
  (`idx_notes_live`, `sort_date DESC, id DESC WHERE deleted_at IS NULL`),
  so a page is read in order and the scan stops after it; every listing
  says `deleted_at IS NULL` so SQLite can use it. (An earlier index on
  `deleted_at` alone led SQLite to read and sort every note, bodies and
  all, to show 50; it's dropped at startup.) Everything else on a page —
  badges (§6.5, §9.4) and whether each `[[ref]]` exists (§6.2) — is
  looked up for just the notes shown. Measured with
  `scripts/benchmark.py` on ten years of notes at 10 a day (36,500): the
  feed in 14 ms and a note's page in 7 ms, which had taken 255 and
  107 ms; at 100,000 notes, 38 and 17 ms (from 711 and 279). What still
  grows with the collection is small: the pager's count, and listing the
  labels in the sidebar.
- Navigation: Previous / Next plus page numbers — always the first and
  last page, and two either side of the current one, with `…` for gaps
  (a gap of exactly one page is filled in instead). A "Showing 101–150
  of 320" summary sits below. Nothing is rendered when everything fits
  on one page.
- Page links carry the current search (`q`), so paging works inside
  filtered results.
- Out-of-range pages (`?page=99` when there are 7) redirect to the last
  page; non-numeric, zero, or negative values fall back to page 1.
- Search results are ranked and paged in SQL, in one ordering over the
  **whole** result set — note-number matches (§7) first, then relevance
  — so the promoted matches always lead page 1, and a page of a large
  result reads only that page's notes in full.


### 4.3 The note page: View and Edit

Each note has one page with two modes, switched by a sliding **View /
Edit** switch at the top of the sidebar. Switching never reloads or
leaves the page.

- **Addresses:** `/notes/<id>` opens in View, `/notes/<id>/edit` in Edit.
  Both serve the same page (`note.html`); flipping the switch replaces
  the address in place (`history.replaceState`), so refreshing keeps the
  mode and Back doesn't step through every flip.
- **Which mode links open in:** every link *to* a note — `[[links]]` in
  note text, backlinks, History entries, graph nodes, Random, "see all on
  the note's page" — opens in **View**. Only the feed cards' Edit button
  and New note open in **Edit**.
- **View** shows the rendered note, its attachments and its backlinks
  (§6.5). Its sidebar has the sort date and line count.
- **Edit** is the raw markdown in a plain `<textarea>` — intentionally
  not a rich editor or a live-preview split pane — using the full width
  of the main column and the height of the window. Its sidebar has the
  sort date picker, Save / Done / Cancel, a save status line and the
  Ctrl+S hint. Labels and links are suggested in the text as you type
  them (§6.1, §6.2). The attachment editor is under the text (§9.3). Opening a page
  in Edit puts the cursor in the editor.
- **View graph**, **Versions** (§11.5) and **Delete** are in the sidebar in both modes. View
  graph is greyed out, explaining "No connections yet" on hover, when the
  note has no links in or out (§10).

**Saving never leaves the page.**

| Action | What happens |
|---|---|
| **Save** or **Ctrl+S** (⌘S on a Mac) | Saves and stays in Edit, as often as you like. |
| **Done** | Saves, then switches to View. |
| Flipping the switch to **View** | Same as Done: saves (if anything changed), then shows View. |
| Stopping typing for **3 seconds** | Saves by itself (autosave), once the note exists. |
| **Cancel** | Undoes this stretch of editing — asking first if anything changed: puts back the text and date as they were when Edit was opened, saving that if autosave had already saved some changes, and switches to View. |

- A save sends the form with `fetch` and an `X-Requested-With: fetch`
  header; the server answers with JSON: the freshly rendered View pane,
  the sort date and line count, whether the note now has connections
  (so View graph can enable or grey out without a reload), and the time
  saved. Without that header the same routes redirect as a plain form
  would.
- **Line endings are stored as `\n`.** Browsers send a textarea's text
  with `\r\n`; `create_note`/`update_note` convert it, so headers don't
  end in a stray `\r` and fenced code doesn't open with a blank line.
  (Found by the browser tests; notes saved before keep `\r\n` until
  they're next saved, which renders the same.)
- The status line under the buttons reads "Unsaved changes" as soon as
  the text or date differs from what was last saved, "Saved 10:42 AM"
  after a save, or the error if a save failed.
- A rejected save (e.g. the sort date was cleared) changes nothing and
  keeps you in Edit — including when it was triggered by Done or the
  switch — with the reason in the status line; the browser's own
  "please fill in" bubble shows for a missing date.
- Ctrl+S never opens the browser's "Save page as", ignores repeat
  presses while a save is in flight, and does nothing in View. It's the
  app's only keyboard shortcut, by choice.
- **Leaving with unsaved changes** (a nav link, Back, closing the tab)
  shows the browser's "leave page?" warning. Delete asks its own
  confirmation instead, so it never shows both.
- Frequent saves don't flood History: saves within 15 minutes merge into
  one entry (§11.2), and into one version (§11.5).
- **Autosave** runs only for a note that exists: a new note is created by
  its first Save, by hand, so a stray keystroke on New note never creates
  one. It waits for 3 s without typing, skips a form the browser would
  reject (a cleared date — that waits for Save, which explains), and if
  typing went on while a save was in flight, saves again 3 s later. A
  failed autosave shows its error in the status line and tries again at
  the next pause in typing.
- **Drafts.** Until text is saved, it's also kept in the browser's own
  storage (`localStorage`, `rn-draft:<id>` or `rn-draft:new`), written
  300 ms after typing stops and when the page is hidden or closed. So a
  crash, a killed tab, a dead battery or the app being unreachable can't
  lose it. Opening that note (or New note) again in the same browser
  shows a banner — "Unsaved changes to this note from … were kept in
  this browser", adding "The note has been saved since" if it has —
  with **Restore** (puts it in the editor, in Edit, where autosave then
  saves it) and **Discard**. A draft identical to the saved note is
  dropped silently; drafts are removed once saved, and any older than
  30 days are dropped. A draft lives in one browser only.

**New notes** (`/notes/new`) open in Edit with no switch yet, dated
today (local time).

- The **first Save** (or Ctrl+S) creates the note and keeps you editing.
  The page becomes that note's page in place: its number appears, the
  address becomes `/notes/<id>/edit`, the switch appears, and the
  attachment editor, View graph, Versions and Delete arrive in the same JSON reply
  — so files can be attached right away.
- **Done** saves and returns to the **feed, landing on the new note**: the
  feed opens on the page the note falls on in the default order (its
  sort date is yours to set, so it may not be page 1; any search or label
  filter is dropped so it's sure to be listed), scrolls it to the middle
  of the window, and briefly highlights it (≈2 s fade). The `focus`
  parameter that asks for this is removed from the address afterwards,
  so a refresh doesn't repeat it. If the page is too short to scroll, the
  note just stays where it is.
- **Cancel** returns to the feed. If the note was never saved, nothing is
  created (it asks first if anything was typed); once it has been saved,
  Cancel goes back to its first save.

**Coming back to lists.** Pages are sent with `Cache-Control: no-store`,
and the list pages reload if the browser restores them from its
back/forward cache — so pressing Back to the feed after editing always
shows the current titles and counts, at the scroll position you left.
No highlight in that case.

## 5. Markdown subset

Hand-rolled, not CommonMark. Supported:

| Syntax | Renders as |
|---|---|
| `# ` … `###### ` (space required) | `<h1>`–`<h6>` |
| `**bold**` / `__bold__` | `<strong>` |
| `*italic*` / `_italic_` | `<em>` |
| `~~strike~~` | `<del>` |
| `` `code` `` | `<code>` |
| ` ```lang␊code␊``` ` | `<pre><code class="language-lang">` |
| `[text](url)` | `<a>` |
| a bare `http(s)://` address | `<a>` (the address as its text) |
| `- item` / `* item` | `<ul><li>` |
| `1. item` | `<ol><li>` |
| `> quote` | `<blockquote>` |
| `---` / `***` / `___` | `<hr>` |
| blank-line-separated paragraphs; single `\n` | `<p>`; `<br>` |
| `$x^2$` / `$$…$$` | math, drawn by KaTeX (§5.1) |
| `\| a \| b \|` + `\|---\|--:\|` + rows | `<table>` (§5.2) |
| `![caption](/files/<hash>)` | an attached image; alone on its line, a `<figure>` (§9.5) |

Deliberately **not** supported: images from anywhere but the app's own
stored files (§9.5), raw HTML passthrough
(everything is HTML-escaped first, so the table above is genuinely the
entire vocabulary available — there is no way to smuggle a `<script>`
tag through a note body).

Underscores only emphasise at word boundaries, as in CommonMark:
`_word_` and `__word__` work, but an underscore inside a word does
nothing, so `max_batch_size` and `results_2024_final.csv` stay as typed
(they used to come out as max*batch*size). Asterisks work anywhere,
including inside a word. As in CommonMark, `__init__.py` is still read as
bold "init" — the closing `__` is followed by punctuation — so wrap
identifiers like that in backticks.

A bare `http://` or `https://` address in running text becomes a link,
with the address as its text. Trailing punctuation is left out
(`…/2401.00001.` links without the full stop), and so is a `)` that
doesn't close a `(` inside the address: `(see https://x.org/a)` links
`https://x.org/a`, while `https://en.wikipedia.org/wiki/Foo_(bar)` keeps
its brackets. Addresses are set aside before emphasis and labels, so
underscores and `#` in them are left alone; inside code they stay code.

A `[text](url)` link is only made clickable when the URL is `http:`,
`https:`, `mailto:` or has no scheme at all (a relative address, a
`#fragment`, `//host/...`). Any other scheme — `javascript:`, `data:`,
`vbscript:` — and any URL containing control characters (which browsers
silently strip, so they could hide a scheme) is shown exactly as typed,
as plain text. The URL is checked as typed and HTML-escaped once for the
attribute, so `?a=1&b=2` reaches the browser intact.

Parsing order, in the actual renderer: fenced code blocks, inline code
spans and then math are extracted into placeholders **before** anything else runs
(so a `#` inside a code block is never mistaken for a label, and a
`[[42]]` inside inline code is never linkified); block-level structure
(headers/lists/quotes/hr) is parsed next; inline formatting and the two
note-specific extensions below run last, on the remaining text; code is
spliced back in verbatim at the end.

### 5.1 Math

TeX between dollar signs, the way most research writing tools take it:

- `$…$` is inline math: `the rate $\lambda$ falls as $e^{-t/S}$`.
- `$$…$$` is a display block, centered on its own line. It may span
  lines, blank lines included:

  ```
  $$
  \hat\theta = \arg\max_\theta \sum_i \log p(x_i \mid \theta)
  $$
  ```

- **Prices stay text** (pandoc's rules): an opening `$` must have no
  space after it and not follow a letter or digit, and a closing `$`
  must have no space before it and not be followed by a digit. So "$5
  and $10", "between $5 and $6." and an address with a `$` in it are not
  math. `\$` is a plain dollar sign.
- **Nothing inside math is anything else.** Math is set aside right after
  code, so `#`, `[[3]]`, `*` and `_` inside it are TeX -- not labels,
  links or emphasis -- and `extract_labels()` / `extract_note_refs()`
  agree (§6.3). Code wins: `` `$x$` `` is code.
- **The server never renders TeX.** It writes the source as typed,
  dollars included and HTML-escaped, in `<span class="math math-inline">`
  or `<span class="math math-display">`. `static/math.js` hands each one
  to KaTeX in the browser -- on load, and in anything added later (a
  feed card opened in place, the view after a save) -- and keeps the TeX
  as the element's tooltip. KaTeX runs with `trust: false`, so commands
  that make links or HTML (`\href`, `\url`, `\html…`) are refused, and
  with `throwOnError: false`, so bad TeX shows in red instead of breaking
  the page. Without JavaScript the TeX simply shows as typed.
- **Plain text keeps the TeX**: titles in links, backlink passages,
  History and the graph show `$e^{-t/S}$` as typed. Search indexes the
  note as typed, so `lambda` finds `$\lambda$`.

### 5.2 Tables

Pipe tables, as in GitHub, pandoc and Obsidian, so they paste in from
other tools and copy back out unchanged:

```
| Model | Accuracy | Notes        |
|:------|---------:|--------------|
| Base  |     0.81 | see [[12]]   |
| +aug  | **0.86** | $p < 0.01$   |
```

- **A header row, then a separator row** of dashes with as many cells,
  then rows. The separator is required, so a stray `|` in prose never
  makes a table. Outer pipes and spacing are optional (`A | B` / `--- |
  ---` works).
- **Alignment** from colons in the separator: `:---` left, `---:` right
  (numbers line up), `:---:` centered. Shown as `class="align-…"` on each
  cell, not a style attribute.
- **Cells** hold inline markdown: emphasis, code, math, links, `#labels`
  and `[[refs]]`, which count like anywhere else. `\|` is a literal pipe;
  a `|` inside code or math never splits a cell, since those are set
  aside first.
- **Rows** run until a blank line or another block (header, list, quote,
  rule, code block, another table). A row with too few cells is padded
  to the header's width; one with too many keeps its extra cells (GitHub
  drops them, but then a label or link in one would be stored without
  showing). No merged cells and no
  line breaks inside a cell -- a deliberate limit, shared with the other
  tools.
- **A table ends a paragraph**: a line of text right above the header
  row is its own paragraph.
- A wide table scrolls sideways inside the note.
- **Backlinks**: a `[[ref]]` in a cell shows its row as the passage, the
  cells joined by `|` ("Base | see [[12]]"), not the whole table (§6.5).
- **Pasting** (the editor, `app.js`): cells copied from a spreadsheet
  arrive as tab-separated text, and exported data is often
  comma-separated. Either becomes a pipe table -- columns padded to line
  up, columns of numbers right-aligned, `|` in a cell escaped, line
  breaks in a cell made spaces -- when it has two or more rows of the
  same number of cells (two or more), none empty in the first row.
  Quoted cells (`"Smith, J."`, `"two\nlines"`) are read as one. With
  commas, no unquoted cell may start with a space: that's how prose
  ("Hello, world") differs from data (`Model,Accuracy`). Never inside a
  ` ``` ` code block. The table goes on lines of its own, ended by a
  blank line. The paste goes in as it was first and the table then
  replaces it, so **Ctrl+Z gives back exactly what was pasted**.

## 6. Labels and note references

### 6.1 `#label`

What is and isn't a label:

- The `#` **starts a line or follows whitespace** (a space or tab). So a
  URL's fragment (`guide#install`), `C#` and `foo#bar` aren't labels —
  nor is a `#` right after a bracket or bold markers: `(#aside)`,
  `**#bold**`.
- Then a **letter**, in any language (`#café`, `#日本語`), so `#3`,
  `PR #42` and `#2024-review` aren't labels (`#review-2024` is).
- Then any of letters, digits, `.`, `-` and `_` (`#node.js`, `#v2.1`,
  `#snake_case`) — but a label **ends on a letter or digit**: trailing
  `.`, `-` and `_` aren't part of it, so "I read about #physics." is
  `#physics`.
- `#label` needs no space after the `#`; that's what tells it from an
  ATX header, which (per CommonMark and this parser) **requires** one:
  `#label` → label; `# Title` → `<h1>`. A doubled `##word` (no space) is
  neither — not a header (no space) and not a label (its second `#`
  follows a `#`, not whitespace) — so it renders as inert literal text.
- A hex colour like `#fff` still reads as a label; wrap it in backticks.
- A `#` running into a web address isn't a label: `#http://x.org` is the
  address, which becomes a link.
- A `|` counts like whitespace before a label, so a table cell written
  without spaces, `|#baseline|`, holds the label `#baseline` (§5.2). The
  same goes outside tables: `a|#b` is a label.
- Nothing inside a web address, a `[text](url)` link (its text or its
  address) or a `[[later: hint]]` is a label: rendering sets those aside
  first and shows them as typed. (`[a #draft](url)` used to store
  `#draft` without showing it; such notes drop it at their next save or
  `flask reindex`.)
- A quote marker at the very start of a line counts as the line's start:
  `>#idea` is a label inside the quote. (What's stored is what shows,
  below; property tests, `tests/test_properties.py`, check that over
  thousands of generated texts.)

**What's stored is what shows, by construction:** a note's labels are
collected while rendering it (`extract_labels()` renders the note and
keeps every label it drew), not by a second scan of the raw text that
would have to repeat every rule -- code, math, links, tables, quotes --
and could drift from it. Two separate scans did drift (a label in a
table row cut short, or after a `|` inside an address, was stored but not
shown); the property tests found both. Rendering a note takes well under
a millisecond, so this costs nothing noticeable on save, and about 20
seconds over 36,500 notes for `flask reindex`. Labels are found before
bold and italic run (emphasis used to reach into them: `#snake_case_`
displayed as `#snake` and an italic "case"). The label's link searches for it: `/?q=%23<name>`, percent-encoded
(§6.4).
These rules were tightened from "a `#` not after another `#`, then
letters, digits, `_` and `-`" (which made `#3` a label and cut `#café`
to `caf`). Notes saved before keep their old labels until they're saved
again, or until one `flask reindex` (§13).

Within those rules a label can appear **anywhere** in a note's text,
not just at the start of a line. Extraction and rendering both ignore anything inside code
blocks/spans. Label names are case-insensitive for storage/dedup
purposes — extraction lowercases before it ever reaches the database
(`#Research` and `#research` are stored as the same `note_labels` name,
backed up by `COLLATE NOCASE` on the column) — so the label list always shows
one canonical entry per name with a combined count. Inline within a
note's own rendered text, a `#label` still displays in whatever case the
person actually typed; only the underlying row and the filter-link
target are lowercased.

**Suggestions while typing.** In the editor, `#` and a letter where a
label can start (line start or after whitespace — so not `# Title`,
`page#part` or `C#`) opens a list of the labels in use that match, in
the same pop-up as `[[` links (§6.2): the label typed exactly first,
then labels starting with it, then labels containing it (`learn` →
`#learning`, `#machine-learning`), most-used first within each, up to
8, each with its note count. Tab or Enter completes it (replacing the
rest of the word, and adding a space unless one follows); Enter on a
label already typed in full just starts a new line; a label nobody has
used yet shows no list. The labels come from `/api/labels` once per
page, and again after each save, which may have added some.

This replaced a picker in the Edit sidebar that showed every label as a
button — 376 buttons at ten years — and made every note page count
every label to draw it.

### 6.2 `[[note-number]]`

References another note by id, from anywhere in the text. The text
always holds the number — it never changes, whatever happens to the
other note's title — but it **displays as that note's title**: the
title (its first line as plain text, `md.first_line_text`, cut at 80
characters with the whole title in the tooltip) as a link to
`/notes/<id>`, with the number small after it, so "see [[12]]" reads
"see Spacing effect" with a small 12. A note with an empty first line shows as
`[[12]]`. Where the text sits inside a link of its own, or is plain text
— backlink titles and passages (§6.5), History entries and their "now
links to …", Trash rows, the Versions and graph pages' titles — a ref
shows the same way but **as text** (`md.refs_as_titles`): the title,
dotted underline, number small after it, no link of its own (links can't
nest). The titles are looked up once per page, for just the refs on it.
A ref to a missing note stays `[[12]]` there too.

A ref to a note that doesn't exist or is in Trash renders as a
**ghost** — visually distinct (dashed, muted-red underline, a `title`
tooltip) so a broken reference is visible at a glance instead of
silently swallowed or erroring. Titles are looked up for just the ids
the shown notes reference (`db.note_titles`), not by loading every note.

**Writing a link.** Typing `[[` in the editor opens a list of notes to
link to, under the cursor (`app.js`, backed by `/api/notes/lookup`):

- Nothing typed yet: the latest notes, in feed order.
- Words: a search (§7) with the last word taken as a prefix since it's
  still being typed; notes with all the words in their **title** come
  first, then the rest by relevance. Up to 8.
- A number: notes numbered that way first (`12` → 12, 120, 121 …).
- The note being edited is left out.
- The last choice is always **link later** (below), carrying what was
  typed as its hint.

Up/Down move, Enter or Tab inserts `[[id]]` (replacing the `[[` and
what was typed after it, and a `]]` already there), Escape closes the
list until the next `[[`. Clicking a choice works too. Insertion goes
through the browser's own editing, so Ctrl+Z undoes it.

**`[[later]]`** — a link to fill in later, for when the note it should
point to doesn't exist yet or can't be found right now. Any
capitalization; optionally with a hint, `[[later: Bjork 1994]]`, so it
says what it's waiting for. It shows as a dotted placeholder (hint and
all, as typed — nothing inside it is formatted) and links nowhere: it
makes no `note_links` row, so no backlink, no graph node, and it doesn't
count as a connection for `is:unlinked`. `has:later` (§7, and the
sidebar's *Links to fill in* view) lists every note that still has one;
filling one in is replacing it with a `[[` pick. Chosen over creating a
note from the pop-up, or `[[+]]` for "the next note": a number taken
before the note exists can end up pointing at the wrong one (§12).

### 6.3 Resyncing on save

`create_note()` / `update_note()` re-parse the full body on every save,
diff the resulting label set and reference set against what's currently
stored in `note_labels` / `note_links`, and add/remove rows to match
exactly. When the last note using a label stops using it, that label is
simply gone from the list — there's no separate label record to clean
up (§3). This is why labels and links are described as "derived" in §3 —
the note body is the only thing a person actually edits; the index
tables just track it. `flask reindex` does the same for every note at
once, from scratch (§13).

### 6.4 Label list & filtering

The feed's sidebar lists every label in use as **chips** packed several to
a line (`app/labels.py`), each with its note count. One line per label —
the original list — made 376 labels about 13,000 px tall.

- **Namespaces.** Labels sharing the part before their first `-`
  (`#physics-mechanics`, `#physics-quantum`) collapse into one dashed chip,
  `#physics-*`, when two or more labels share it; a label that *is* the
  prefix (`#physics`) joins its group, first. A lone `#long-term` stays
  as it is. The group's count is notes with any of its labels, each note
  once. Its caret (▸) opens the labels in place, shortened (`-quantum`);
  clicking the group's name searches the whole namespace. One level only:
  `#physics-quantum-field` sits under `#physics-*`.
- **Order**: a switch above the chips — **Recent** (the default), **Most
  used**, **A–Z** — kept in a cookie (`label_order`, via
  `/labels/order/<order>?next=…`, which only ever redirects back into the
  app), so the server lists labels in that order straight away; each
  browser keeps its own. *Recent* is by when a note carrying the label
  was last saved, read from the newest 1,000 saves in History (§11; its
  time index makes that 1.6 ms at ten years — every label's exact last
  use took 75 ms); labels not used in that stretch follow, by count. A
  group sorts by its newest label, its count, or its prefix.
- **Cap**: in Recent and Most used, the first 36 chips (a group counts as
  one), then **Show all N labels** opens the rest in place. A–Z shows
  everything — the point of choosing it.
- **Labels in the current search come first**, highlighted, and a group
  holding one is opened, so what's selected is never hidden by the cap.
- **Filter box** (over 12 labels): narrows every chip as you type,
  matching anywhere in the name (`phy` → `#physics-*` and its labels,
  `#philosophy`, `#typography`), opening groups to show matching labels,
  with "9 of 139 labels" below; clearing it restores the list as it was.

A label is a search term (§7): clicking one adds `#name` to the current
search, and clicking it again (it's highlighted while it's in the search)
takes it out. Labels together match **any** of them — clicking a second
label widens the list: `#memory #learning` is notes with either (or both).
`#physics-*` is the namespace: `#physics` or any `#physics-…` label.
`+#name` requires a label whatever else is there (`#memory +#draft`:
memory notes that are drafts; `+#a +#b`: both; `+#physics-*`: some physics
label), and `-#name` leaves it out (`-#physics-*`: no physics label at
all). Labels then combine with words, dates and the other filters, which
are all required. Old `/?label=name` links redirect to the same search.
(Several labels used to mean all of them; any-of was asked for, and `+#`
keeps the narrowing.)

How they stay quick on a big collection (§4.2):

- **Counts** are a plain count over the label index: notes in Trash have
  no label rows (§3), so there's nothing to check. On ten years of notes
  that's about 3 ms; checking each labelled note's row for Trash took
  over 100, on every feed and note page. Building the whole chip list —
  counts, namespaces and their counts, recency — takes 4–6 ms with 375
  labels; the feed page about 17 ms in all.
- **A namespace** is one range on the label index: `#physics-*` is
  `physics` plus every name from `physics-` up to `physics.` (`.` sorts
  right after `-`).
- **A label's page** — or a page of any of several labels, when that's
  all the search is — can be read two ways, and the quicker one depends
  on how common the labels are. Walking the feed in order and checking
  each note's labels stops as soon as the page is full — quick for a
  common label. Taking the labels' notes and sorting them reads every
  one — quick for rare labels. A step of the walk costs about a tenth of
  reading a note, so `list_notes_page` walks when that should take fewer
  than ten steps per note the labels have, and fetches by label
  otherwise. Both give the same notes in the same order. On ten years of
  notes: either of two common labels 17 ms, of two rare ones 12 ms (a
  single walk took 68 ms for the rare pair).

### 6.5 Backlinks

The notes that reference a note via `[[id]]` — a plain reverse lookup
on `note_links`, no re-parsing needed at render time. This is
Zettelkasten's core navigation primitive: it's what makes a note's
incoming connections as visible as its outgoing ones. Shown in three
places:

- **A note's own page** (`/notes/<id>`): the full list, below the body
  and attachments.
- **The inline expansion** on the feed-style lists: the same list,
  capped at the 10 most recent (by the referencing note's sort date),
  followed by "N more — see all on the note's page" when there are more.
  The cap keeps a heavily-linked hub note from turning one expanded card
  into a wall of hundreds of rows in the middle of the feed.
- **A badge on every card** (`N backlinks`), counted with one grouped
  query for the notes on the page (`db.get_backlink_counts(ids)`), not
  one per card and not for every note. The
  count is defined identically to the list — non-deleted referencing
  notes, each counted once — so the badge always matches what you see
  when you expand or open the note. A reference from a soft-deleted
  note doesn't count: a note in Trash has no link rows (§3).

Each backlink row is a single link to the referencing note, showing:

1. **Its title**: id, sort date, and first line (`md.first_line_text()`
   — usually the header, markdown stripped, so `# About #physics` shows
   as `About #physics`). This says *which* note links here.
2. **The context of each mention** (`md.ref_contexts()`): the passage
   around the `[[id]]`, with the reference highlighted. This says *why* it
   links here — often the more useful half, since in a Zettelkasten the
   reason for a link is the valuable part.

Context rules:

- The unit of context is the block the link sits in, matching how the
  renderer splits blocks: a paragraph, a single list item, a single
  header line, or a run of blockquote lines. A link in one bullet shows
  that bullet, not the whole list.
- The passage is trimmed to about 240 characters centered on the link,
  breaking at word boundaries, with `…` where text was cut. If one side
  of the link is short, the other side gets its unused space.
- Deliberately **not** sentence-based: sentence splitting is unreliable
  in research writing ("et al.", "e.g.", "Fig. 2", "p. 14", decimals),
  and "the first sentence of the paragraph" can miss the link entirely
  when it's further in. A character window around the link always
  contains it.
- Every mention gets its own context; a row shows at most 2, then
  "and N more mentions".
- A mention in the referencing note's first line is skipped — that line
  is already the row's title. If that was the only mention, the row is
  just the title.
- A `[[id]]` inside inline code or a fenced code block isn't a link
  (same rule as extraction, §5), so it's never shown as a mention.
- **Math** in a passage is drawn (KaTeX, §5.1), display math inline so
  the row stays a line of text; the trim never cuts through a formula --
  one it would split is left out. An **image** reads "Figure: <caption>".
  A table row stays its cells joined by `|` (§5.2). Nothing else is
  rendered: a passage is a reason for the link, not a preview.
- The highlighted mention reads **this note**: the list sits under that
  note, so its title would only repeat on every row. Any other `[[refs]]`
  in the passage or the title line show as their notes' titles (§6.2),
  so a row reads "This result contradicts **this note**, see Spacing
  effect ⁴".

Precedent for showing context rather than titles alone: Obsidian's
backlinks pane shows the text around each mention (truncated, with a
toggle for the full paragraph), and Semantic Scholar shows "citation
contexts" — the sentences in citing papers where a reference is cited.
Wikipedia's "What links here" is the titles-only counterexample; it
works there because article titles are self-explanatory, which a note's
first line often isn't.

Everything in a row is **plain text** rather than rendered HTML because
rendered markdown can contain links of its own (labels, other
`[[refs]]`), and a link inside the row's link is invalid HTML that
browsers repair unpredictably.

## 7. Search

One search box, in the top bar of every page (§4.1); it always lands on
the feed, filtered (`/?q=…`). Typing words is enough for a quick look;
the rest is there when it's wanted (`app/search.py` parses it, and the
feed's sidebar has the same list folded under "Search tips"):

| Typed | Finds notes… |
|---|---|
| `memory retrieval` | with both words, in any form (`retrieving` too) |
| `"spaced repetition"` | with that exact phrase |
| `retriev*` | with a word starting `retriev` |
| `-flashcards`, `-"rote learning"` | without that word / phrase |
| `#learning`, `#learning #memory` | carrying the label — or any of the labels typed (§6.4) |
| `+#draft` | carrying the label, whatever else is there; several `+#`: all of them |
| `#physics-*` | carrying `#physics` or any `#physics-…` label (a namespace, §6.4); `+#`/`-#` work too |
| `-#draft` | not carrying the label |
| `is:unlinked` | with no `[[links]]` in or out (§8) |
| `has:file` (or `has:files`) | with at least one attachment (§9.4) |
| `has:later` | with a `[[later]]` link still to fill in (§6.2) |
| `after:2025-03`, `before:2026` | by sort date: on/after the start of that period, before the start of that one (`YYYY`, `YYYY-MM` or `YYYY-MM-DD`) |
| `1234` | (a number alone) whose number starts with 1234, first — see below |

Every part is ANDed. Matching:

1. **Words and phrases** use **SQLite FTS5** over note bodies (external-
   content table kept current by triggers, below; porter + unicode61
   tokenizer). Case-insensitive; stemmed (`research` also matches
   `researching`); punctuation from `#labels` and `[[refs]]` is a token
   boundary, so searching `zettelkasten` finds a note that only contains
   `#zettelkasten`. Every word or phrase reaches FTS5 as a quoted phrase
   (a trailing `*` kept as a prefix), so nothing typed — `OR`, `NEAR(`,
   a stray quote — is ever read as FTS5's own syntax. A token with no
   letters or digits is dropped, since it couldn't match anything.
   Excluded words are one `NOT IN` over the index. With words to rank
   by, results order by FTS5 relevance (`bm25`); with none (only labels,
   filters or dates) they're in feed order.
2. **If the entire query is digits**, notes whose id *starts with* that
   number are also included and placed **at the top**, ahead of the
   notes whose text contains it — exact id first, then the rest
   ascending by id. Example: `100` lists notes 100, 1000, 1005, ...
   first, then any note whose *text* contains "100". A note that
   qualifies both ways is listed once, in the id group. A mixed query
   like `100a` does **not** match ids.
3. **Labels, `is:unlinked`, `has:file` and dates** are plain conditions
   on the same query (`EXISTS` over `note_labels` / `note_attachments`,
   a range on `sort_date`). **`has:later`** uses the search index to
   narrow it to notes containing the word "later", then checks those
   with the renderer's own rule (`md.has_later`, registered as an SQL
   function), so `[[later]]` inside code or the plain word "later"
   doesn't count — with no column or table of its own.
4. **Mistakes are said, not swallowed**: something that looks like an
   operator but isn't readable (`after:yesterday`, `after:2025-02-30`)
   is shown under the result count, and the rest of the search still
   runs. Other unknown `word:word` tokens are ordinary words.

Search never navigates directly to a note; it always filters the feed
list. Notes in Trash are never matched. A banner above the results says
how many notes match, with a link that clears the search. Each result
card shows, under its first line, **the passage that matched** — up to
about 24 words around the hits, with the matched words highlighted
(`<mark>`) — from FTS5's `snippet()`, for just the cards on the page;
the passage is HTML-escaped before the highlight is added, so a note's
text can't inject markup there.

Ranking, paging and counting are all done in SQLite (§4.2): nothing
loads every match. Two cases take the feed's own quicker paths: an empty
search is the feed, and a search that's only one `#label` is that
label's page (§6.4).

The index holds exactly the notes not in Trash, kept that way by
triggers on `notes`: a new note is added; a save that changes the text
replaces its entry, while a save that doesn't (a new sort date, say)
leaves the index alone; moving a note to Trash removes it, and restoring
adds it back. (They replaced triggers that re-indexed a note on every
update of any kind, and kept notes in Trash indexed.)

The index is kept current by the triggers alone; startup never touches
it (§13). It used to be re-filled from every note on every start, which
re-added notes that were already indexed: matches stayed right, but the
file grew with each restart (64 → 120 MB over a dozen restarts on a
10-year test database), each restart rewrote a sixth of the file (churn
for backups), and the index's count of notes grew too, skewing ranking.
`flask reindex` rebuilds the index from scratch when that's ever wanted
(§13).

## 8. Unlinked notes (`is:unlinked`)

The `is:unlinked` search filter (§7, also a shortcut under Views in the
feed's sidebar) finds notes with **zero** incoming and **zero** outgoing
`[[links]]` — notes that were never integrated into the web of ideas.
"Connected" uses the same rule as the graph (§10): a link *from* a note
in Trash doesn't count, so a note referenced only by trashed notes is
unlinked (and has no graph).
This is a Zettelkasten-hygiene view: a note that's been sitting unlinked
is a prompt to go back and connect it to something. It was a separate
Orphans page (`/orphans`, which now redirects to `/?q=is:unlinked`);
as a filter it combines with the rest of a search — `#memory
is:unlinked` is the memory notes still waiting to be connected.

## 9. Attachments

### 9.1 Model

Attachments are **not** stored on the note row — they live in their own
table, content-addressed by SHA-256 hash, with a many-to-many join table
(`note_attachments`) so the same uploaded file can be linked from
several notes without duplicating bytes. Uploading a file whose hash
already exists just adds a link row; it does not write to disk again or
create a second `attachments` row. There is no separate "attach an
existing file" search/picker UI — automatic hash-based dedup on upload
*is* the reuse mechanism, which is sufficient for the scale this app is
built for (one person's notes, not hundreds of attachments per note).

### 9.2 Storage layout

```
uploads/<hash[0:2]>/<hash[2:4]>/<hash><ext>
```

e.g. hash `abcd1234...` with a `.png` original → `uploads/ab/cd/abcd1234....png`.
Sharded by the first 4 hex chars so no single directory accumulates
thousands of files. The extension is captured as its own column at
upload time (parsed from the original filename), so path construction
never needs to re-derive it later, and `mime_type` (also captured at
upload time) is the source of truth for the `Content-Type` header when
serving the file back.

A file is deleted from disk (with its `attachments` row, and its two
folders if that leaves them empty) as soon as no note uses it: when it's
removed from its last note, or its last note is deleted permanently
(§4). A note in Trash still counts as using its files, since restoring
it brings them back. Only the files just unlinked are checked — never a
sweep of every file — so an upload that has written its file but not
linked it yet can't lose it.

**A file missing from disk** -- a data folder copied without all of
`uploads/`, say -- breaks nothing: notes open as usual and keep listing
it. An image whose file is gone shows "Missing image: <caption>" in red
instead of a broken picture (§9.5); opening the file gives a 404 page
naming it and where it was expected (`uploads/ab/cd/<hash><ext>`), so
putting it back there brings it back. **`flask prune-files`** (§13) cleans up
what earlier versions left: records no note links to, and files in
`UPLOAD_DIR` with no record (an upload that failed half-way). (Files
used to be kept forever, even when nothing used them.)

### 9.3 Rendering

Attachments render as a list (filename, size, **Copy**) below a note's
body on both the standalone view and the inline feed expansion, and as
an editable list (**Insert**, **Copy**, **Remove**) under the editor in
Edit mode. Images can also be shown in the text itself (§9.5).

**Opening a file** (`/files/<hash>`, §14): images (PNG, JPEG, GIF, WebP,
AVIF, BMP), PDFs, plain text, audio and video open in the browser;
everything else downloads under its stored name. HTML and SVG are the
reason: either can carry scripts, and opened from the app's own address
a script could read and change every note. Every file is sent with
`X-Content-Type-Options: nosniff`, so the browser uses the stored type
rather than guessing from the bytes (an HTML file uploaded as
`fake.png` is treated as an image), and with
`Content-Security-Policy: sandbox`, so whatever opens runs no scripts
and can't reach the app. PDFs are the one exception to the sandbox:
browsers' built-in PDF viewers may refuse to open under it.

**Attaching is by drag and drop**: in Edit mode, drop one or more files
anywhere on the page. There is no file picker or upload button (removed
in favor of dropping); the Attachments section just says "Drop a file
anywhere on the page to attach it."

- While a file is dragged over the window, an overlay says what dropping
  will do: "Drop to attach to No. 12" ("Drop to save the note and
  attach" on a new note, which is saved first), or why it won't --
  "Switch to Edit to attach files" in View mode. A refused drop leaves
  that explanation up for a couple of seconds, since View has no status
  line.
- Dropped images also go into the text at the cursor (§9.5); other files
  are only attached.
- The browser's own reaction to a dropped file — opening it in place of
  the page, which would lose unsaved text — is always cancelled on the
  note page, in either mode.
- Only drags that carry files count; dragging selected text within the
  editor behaves normally.
- Several files dropped together upload one after another, with
  progress ("Uploading data.csv (2 of 3)…") and one summary at the end
  ("Attached 3 files.", or each problem by name).

Uploading and removing happen **in place, without reloading the page**,
so unsaved text in the editor is never lost:

- The browser sends the file with `fetch` and an `X-Requested-With:
  fetch` header. The server does the same work as for a plain form
  submit, but answers with JSON — `ok`, a `message`, and the freshly
  rendered attachment list (`_attachments_edit.html`) — which the page
  swaps in. Without that header (e.g. JavaScript off) the same routes
  flash a message and redirect back to the edit page, as before.
- Attaching a file never saves the note's text, except on a new note,
  which needs an id first: dropping or pasting onto it saves it, then
  attaches. (Saving every time was considered and rejected: it would
  store half-finished text whenever a file is attached.)
- Result messages appear on the "drop a file" line: "Attached
  figure-3.png.", "Removed dataset.csv from this note.", "figure-3.png
  is already attached." (same bytes uploaded to the same note again), or
  an error. When the uploaded bytes already exist under another name,
  the message and list use the stored name, since dedup (§9.1) reuses
  the existing file.
- Files over the upload limit (`MAX_CONTENT_LENGTH`, 50 MB) are rejected
  in the browser before uploading, naming the file and the limit; other
  files in the same drop still upload. The server enforces the same
  limit (413) in case the browser check is bypassed.
- Each upload also returns a fresh View pane, so flipping to View shows
  the new attachment list even if the text wasn't changed.
- A new, never-saved note has no attachment section until that first
  save. Its sort date defaults to today (local time).

### 9.4 Discovery: `has:file` + count badge

Two complementary ways to find notes with attachments, added together
because they solve different problems:

- **`has:file`** (a search filter, §7, and a shortcut under Views in
  the feed's sidebar): only notes with at least one attachment, in feed
  order unless there are words to rank by, combinable with the rest of
  a search (`has:file #paper after:2025`). Answers "show me only
  these." It was a separate Attachments tab (`/attachments`, which now
  redirects to `/?q=has:file`).
- **Attachment-count badge**: every card everywhere shows a `N file(s)` meta-item alongside the id/date/
  line-count stamp whenever a note has at least one attachment — nothing
  rendered when it has none. (The `N backlinks` badge, §6.5, works the
  same way.) Answers "does this one have anything?"
  without needing to open a separate view. Counts are fetched in one
  grouped query for the notes on the page
  (`db.get_attachment_counts(ids)`, `note_id -> count`), not one query
  per note.

An explicit design choice made alongside this: attachments are **not**
represented as an automatic/synthetic label (e.g. an implicit
`#has-attachment`). Labels are 100% user-authored — a label exists
because someone typed `#word` in a note's text — and every label is
resynced from parsed body text on every save (§6.3). Injecting a
system-derived label into that same namespace would either get wiped on
the next save (since it isn't actually in the text) or need special-
casing that breaks the "labels = literally typed in the note" invariant,
and it would be visually indistinguishable from a real label in the
label list. A dedicated filter keeps system-derived "notes with X" facts
structurally separate from user-authored vocabulary.

### 9.5 Images in the text

A note shows a stored image with standard image markdown pointing at it
by hash: `![Gull Rock layout, v4](/files/191ff6f6b235)`.

- **The hash, not the name.** The reference is the first 12 hex
  characters of the file's SHA-256 (48 bits: two of one person's files
  sharing them is vanishingly unlikely; if they did, the reference shows
  as ambiguous rather than picking one). Any 12 to 64 characters work, in
  either case. `/files/<hash>` serves a file by such a prefix too, so
  `[the PDF](/files/4372a0cb0d86)` is a plain link to one.
- **Any note can show any stored file**, not only its own: the hash names
  the file wherever it was uploaded.
- **Showing a file attaches it.** Saving a note whose text points at a
  stored file -- `![…](/files/…)` or `[…](/files/…)`, outside code and
  math -- attaches that file to it (logged in History like any attach).
  So it's listed under the note, counted by `has:file`, and kept while
  this note uses it even if the note it came from is deleted for good.
  Taking the reference out of the text doesn't detach it: removing is
  Remove, as always. If the file is removed while the text still points
  at it, the reference shows as missing.
- **What shows:** a stored PNG, JPEG, GIF, WebP, AVIF or BMP shows as an
  image, never wider than the note, linking to the full-size file. Any
  other stored file -- SVG included, which can carry scripts -- shows as
  a link to it. A prefix that names no stored file, or more than one, is
  shown as typed, in red, saying which. A stored image whose file isn't
  on disk shows "Missing image: <caption>" (§9.2). `![…](https://…)` is not an image:
  nothing is ever loaded from another site (offline it would break, and
  online it would tell that site what you read). Without a lookup
  (`md.render` called without `files`, e.g. feed card titles), an image
  reference is a plain link with its caption.
- **A line that's just an image is a figure** (`<figure>`), its caption
  (the alt text) below it, even between other lines of a paragraph; an
  image in a sentence stays inline. The caption is plain text: no labels
  or links inside it.
- **Plain text** (titles inside links, backlink passages, History) shows
  the caption.
- **Rendering** sets images aside before links, labels and emphasis, like
  other links. The lookup is one indexed range read per reference
  (`db.files_by_prefix`), for the note being shown only.

**Putting an image in:**

- **Drop or paste** (Edit mode): a dropped image -- or a pasted one, a
  screenshot or "Copy image" from anywhere -- goes into the text at the
  cursor, on a line of its own. A placeholder (`![Uploading x.png…](uploading:1)`)
  marks the spot at once and becomes `![caption](/files/<hash>)` when the
  upload finishes, without moving the cursor if you've typed on
  meanwhile; if it fails, the placeholder goes. The caption starts as the
  file's name without its extension, dashes and underscores as spaces;
  pasted images are named by when (`pasted-2026-10-06-1432.png`) and
  captioned "figure". Files that aren't images are only attached.
  Dropped onto the cursor, not the pointer: a textarea can't say which
  character the pointer is over.
- **Insert** (Edit mode, each attachment) puts the file at the cursor:
  an image as `![…](…)`, anything else as a link.
- **Copy** (everywhere attachments are listed) puts the same text on the
  clipboard, to paste into any note. Over plain `http` on the network the
  browser's Clipboard API isn't available, so the older copy command is
  used.

## 10. Graph views

**A graph always belongs to a note.** It's reached only from a note's
page ("View graph", §4.3) and shows the notes related to that note.
There is deliberately no whole-collection graph: it was removed to keep
the app smaller, and past a few hundred notes it was a hairball rather
than something to read.

Rendered with Cytoscape.js (bundled, §2), reading from a small JSON
endpoint (`/api/graph/<id>`). The canvas takes the full
window width and height below the top bar; scroll zooms, dragging the
background pans, and nodes can be dragged.

The endpoint works outward from the note one hop at a time, asking only
for the links of the notes it has just reached (and finally for links
among the outermost ring), rather than loading every link in the
database — so a 1-hop graph costs the same in a large collection as in
a small one.

Layout and legibility:

- Force-directed layout ("cose"), tuned for labeled boxes rather than
  dots: node size includes the label, and edge length and repulsion are
  set so boxes don't stack on top of each other. Measured on sample data:
  no overlaps on small graphs; on a 60-note graph, about one run in six
  leaves a single overlapping pair (the layout starts from random
  positions), at a zoom level where labels are hidden anyway.
- The initial view fits the whole graph, but never zooms in past 130%,
  so a two-note graph isn't blown up to fill the window.
- Labels that would render smaller than 8px are hidden rather than drawn
  as smudges. A large graph therefore opens as an overview of boxes and
  links; labels appear as you zoom in.
- Hovering any node shows its full label in a tooltip, so notes can be
  identified from the overview without zooming.

- **`/graph/<id>`** (default 1 hop, 1–3 selectable): centered on the
  note, showing only notes within N hops via breadth-first traversal over
  the link adjacency (undirected for traversal purposes, since "is
  connected to" should surface both incoming and outgoing neighbors).
  "Back to note" returns to the note in View.
- **At most 200 notes** (`db.GRAPH_MAX_NODES`). If the next ring of notes
  would take the graph past that, only part of the ring is shown — the
  notes with the most links to what's already drawn, then the lowest
  numbers — and the walk stops there. The toolbar then says so:
  "Showing the nearest 200 notes; 248 more at that distance (and any
  beyond) aren't shown. Fewer hops shows a complete graph." A ring is
  never dropped in favour of a farther one, so what's shown is always
  the nearest part of the neighbourhood.
- Why these limits: the layout's cost grows faster than the number of
  notes. On ten years of notes, a hub note's graph was 52 notes at 1
  hop, 448 at 2 and 2,973 at 3; the old 4- and 5-hop options drew 814
  and 4,417 notes and froze the tab for 21 s and over 10 minutes.
  Capped at 200, the same hub's 2- and 3-hop graphs take about 1.5 s to
  lay out in Chromium (a 300-note cap, the first choice, took about 3 s),
  and a typical note's 3-hop graph (149 notes) under 2 s. Old `?hops=4`/`5` addresses
  show 3.
- **Connected** means the note links out to anything — a missing or
  deleted target counts, since the graph shows it as a ghost — or a note
  that isn't in Trash links to it (`db.note_has_connections`). A note
  that isn't connected has nothing to draw, so its View graph button is
  greyed out with "No connections yet…" on hover, and its graph address
  shows that message instead of a lone box. The button updates after
  every save, so adding a note's first link enables it without a reload.
  `is:unlinked` (§8) uses the same rule.
- Edges are **directed** (arrowheads matter: A→B is a different fact
  than B→A).
- A `[[link]]` to a note that doesn't exist produces a **ghost node**
  (dashed border, distinct color, not clickable) rather than being
  silently omitted as an edge — same reasoning as the ghost styling in
  the renderer (§6.2): broken references should be visible, not hidden.
  A ghost node's existence is entirely implied by an edge pointing at
  it, so "only show nodes with at least one connection" falls out
  automatically with no special-casing.
- Clicking a real node navigates to that note's standalone page.
  Clicking a ghost node does nothing (there's nowhere to go).
- **Labels are not represented on the graph at all** — no label-based
  edges, no label nodes. Mixing "explicitly linked" and "happens to
  share a tag" into one graph was considered and rejected early on: it
  makes the graph noisier and conflates two different kinds of
  relationship. Labels stay a feed-filtering mechanism only (§6.4).

## 11. History (activity log)

`/history` lists what you've done, newest first. Its main use is
answering "what was I working on?" — which the feed can't, because the
feed is ordered by sort date, which you set by hand: editing a note from
last March today leaves it in March on the feed, but puts it at the top
here.

### 11.1 What's recorded

| Event | Entry |
|---|---|
| New note | **Created** No. 11 Title |
| Save that changes something | **Edited** No. 6 Title, with what changed (below) |
| Delete / restore | **Deleted** / **Restored** No. 6 Title |
| Attach / remove a file | **Attached** `fig.png` to No. 6 Title / **Removed** `fig.png` from … |

An edit's changes: lines added and removed, labels added and removed,
`[[links]]` added and removed ("now links to [[5]]", "no longer links to
[[3]]"), a changed sort date, how many saves were grouped into it, and
"restored the version from …" for a restore (§11.5).

Not recorded: viewing, searching, filtering, the graph — anything that
doesn't change a note. A save that changes nothing that counts isn't
recorded either (e.g. pressing Ctrl+S twice, or only blank lines or
trailing spaces changing). Uploading a file the note already has isn't
recorded (§9.3).

Line counting works like `git diff --stat`: a line that changed counts as
one removed plus one added, so rewording a sentence reads "+1 / −1
lines". Blank lines and trailing whitespace are ignored, matching the
line count shown on cards (§4). When only one side changed, only that
side is shown ("+3 lines", "−2 lines").

### 11.2 Editing sessions

Pressing Ctrl+S every minute while writing would otherwise produce a wall
of "Edited" entries. Instead:

- Saves to the same note less than **15 minutes** after the previous save
  to it (`activity.SESSION_WINDOW`) extend one "Edited" entry. The window
  slides: each save extends it.
- The entry's changes are measured from the note **as it was when the
  session began**, not summed per save — so adding a line and deleting it
  again in the same session nets out to nothing. A session whose net
  change is nothing (you undid everything) is removed entirely.
- Saves within 15 minutes of **creating** a note fold into its "Created"
  entry, since writing a new note usually means several saves.
- To measure against the session's start, the entry holds the note's
  starting text (`base_body`, `base_sort_date`). It stays after the
  session closes: it's the note's previous version (§11.5). (It used to
  be cleared once the session closed, by an update that every save ran
  over the whole log — 13 ms a save at ten years; saving now takes
  about 5 ms.)
- The entry's time is its last save.

### 11.3 The page

- Entries grouped under local-day headings — "Today", "Yesterday", then
  "Tuesday 22 September 2026" — with local 12-hour times. Timestamps are
  stored in UTC (§13).
- Each entry links to its note. A note that's currently deleted is shown
  greyed and unlinked (its page would 404), with an "in Trash" link to the
  Trash view. An edit also links to the note as it was before it ("see
  before", §11.5).
- Sidebar filters: All activity, New notes, Edits, Link changes (edits
  that added or removed a `[[link]]`), Attachments, Deleted & restored.
  Paginated like the other lists (§4.2); page links keep the filter.
- In the top nav between Graph and Trash.

### 11.4 What the log covers

Everything done through the app since the database was created. Nothing
is reconstructed afterwards: a note added some other way (straight into
the database, say) has no entries until it's next changed in the app.
(Startup used to fill in "Created" and "Deleted" entries for notes that
had none; that went with the rule that startup doesn't touch existing
data, §13.)

### 11.5 Versions

Every editing session (§11.2) keeps the note as it was when the session
began — which is the note as the previous session, or creating it, left
it. So a note has **one version per sitting**, however often you saved:
come back to a note three times and it has three earlier versions.
Nothing is stored for a note that's never edited after the sitting it
was created in.

- **Versions page** (`/notes/<id>/versions`, the *Versions* button on a
  note in View and Edit): "Now", then each earlier version newest first
  — when it was last saved, its sort date and line count, and what the
  next sitting changed ("then, 3 Oct 2026, 2:14 PM: +6 / −7 lines, added
  #memory").
- **A version** (`/notes/<id>/versions/<entry id>`): the text rendered as
  the note would be (refs by title, §6.2), and "What's different now": a
  line diff from it to the current text, plus the sort date if that
  changed.
- **Restore** saves the version's text and sort date as an edit. It
  always starts a session of its own — even within 15 minutes of the
  last save — so the text it replaces becomes the newest version, and
  restoring that undoes it. History shows it as an edit, "restored the
  version from …". A version identical to the note now can't be
  restored (nothing would change). Labels, links and search follow the
  restored text like any save (§6.3).
- **Size**: one copy of a note per sitting. At 10 notes a day, with a
  third of notes edited in a later sitting, ten years of versions add
  about 10 MB (58.6 → 68.4 MB in `scripts/benchmark.py`'s data); each
  page reads only its own note's entries (`idx_activity_note`), about
  2 ms.
- Versions go with their note: a note in Trash has none to show (its
  pages 404), and they're back when it's restored. Databases from before
  this keep no versions for sessions that had already closed.

## 12. Explicitly out of scope

Called out here so a future contributor doesn't wonder if these were
overlooked:

- **Auth / multi-user.** Single-user, no-auth is the design, not a
  placeholder.
- **Creating a note from the `[[` pop-up, or `[[+]]` for "the next
  note".** The number isn't reserved until the note exists, so the
  link can end up pointing at the note being written, or whichever
  note is created next. `[[later: hint]]` (§6.2) marks the gap instead,
  and `has:later` finds it again.
- **Folgezettel-style branching ids** (`1`, `1a`, `1a1`, ...). Notes
  have plain sequential integer ids. Explicit `[[links]]` + backlinks +
  the graph view cover the associative purpose Folgezettel numbering
  served in a paper Zettelkasten, more flexibly, in a digital tool.
- **Structure notes / MOCs are not special-cased.** A structure note is
  just an ordinary note whose body happens to be mostly a curated list
  of `[[links]]` with commentary. No auto-pinning, no dedicated type.
- **Related-notes-by-shared-label.** Considered, rejected: redundant
  with the label filter, and would add a second, weaker-signal
  discovery mechanism next to backlinks (a strong, explicit signal).
- **Images from other sites.** Only the app's own stored files show as
  images (§9.5).
- **An "attach an existing file" search/picker UI.** Removed after
  initial build — hash-based dedup on upload already provides reuse
  without needing a picker, and a search box across what's expected to
  be a small number of attachments added complexity without adding
  capability (see git history / conversation log for the original
  attempt).
- **A whole-collection graph.** Removed; a graph always belongs to a
  note (§10).
- **Separate View and Edit pages.** Replaced by one page per note with a
  switch (§4.3).
- **Infinite scroll.** Rejected in favor of numbered pagination; see
  §4.2 for why.
- **Phone / narrow-window layouts.** Desktop is the target (§1).
- **Keyboard shortcuts beyond Ctrl+S.** Considered (search, moving
  between cards, expand, edit, new note) and left out by choice.
- **A version for every save.** Versions are per sitting (§11.5): saving
  every few seconds while writing would otherwise keep hundreds of
  near-identical copies.

## 13. Configuration & deployment

- `run.py`: standalone launcher, reads `.env` (via `python-dotenv`),
  starts the Flask dev server. `PORT` / `HOST` / `SECRET_KEY` /
  `DATA_DIR` / `PAGE_SIZE` / `ALLOWED_HOSTS` are the configurable values
  (see `.env.example`). `PAGE_SIZE` must be a positive integer; anything
  else falls back to the default of 50. `ALLOWED_HOSTS` is only needed
  to reach the app by a public domain name (§14).
- `Dockerfile` + `docker-compose.yml`: `PORT` flows through to both the
  container's bound port and the host port mapping; `./data` is a bind
  mount so the SQLite file and `uploads/` survive rebuilds. `TZ` (e.g.
  `America/New_York`) sets the container's time zone; without it the
  container runs in UTC.
- Timestamps (`created_at`, `updated_at`, `deleted_at`) are stored in
  UTC. Dates shown to the person — a new note's default sort date, the
  deletion date in Trash — use the local time zone of the machine running
  the app, which for this desktop app is the user's own.
- **Startup only creates what's missing.** `schema.sql` runs on every
  start with `CREATE TABLE/INDEX/TRIGGER IF NOT EXISTS` (plus
  `DROP INDEX IF EXISTS` for two indexes a newer one replaced, §4.2),
  and that's all: startup never reads or rewrites existing data, so it
  stays instant
  however many notes there are and a restart changes nothing in the file
  (the search index, §7, and History, §11.4, used to be back-filled on
  every start).
- **No migration code.** A change to an existing table's shape applies to
  new databases; an existing one keeps working with what it has. What's
  derived from note text — labels, links and the search index — can
  always be rebuilt from scratch with `flask reindex` (below), which is
  how an existing database picks up a change in how labels or links are
  read. The label tables' one-time migration from their old two-table
  shape (§3) has been removed.
- **`flask reindex`** (`docker compose exec research-notes flask --app app
  reindex` under Docker) rebuilds `note_labels`, `note_links` and the
  search index from the text of every note not in Trash, in one
  transaction — about 2 s for 36,500 notes. `--vacuum` then compacts the
  file, which rewrites all of it once (a full-size backup, once) and
  gives back space a grown search index left behind. Nothing runs it
  automatically. A database from before notes in Trash were left out of
  labels, links and search (§3) still holds them for notes already in
  Trash; one `flask reindex` clears that up.
- **`flask prune-files`** deletes uploaded files no note uses, and their
  records (§9.2). Since files are now deleted as soon as nothing uses
  them, it's only needed once, for files earlier versions left behind.

## 14. Network safety

The app has no login (§1), so it can't tell people apart. What it can
tell apart is the app's own pages from *other web pages*: any page open
in a browser on the network could otherwise send that browser to the
app, and the browser would carry the request out. Two checks, in
`security.py`, close that off without anything to configure or click:

- **Changes must come from the app's own pages.** Every POST (or other
  changing request) is refused with 403 when the browser reports it came
  from another site: an `Origin` header that doesn't match the address
  the request was sent to (default ports ignored; `null` refused), or,
  without one, a `Sec-Fetch-Site` other than `same-origin` or `none`.
  Requests carrying neither header — curl, scripts — are allowed: they
  aren't a browser acting for some other page. Reading is unaffected.
- **The address must be a local one.** Every request is refused with 400
  unless the `Host` it was sent to is an IP address, `localhost`, a name
  without dots (`myserver`), or a name under a suffix that can't be
  registered publicly (`.local`, `.lan`, `.home`, `.home.arpa`,
  `.internal`, `.corp`, `.localhost`). A public domain name there means
  a web page has pointed its own domain at this server (DNS rebinding),
  which would make it the same site as the app and get it past the first
  check — able to read notes, too. Names that should work anyway go in
  `ALLOWED_HOSTS` (comma-separated; a leading dot allows every name under
  it, e.g. `.example.com`). The 400 page says so.

Two more places a note's content could turn into code are handled where
they render: links only become clickable for safe schemes (§5), and
attached files that could run scripts download instead of opening (§9.3).

Out of scope, deliberately: login, accounts, TLS. Docker publishes the
port on every interface so other machines on the network can use the app.

## 15. Future work

Ideas that fit the design and have a worked-out shape, but aren't built
yet. Unlike §12, these are wanted.

### 15.1 DOI and arXiv identifiers as links

**How sources work today.** A paper, book or talk gets a note of its
own -- a literature note: its title line names it (`# Smith 2021 — Sleep
and memory`), the body holds the citation, a link to the paper
(`[doi](https://doi.org/10.1038/...)` or a bare `https://` address) and
what you took from it, and a label such as `#ref-paper` groups them.
Other notes cite it with `[[id]]`, so its backlinks list every note that
draws on it, with the passage around each citation (§6.5), and the `[[`
pop-up finds it by title. Nothing here needs a reference manager.

**The gap.** Researchers copy identifiers, not addresses: `doi:10.1038/
s41586-021-03819-2`, `arXiv:2401.00001`. Today those are plain text, so
the link has to be written out by hand.

**Proposal.** In running text, an identifier with its prefix becomes a
link, shown exactly as typed:

| Typed | Links to |
|---|---|
| `doi:10.1038/s41586-021-03819-2` | `https://doi.org/10.1038/s41586-021-03819-2` |
| `arXiv:2401.00001`, `arXiv:2401.00001v2` | `https://arxiv.org/abs/2401.00001` (version kept) |
| `arXiv:hep-th/9901001` (pre-2007 form) | `https://arxiv.org/abs/hep-th/9901001` |

- **The prefix is required**, any capitalization (`doi:`, `DOI:`,
  `arxiv:`). A bare `10.1038/...` or `2401.00001` is too easy to hit by
  accident (version numbers, dates, figures) and stays text.
- **Patterns.** DOI: Crossref's recommended form,
  `10\.\d{4,9}/[-._;()/:a-z0-9]+` (case-insensitive) -- covers nearly
  every DOI issued. arXiv: `\d{4}\.\d{4,5}(v\d+)?` and the old
  `[a-z-]+(\.[a-z]{2})?/\d{7}(v\d+)?`. The prefix may not follow a letter
  or digit (`xdoi:` isn't one).
- **Where in the renderer:** in `_render_inline`, next to the bare-address
  autolink and stashed the same way (`LINK`), so emphasis, labels and
  `[[refs]]` can't reach into one. Trailing punctuation is left out with
  the same rule as addresses (`_URL_TRAILING`, and a `)` that doesn't
  close a `(` inside the DOI -- DOIs can contain brackets). The DOI is
  percent-encoded for the `href` (`quote(doi, safe="/:;()")`): DOIs may
  contain `#`, `?` or `<`, which would otherwise change the address.
- **Unchanged:** inside code or math it stays as typed (both are set
  aside first); in plain-text places (titles in links, backlink passages,
  History) it shows as typed; search indexes it as typed, so
  `doi:10.1038` finds it -- `doi:` is not a search operator.
- **Tests:** each pattern linked with the right `href`; prefix required;
  trailing `.`, `,` and `)` left out; brackets inside a DOI kept; nothing
  inside code or math; the property tests' alphabet gets `doi:10.1/x` and
  `arXiv:` so "what's shown is what's stored" keeps holding.
- **Left out on purpose:** fetching a paper's title from Crossref or the
  arXiv API to show it in place of the identifier. That needs the server
  to reach the internet on every render or save, which the app otherwise
  never does (§14), and offline it would fail. The literature note's
  title line already gives the human-readable name.

Effort: about 30 lines in `markdown.py` plus tests, and a row in §5's
table.
