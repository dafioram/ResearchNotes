import hashlib
import mimetypes
import os
from datetime import datetime
from pathlib import Path

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

from . import db
from . import markdown as md

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
    existing_ids = db.get_existing_note_ids()
    return {
        "id": row["id"],
        "body": row["body"],
        "sort_date": row["sort_date"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "line_count": md.line_count(row["body"]),
        "html": md.render(row["body"], existing_ids),
    }


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------

@bp.route("/")
def feed():
    label = request.args.get("label") or None
    if label:
        label = label.lower()
    notes = db.list_notes(label=label)
    existing_ids = db.get_existing_note_ids()
    items = [
        {
            "id": n["id"],
            "sort_date": n["sort_date"],
            "line_count": md.line_count(n["body"]),
            "snippet_html": md.render_first_line(n["body"], existing_ids),
        }
        for n in notes
    ]
    labels = db.get_labels_with_counts()
    return render_template("feed.html", notes=items, labels=labels, active_label=label)


@bp.route("/random")
def random_note():
    note_id = db.get_random_note_id()
    if note_id is None:
        flash("No notes yet -- create one first.")
        return redirect(url_for("notes.feed"))
    return redirect(url_for("notes.view_note", note_id=note_id))


@bp.route("/orphans")
def orphans():
    notes = db.get_orphan_notes()
    existing_ids = db.get_existing_note_ids()
    items = [
        {
            "id": n["id"],
            "sort_date": n["sort_date"],
            "line_count": md.line_count(n["body"]),
            "snippet_html": md.render_first_line(n["body"], existing_ids),
        }
        for n in notes
    ]
    return render_template("orphans.html", notes=items)


@bp.route("/trash")
def trash():
    notes = db.list_deleted_notes()
    return render_template("trash.html", notes=notes)


# ---------------------------------------------------------------------------
# Note CRUD
# ---------------------------------------------------------------------------

@bp.route("/notes/new", methods=["GET"])
def new_note_form():
    labels = db.get_labels_with_counts()
    return render_template(
        "note_edit.html",
        note=None,
        labels=labels,
        default_date=db.today_str(),
    )


@bp.route("/notes/new", methods=["POST"])
def create_note():
    body = request.form.get("body", "")
    sort_date = request.form.get("sort_date", "").strip()
    if not _valid_date(sort_date):
        flash("Sort date must be a valid date (YYYY-MM-DD).")
        labels = db.get_labels_with_counts()
        return render_template(
            "note_edit.html",
            note={"id": None, "body": body, "sort_date": sort_date},
            labels=labels,
            default_date=db.today_str(),
        ), 400
    note_id = db.create_note(body, sort_date)
    return redirect(url_for("notes.view_note", note_id=note_id))


@bp.route("/notes/<int:note_id>")
def view_note(note_id):
    row = db.get_note(note_id)
    if row is None:
        abort(404)
    note = _note_view_model(row)
    backlinks = db.get_backlinks(note_id)
    existing_ids = db.get_existing_note_ids()
    backlink_items = [
        {
            "id": b["id"],
            "sort_date": b["sort_date"],
            "snippet_html": md.render_first_line(b["body"], existing_ids),
        }
        for b in backlinks
    ]
    attachments = db.list_note_attachments(note_id)
    return render_template(
        "note_view.html", note=note, backlinks=backlink_items, attachments=attachments
    )


@bp.route("/notes/<int:note_id>/fragment")
def note_fragment(note_id):
    """Full rendered body + attachments for a note, as a bare HTML fragment
    (no page chrome) -- used to expand a note inline on the feed."""
    row = db.get_note(note_id)
    if row is None:
        abort(404)
    note = _note_view_model(row)
    attachments = db.list_note_attachments(note_id)
    return render_template("note_fragment.html", note=note, attachments=attachments)


@bp.route("/notes/<int:note_id>/edit", methods=["GET"])
def edit_note_form(note_id):
    row = db.get_note(note_id)
    if row is None:
        abort(404)
    labels = db.get_labels_with_counts()
    attachments = db.list_note_attachments(note_id)
    return render_template(
        "note_edit.html",
        note=row,
        labels=labels,
        default_date=row["sort_date"],
        attachments=attachments,
    )


@bp.route("/notes/<int:note_id>/edit", methods=["POST"])
def update_note(note_id):
    row = db.get_note(note_id)
    if row is None:
        abort(404)
    body = request.form.get("body", "")
    sort_date = request.form.get("sort_date", "").strip()
    if not _valid_date(sort_date):
        flash("Sort date must be a valid date (YYYY-MM-DD).")
        labels = db.get_labels_with_counts()
        attachments = db.list_note_attachments(note_id)
        return render_template(
            "note_edit.html",
            note={"id": note_id, "body": body, "sort_date": sort_date},
            labels=labels,
            default_date=row["sort_date"],
            attachments=attachments,
        ), 400
    db.update_note(note_id, body, sort_date)
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

@bp.route("/graph")
def graph_global():
    return render_template("graph.html", center=None)


@bp.route("/graph/<int:note_id>")
def graph_ego(note_id):
    row = db.get_note(note_id)
    if row is None:
        abort(404)
    hops = request.args.get("hops", default=1, type=int)
    hops = max(1, min(hops, 5))
    return render_template("graph.html", center=note_id, hops=hops)


@bp.route("/api/graph")
def api_graph_global():
    return jsonify(db.get_graph_data())


@bp.route("/api/graph/<int:note_id>")
def api_graph_ego(note_id):
    hops = request.args.get("hops", default=1, type=int)
    hops = max(1, min(hops, 5))
    return jsonify(db.get_graph_data(center_id=note_id, hops=hops))


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

def _attachment_disk_path(file_hash: str, extension: str) -> Path:
    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    return upload_dir / file_hash[:2] / file_hash[2:4] / f"{file_hash}{extension}"


@bp.route("/notes/<int:note_id>/attachments/upload", methods=["POST"])
def upload_attachment(note_id):
    row = db.get_note(note_id)
    if row is None:
        abort(404)
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Choose a file to upload.")
        return redirect(url_for("notes.edit_note_form", note_id=note_id))

    filename = secure_filename(file.filename)
    extension = Path(filename).suffix.lower()
    data = file.read()
    file_hash = hashlib.sha256(data).hexdigest()

    existing = db.get_attachment_by_hash(file_hash)
    if existing:
        attachment_id = existing["id"]
    else:
        mime_type = file.mimetype or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        disk_path = _attachment_disk_path(file_hash, extension)
        disk_path.parent.mkdir(parents=True, exist_ok=True)
        with open(disk_path, "wb") as f:
            f.write(data)
        attachment_id = db.create_attachment(
            file_hash, filename, extension, mime_type, len(data)
        )

    db.link_attachment(note_id, attachment_id)
    flash(f"Attached {filename}.")
    return redirect(url_for("notes.edit_note_form", note_id=note_id))


@bp.route("/notes/<int:note_id>/attachments/attach", methods=["POST"])
def attach_existing(note_id):
    row = db.get_note(note_id)
    if row is None:
        abort(404)
    attachment_id = request.form.get("attachment_id", type=int)
    if attachment_id:
        db.link_attachment(note_id, attachment_id)
        flash("Attachment linked.")
    return redirect(url_for("notes.edit_note_form", note_id=note_id))


@bp.route("/notes/<int:note_id>/attachments/<int:attachment_id>/remove", methods=["POST"])
def remove_attachment(note_id, attachment_id):
    db.unlink_attachment(note_id, attachment_id)
    flash("Attachment removed from this note.")
    return redirect(url_for("notes.edit_note_form", note_id=note_id))


@bp.route("/api/attachments/search")
def api_attachment_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    rows = db.search_attachments(query)
    return jsonify(
        [
            {
                "id": r["id"],
                "filename": r["filename"],
                "size": r["size"],
                "mime_type": r["mime_type"],
            }
            for r in rows
        ]
    )


@bp.route("/files/<file_hash>")
def serve_attachment(file_hash):
    row = db.get_attachment_by_hash(file_hash)
    if row is None:
        abort(404)
    disk_path = _attachment_disk_path(file_hash, row["extension"])
    if not disk_path.exists():
        abort(404)
    return send_file(
        disk_path,
        mimetype=row["mime_type"],
        download_name=row["filename"],
        as_attachment=False,
    )
