import hashlib
import math
import mimetypes
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.utils import secure_filename

from . import activity
from . import db
from . import markdown as md
from . import search

bp = Blueprint("notes", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def _note_view_model(row):
    existing_ids = db.existing_note_ids(md.extract_note_refs(row["body"]))
    return {
        "id": row["id"],
        "body": row["body"],
        "sort_date": row["sort_date"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "line_count": md.line_count(row["body"]),
        "html": md.render(row["body"], existing_ids),
    }


def _card_items(notes, matches=None):
    """Build the per-card dicts used by every list of notes (the feed,
    with or without a search) so cards look and behave the same however a
    list was reached. Counts come from one grouped query each, not one per card, and
    every lookup is about these cards only, so a page costs the same
    however many notes there are."""
    ids = [n["id"] for n in notes]
    existing_ids = db.existing_note_ids(
        {ref for n in notes for ref in md.extract_note_refs(n["body"])}
    )
    attachment_counts = db.get_attachment_counts(ids)
    backlink_counts = db.get_backlink_counts(ids)
    return [
        {
            "id": n["id"],
            "sort_date": n["sort_date"],
            "line_count": md.line_count(n["body"]),
            "snippet_html": md.render_first_line(n["body"], existing_ids),
            "attachment_count": attachment_counts.get(n["id"], 0),
            "backlink_count": backlink_counts.get(n["id"], 0),
            # the passage that matched a search, highlighted (HTML)
            "match_html": (matches or {}).get(n["id"]),
        }
        for n in notes
    ]


def _backlink_items(note_id):
    """Each note linking to `note_id`: its first line as a title, plus the
    passage around every mention of [[note_id]] in it (why it links here)."""
    return [
        {
            "id": b["id"],
            "sort_date": b["sort_date"],
            "text": md.first_line_text(b["body"]),
            "mentions": md.ref_contexts(b["body"], note_id),
        }
        for b in db.get_backlinks(note_id)
    ]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def _requested_page() -> int:
    page = request.args.get("page", default=1, type=int)
    return page if page and page > 0 else 1


def _page_window(page: int, total_pages: int) -> list:
    """Page numbers to show: first, last, and two either side of the
    current page, with None marking a gap ("..."). A gap of exactly one
    page is filled in rather than shown as "...", since "1 ... 3" would
    be sillier than just "1 2 3"."""
    wanted = {1, total_pages} | set(range(page - 2, page + 3))
    nums = sorted(n for n in wanted if 1 <= n <= total_pages)
    out = []
    for n in nums:
        if out and n - out[-1] == 2:
            out.append(n - 1)
        elif out and n - out[-1] > 2:
            out.append(None)
        out.append(n)
    return out


def _pager(endpoint: str, page: int, total: int, **params) -> dict:
    """Everything a template needs to render page navigation. `params`
    are the other query args to carry across pages (search text, label);
    None values are dropped from the URLs."""
    size = current_app.config["PAGE_SIZE"]
    total_pages = max(1, math.ceil(total / size))

    def url(p):
        return url_for(endpoint, page=(p if p > 1 else None), **params)

    shown_page = min(page, total_pages)
    first = (shown_page - 1) * size + 1 if total else 0
    last = min(shown_page * size, total)
    return {
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "first": first,
        "last": last,
        "prev_url": url(page - 1) if 1 < page <= total_pages else None,
        "next_url": url(page + 1) if page < total_pages else None,
        "last_page_url": url(total_pages),
        "links": [
            {"gap": True} if n is None
            else {"num": n, "url": url(n), "current": n == page}
            for n in _page_window(shown_page, total_pages)
        ],
    }


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------

# Shortcuts in the feed's sidebar: each fills in its filter (spec §7).
VIEWS = [
    ("is:unlinked", "Unlinked notes", "no [[links]] in or out"),
    ("has:file", "Notes with files", "at least one attachment"),
]


@bp.route("/")
def feed():
    raw = request.args.get("q", "").strip()
    legacy_label = request.args.get("label")
    if legacy_label:
        # /?label=x links from before labels moved into the search box.
        return redirect(url_for("notes.feed", q=search.toggle(raw, "#" + legacy_label.lower())))
    query = search.parse(raw)

    page = _requested_page()
    size = current_app.config["PAGE_SIZE"]
    notes, total = db.search_page(query, size, (page - 1) * size)

    pager = _pager("notes.feed", page, total, q=raw or None)
    if page > pager["total_pages"]:
        return redirect(pager["last_page_url"])

    # One link per label, so built directly: url_for for each of a few
    # hundred labels took 5 ms. Encoded the way url_for would.
    base = url_for("notes.feed")

    def toggle_url(token):
        q = search.toggle(raw, token)
        return base + "?q=" + quote_plus(q, safe="!$'()*,/:;?@") if q else base

    labels = [
        {"name": l["name"], "count": l["count"], "url": toggle_url("#" + l["name"]),
         "active": l["name"] in query.labels}
        for l in db.get_labels_with_counts()
    ]
    views = [
        {"label": label, "hint": hint, "url": toggle_url(token), "active": search.has_token(raw, token)}
        for token, label, hint in VIEWS
    ]
    return render_template(
        "feed.html",
        notes=_card_items(notes, db.search_snippets(query, [n["id"] for n in notes])),
        labels=labels,
        views=views,
        query=raw,
        problems=query.problems,
        pager=pager,
        # a just-created note to scroll to and briefly highlight (feed.js)
        focus=request.args.get("focus", type=int),
    )


@bp.route("/random")
def random_note():
    note_id = db.get_random_note_id()
    if note_id is None:
        flash("No notes yet -- create one first.")
        return redirect(url_for("notes.feed"))
    return redirect(url_for("notes.view_note", note_id=note_id))


# Orphans and Attachments became search filters; their old addresses still work.
@bp.route("/orphans")
def orphans():
    return redirect(url_for("notes.feed", q="is:unlinked"))


@bp.route("/attachments")
def attachments_tab():
    return redirect(url_for("notes.feed", q="has:file"))


@bp.route("/history")
def history():
    show = request.args.get("show", "all")
    if show not in db.ACTIVITY_FILTERS:
        show = "all"
    page = _requested_page()
    size = current_app.config["PAGE_SIZE"]
    rows, total = db.activity_page(show, size, (page - 1) * size)
    pager = _pager("notes.history", page, total, show=(None if show == "all" else show))
    if page > pager["total_pages"]:
        return redirect(pager["last_page_url"])

    filters = [
        {
            "label": label,
            "url": url_for("notes.history", show=(None if key == "all" else key)),
            "active": key == show,
        }
        for key, (_, label) in db.ACTIVITY_FILTERS.items()
    ]
    return render_template(
        "history.html",
        days=_history_days(rows),
        filters=filters,
        filter_label=db.ACTIVITY_FILTERS[show][1],
        show=show,
        pager=pager,
        window_minutes=int(activity.SESSION_WINDOW.total_seconds() // 60),
    )


def _history_days(rows) -> list:
    """Group activity rows (already newest first) under local-day headings:
    "Today", "Yesterday", then "Monday 21 September 2026"."""
    today = datetime.now().astimezone().date()
    days: list = []
    for r in rows:
        local = datetime.fromisoformat(r["updated_at"]).astimezone()
        day = local.date()
        if day == today:
            heading = "Today"
        elif day == today - timedelta(days=1):
            heading = "Yesterday"
        else:
            heading = f"{local:%A} {local.day} {local:%B %Y}"  # no %-d: not on Windows
        item = {
            "kind": r["kind"],
            "time": _clock_time(local),
            "note_id": r["note_id"],
            "title": md.first_line_text(r["note_body"]) or "(empty note)",
            "in_trash": r["note_deleted_at"] is not None,
            **activity.describe(r["kind"], r["detail"], r["save_count"]),
        }
        if not days or days[-1]["heading"] != heading:
            days.append({"heading": heading, "items": []})
        days[-1]["items"].append(item)
    return days


@bp.route("/trash")
def trash():
    # Timestamps are stored in UTC; show the deletion date in the local
    # time of the machine running the app, which for this desktop app is
    # the user's own.
    notes = [
        {"id": n["id"], "deleted_on": _local_date(n["deleted_at"])}
        for n in db.list_deleted_notes()
    ]
    return render_template("trash.html", notes=notes)


def _local_date(iso_utc: str) -> str:
    return datetime.fromisoformat(iso_utc).astimezone().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Note CRUD
# ---------------------------------------------------------------------------

def _clock_time(dt: datetime) -> str:
    """12-hour local time, e.g. "9:05 AM" (no %-I: not available on Windows)."""
    local = dt.astimezone()
    return f"{local.hour % 12 or 12}:{local:%M} {'AM' if local.hour < 12 else 'PM'}"


def _feed_focus_url(note_id: int) -> str:
    """The feed page this note appears on (default order, no filters),
    asking the feed to scroll to it and briefly highlight it."""
    page = db.feed_position(note_id) // current_app.config["PAGE_SIZE"] + 1
    return url_for("notes.feed", page=(page if page > 1 else None), focus=note_id)


def _render_note_page(row, mode, *, body=None, sort_date=None, status=200):
    """The note page (note.html) in View or Edit mode. `row` is None for a
    note that doesn't exist yet. body/sort_date override the editor's
    contents (used to re-show what was typed after a rejected save)."""
    ctx = {"mode": mode, "labels": db.get_labels_with_counts(), "feed_url": url_for("notes.feed")}
    if row is None:
        ctx.update(
            note=None,
            started_new=True,
            body=body or "",
            sort_date=sort_date if sort_date is not None else db.today_str(),
            has_connections=False,
        )
    else:
        note_id = row["id"]
        ctx.update(
            note=_note_view_model(row),
            started_new=False,
            body=row["body"] if body is None else body,
            sort_date=row["sort_date"] if sort_date is None else sort_date,
            attachments=db.list_note_attachments(note_id),
            backlinks=_backlink_items(note_id),
            has_connections=db.note_has_connections(note_id),
        )
    return render_template("note.html", **ctx), status


def _saved_response(note_id: int, created: bool = False):
    """JSON for an in-place save: what the page needs to update itself
    (fresh View pane, sidebar details, graph button state), plus -- for the
    save that created the note -- the pieces that only exist once a note
    has an id (attachment editor, graph/delete buttons, its URLs)."""
    row = db.get_note(note_id)
    note = _note_view_model(row)
    has_connections = db.note_has_connections(note_id)
    payload = {
        "ok": True,
        "note_id": note_id,
        "saved_at": _clock_time(datetime.now()),
        "sort_date": note["sort_date"],
        "line_count": note["line_count"],
        "has_connections": has_connections,
        "feed_url": _feed_focus_url(note_id),
        "view_html": render_template(
            "_note_view_pane.html",
            note=note,
            attachments=db.list_note_attachments(note_id),
            backlinks=_backlink_items(note_id),
        ),
    }
    if created:
        payload.update(
            update_url=url_for("notes.update_note", note_id=note_id),
            view_url=url_for("notes.view_note", note_id=note_id),
            edit_url=url_for("notes.edit_note_form", note_id=note_id),
            attachments_html=render_template(
                "_attachments_section.html", note_id=note_id, attachments=[]
            ),
            common_html=render_template(
                "_note_common_actions.html", note_id=note_id, has_connections=has_connections
            ),
        )
    return jsonify(payload)


_BAD_DATE = "Sort date must be a valid date (YYYY-MM-DD)."


@bp.route("/notes/new", methods=["GET"])
def new_note_form():
    return _render_note_page(None, "edit")


@bp.route("/notes/new", methods=["POST"])
def create_note():
    body = request.form.get("body", "")
    sort_date = request.form.get("sort_date", "").strip()
    if not _valid_date(sort_date):
        if _wants_json():
            return jsonify(ok=False, message=_BAD_DATE), 400
        flash(_BAD_DATE)
        return _render_note_page(None, "edit", body=body, sort_date=sort_date, status=400)
    note_id = db.create_note(body, sort_date)
    if _wants_json():
        return _saved_response(note_id, created=True)
    return redirect(_feed_focus_url(note_id))


@bp.route("/notes/<int:note_id>")
def view_note(note_id):
    row = db.get_note(note_id)
    if row is None:
        abort(404)
    return _render_note_page(row, "view")


@bp.route("/notes/<int:note_id>/fragment")
def note_fragment(note_id):
    """Full rendered body, attachments and backlinks for a note, as a bare
    HTML fragment (no page chrome) -- used to expand a note inline on the
    feed-style lists."""
    row = db.get_note(note_id)
    if row is None:
        abort(404)
    note = _note_view_model(row)
    attachments = db.list_note_attachments(note_id)
    return render_template(
        "note_fragment.html",
        note=note,
        attachments=attachments,
        backlinks=_backlink_items(note_id),
    )


@bp.route("/notes/<int:note_id>/edit", methods=["GET"])
def edit_note_form(note_id):
    row = db.get_note(note_id)
    if row is None:
        abort(404)
    return _render_note_page(row, "edit")


@bp.route("/notes/<int:note_id>/edit", methods=["POST"])
def update_note(note_id):
    row = db.get_note(note_id)
    if row is None:
        abort(404)
    body = request.form.get("body", "")
    sort_date = request.form.get("sort_date", "").strip()
    if not _valid_date(sort_date):
        if _wants_json():
            return jsonify(ok=False, message=_BAD_DATE), 400
        flash(_BAD_DATE)
        return _render_note_page(row, "edit", body=body, sort_date=sort_date, status=400)
    db.update_note(note_id, body, sort_date)
    if _wants_json():
        return _saved_response(note_id)
    return redirect(url_for("notes.view_note", note_id=note_id))


@bp.route("/notes/<int:note_id>/delete", methods=["POST"])
def delete_note(note_id):
    row = db.get_note(note_id)
    if row is None:
        abort(404)
    db.soft_delete_note(note_id)
    flash(f"Note {note_id} moved to trash.")
    return redirect(url_for("notes.feed"))


@bp.route("/notes/<int:note_id>/restore", methods=["POST"])
def restore_note(note_id):
    db.restore_note(note_id)
    flash(f"Note {note_id} restored.")
    return redirect(url_for("notes.trash"))


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

@bp.route("/graph/<int:note_id>")
def graph_ego(note_id):
    """The graph around one note. There is no whole-collection graph."""
    row = db.get_note(note_id)
    if row is None:
        abort(404)
    hops = request.args.get("hops", default=1, type=int)
    hops = max(1, min(hops, 5))
    return render_template(
        "graph.html",
        center=note_id,
        hops=hops,
        title=md.first_line_text(row["body"]) or "(empty note)",
        has_connections=db.note_has_connections(note_id),
    )


@bp.route("/api/graph/<int:note_id>")
def api_graph_ego(note_id):
    if db.get_note(note_id) is None:
        abort(404)
    hops = request.args.get("hops", default=1, type=int)
    hops = max(1, min(hops, 5))
    return jsonify(db.get_graph_data(center_id=note_id, hops=hops))


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

def _attachment_disk_path(file_hash: str, extension: str) -> Path:
    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    return upload_dir / file_hash[:2] / file_hash[2:4] / f"{file_hash}{extension}"


def _wants_json() -> bool:
    """True for the edit page's in-place (fetch) requests, which get JSON
    back instead of a redirect, so the page never reloads."""
    return request.headers.get("X-Requested-With") == "fetch"


def _attachment_result(note_id: int, message: str, ok: bool = True, status: int = 200):
    """Finish an upload/remove: JSON with the refreshed attachment list for
    in-place requests, or flash + redirect for a plain form submit."""
    if _wants_json():
        attachments = db.list_note_attachments(note_id)
        html = render_template("_attachments_edit.html", note_id=note_id, attachments=attachments)
        # The View pane lists attachments too; send it fresh so flipping to
        # View afterwards shows the change even if the text wasn't edited.
        row = db.get_note(note_id)
        view_html = render_template(
            "_note_view_pane.html",
            note=_note_view_model(row),
            attachments=attachments,
            backlinks=_backlink_items(note_id),
        ) if row is not None else None
        return jsonify(ok=ok, message=message, html=html, view_html=view_html), status
    flash(message)
    return redirect(url_for("notes.edit_note_form", note_id=note_id))


@bp.route("/notes/<int:note_id>/attachments/upload", methods=["POST"])
def upload_attachment(note_id):
    row = db.get_note(note_id)
    if row is None:
        abort(404)
    file = request.files.get("file")
    if not file or file.filename == "":
        return _attachment_result(note_id, "Choose a file to upload.", ok=False, status=400)

    filename = secure_filename(file.filename)
    extension = Path(filename).suffix.lower()
    data = file.read()
    file_hash = hashlib.sha256(data).hexdigest()

    existing = db.get_attachment_by_hash(file_hash)
    if existing:
        # Same bytes already stored (possibly under another name): reuse it.
        attachment_id = existing["id"]
        display_name = existing["filename"]
        already_here = any(a["id"] == attachment_id for a in db.list_note_attachments(note_id))
        if already_here:
            return _attachment_result(note_id, f"{display_name} is already attached.")
    else:
        mime_type = file.mimetype or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        disk_path = _attachment_disk_path(file_hash, extension)
        disk_path.parent.mkdir(parents=True, exist_ok=True)
        with open(disk_path, "wb") as f:
            f.write(data)
        attachment_id = db.create_attachment(
            file_hash, filename, extension, mime_type, len(data)
        )
        display_name = filename

    db.link_attachment(note_id, attachment_id)
    return _attachment_result(note_id, f"Attached {display_name}.")


@bp.route("/notes/<int:note_id>/attachments/<int:attachment_id>/remove", methods=["POST"])
def remove_attachment(note_id, attachment_id):
    attachment = db.get_attachment(attachment_id)
    db.unlink_attachment(note_id, attachment_id)
    name = attachment["filename"] if attachment else "Attachment"
    return _attachment_result(note_id, f"Removed {name} from this note.")


# File types a browser shows without running anything in them. Everything
# else -- HTML and SVG above all, which can carry scripts -- downloads
# rather than opening as a page of this app (spec §9.3, §14).
_INLINE_TYPES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp",
    "application/pdf", "text/plain",
})


def _opens_inline(mime_type: str) -> bool:
    return mime_type in _INLINE_TYPES or mime_type.startswith(("audio/", "video/"))


@bp.route("/files/<file_hash>")
def serve_attachment(file_hash):
    row = db.get_attachment_by_hash(file_hash)
    if row is None:
        abort(404)
    disk_path = _attachment_disk_path(file_hash, row["extension"])
    if not disk_path.exists():
        abort(404)
    response = send_file(
        disk_path,
        mimetype=row["mime_type"],
        download_name=row["filename"],
        as_attachment=not _opens_inline(row["mime_type"]),
    )
    # The browser must use the stored type, not guess one from the bytes.
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Whatever opens gets no scripts and no access to the app. Not for PDFs:
    # browsers' built-in PDF viewers may refuse to open under a sandbox.
    if row["mime_type"] != "application/pdf":
        response.headers["Content-Security-Policy"] = "sandbox"
    return response
