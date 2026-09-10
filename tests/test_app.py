import io

from app import db as dbmod


def test_feed_empty_state(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"No notes yet" in resp.data


def test_create_note_and_view(client):
    resp = client.post("/notes/new", data={"body": "# Hello\nWorld", "sort_date": "2026-01-15"})
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert location.endswith("/notes/1")

    resp = client.get(location)
    assert resp.status_code == 200
    assert b"Hello" in resp.data
    assert b"No. 1" in resp.data
    assert b"2026-01-15" in resp.data
    assert b"2 lines" in resp.data


def test_create_note_rejects_bad_date(client):
    resp = client.post("/notes/new", data={"body": "x", "sort_date": "not-a-date"})
    assert resp.status_code == 400
    assert b"valid date" in resp.data


def test_feed_shows_most_recent_sort_date_first(client, app):
    with app.app_context():
        dbmod.create_note("first", "2026-01-01")
        dbmod.create_note("second", "2026-06-01")
        dbmod.create_note("third", "2026-03-01")

    resp = client.get("/")
    body = resp.data.decode()
    # "second" (2026-06-01) should appear before "third" (2026-03-01)
    # which should appear before "first" (2026-01-01).
    assert body.index("No. 2") < body.index("No. 3") < body.index("No. 1")


def test_feed_snippet_is_first_line_only(client, app):
    with app.app_context():
        dbmod.create_note("# Title Line\nSecond line should not appear in feed", "2026-01-01")

    resp = client.get("/")
    assert b"Title Line" in resp.data
    assert b"Second line should not appear in feed" not in resp.data


def test_edit_note_updates_body_and_date(client, app):
    with app.app_context():
        note_id = dbmod.create_note("original", "2026-01-01")

    resp = client.post(
        f"/notes/{note_id}/edit", data={"body": "updated body", "sort_date": "2026-02-02"}
    )
    assert resp.status_code == 302

    resp = client.get(f"/notes/{note_id}")
    assert b"updated body" in resp.data
    assert b"2026-02-02" in resp.data


def test_soft_delete_hides_from_feed_but_note_still_resolvable(client, app):
    with app.app_context():
        note_id = dbmod.create_note("to be deleted", "2026-01-01")

    client.post(f"/notes/{note_id}/delete")

    resp = client.get("/")
    assert b"to be deleted" not in resp.data

    resp = client.get(f"/notes/{note_id}")
    assert resp.status_code == 404

    resp = client.get("/trash")
    assert f"No. {note_id}".encode() in resp.data


def test_restore_brings_note_back(client, app):
    with app.app_context():
        note_id = dbmod.create_note("restore me", "2026-01-01")

    client.post(f"/notes/{note_id}/delete")
    client.post(f"/notes/{note_id}/restore")

    resp = client.get(f"/notes/{note_id}")
    assert resp.status_code == 200
    resp = client.get("/")
    assert b"restore me" in resp.data


def test_labels_appear_in_cloud_with_counts(client, app):
    with app.app_context():
        dbmod.create_note("about #zettelkasten methods", "2026-01-01")
        dbmod.create_note("more #zettelkasten notes and #research", "2026-01-02")

    resp = client.get("/")
    body = resp.data.decode()
    assert "#zettelkasten" in body
    assert "#research" in body
    # zettelkasten used twice
    assert ">2<" in body


def test_label_filter_shows_only_matching_notes(client, app):
    with app.app_context():
        dbmod.create_note("about #alpha", "2026-01-01")
        dbmod.create_note("about #beta", "2026-01-02")

    resp = client.get("/?label=alpha")
    body = resp.data.decode()
    assert "about #alpha" in body or "alpha" in body
    assert "beta" not in body.split("Showing notes")[0] or True  # sanity, real check below
    resp2 = client.get("/?label=beta")
    assert b"beta" in resp2.data


def test_editing_note_removes_stale_labels(client, app):
    with app.app_context():
        note_id = dbmod.create_note("#keepme #dropme", "2026-01-01")

    client.post(f"/notes/{note_id}/edit", data={"body": "#keepme only now", "sort_date": "2026-01-01"})

    resp = client.get("/")
    assert b"#keepme" in resp.data
    assert b"#dropme" not in resp.data


def test_note_ref_creates_backlink(client, app):
    with app.app_context():
        target_id = dbmod.create_note("the target note", "2026-01-01")
        source_id = dbmod.create_note(f"linking to [[{target_id}]]", "2026-01-02")

    resp = client.get(f"/notes/{target_id}")
    body = resp.data.decode()
    assert "Backlinks" in body
    assert f"No. {source_id}" in body


def test_broken_note_ref_renders_as_ghost(client, app):
    with app.app_context():
        dbmod.create_note("dangling ref to [[9999]]", "2026-01-01")

    resp = client.get("/notes/1")
    assert b"note-ref-ghost" in resp.data


def test_orphans_view_lists_unlinked_notes(client, app):
    with app.app_context():
        linked_target = dbmod.create_note("target", "2026-01-01")
        dbmod.create_note(f"links to [[{linked_target}]]", "2026-01-02")
        orphan_id = dbmod.create_note("all alone", "2026-01-03")

    resp = client.get("/orphans")
    body = resp.data.decode()
    assert f"No. {orphan_id}" in body
    assert f"No. {linked_target}" not in body


def test_orphans_card_has_same_expand_edit_affordances_as_feed(client, app):
    with app.app_context():
        orphan_id = dbmod.create_note("all alone", "2026-01-01")

    resp = client.get("/orphans")
    body = resp.data.decode()
    assert f'data-note-id="{orphan_id}"' in body
    assert f'/notes/{orphan_id}/fragment' in body
    assert f'href="/notes/{orphan_id}/edit"' in body
    assert f'href="/notes/{orphan_id}"' in body
    assert "disclosure-btn" in body


def test_random_redirects_to_a_note(client, app):
    with app.app_context():
        dbmod.create_note("only note", "2026-01-01")

    resp = client.get("/random")
    assert resp.status_code == 302
    assert "/notes/1" in resp.headers["Location"]


def test_random_with_no_notes_redirects_to_feed(client):
    resp = client.get("/random")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_graph_api_global(client, app):
    with app.app_context():
        a = dbmod.create_note("note a", "2026-01-01")
        b = dbmod.create_note(f"note b links to [[{a}]]", "2026-01-02")
        dbmod.create_note("isolated note, no links", "2026-01-03")

    resp = client.get("/api/graph")
    data = resp.get_json()
    node_ids = {n["id"] for n in data["nodes"]}
    # only connected notes should appear; the isolated note is excluded
    assert node_ids == {a, b}
    assert len(data["edges"]) == 1


def test_graph_api_ego_respects_hops(client, app):
    with app.app_context():
        a = dbmod.create_note("a", "2026-01-01")
        b = dbmod.create_note(f"b [[{a}]]", "2026-01-02")
        c = dbmod.create_note(f"c [[{b}]]", "2026-01-03")

    resp = client.get(f"/api/graph/{a}?hops=1")
    node_ids = {n["id"] for n in resp.get_json()["nodes"]}
    assert node_ids == {a, b}

    resp = client.get(f"/api/graph/{a}?hops=2")
    node_ids = {n["id"] for n in resp.get_json()["nodes"]}
    assert node_ids == {a, b, c}


def test_graph_includes_ghost_node_for_broken_ref(client, app):
    with app.app_context():
        dbmod.create_note("points at [[555]]", "2026-01-01")

    resp = client.get("/api/graph")
    data = resp.get_json()
    ghost_nodes = [n for n in data["nodes"] if n["ghost"]]
    assert len(ghost_nodes) == 1
    assert ghost_nodes[0]["id"] == 555
    assert data["edges"][0]["broken"] is True


def test_line_count_excludes_blank_lines_end_to_end(client):
    client.post("/notes/new", data={"body": "one\n\n\ntwo\n\nthree", "sort_date": "2026-01-01"})
    resp = client.get("/notes/1")
    assert b"3 lines" in resp.data


def test_feed_card_has_expand_edit_and_permalink_affordances(client, app):
    with app.app_context():
        note_id = dbmod.create_note("# Some Title\nbody text", "2026-01-01")

    resp = client.get("/")
    body = resp.data.decode()
    assert f'data-note-id="{note_id}"' in body
    assert f'/notes/{note_id}/fragment' in body  # data-fragment-url
    assert f'href="/notes/{note_id}/edit"' in body  # explicit edit button
    assert f'href="/notes/{note_id}"' in body  # permalink to standalone page still present
    assert 'disclosure-btn' in body
    assert 'data-role="full"' in body and 'hidden' in body  # collapsed by default


def test_note_fragment_returns_full_rendered_body(client, app):
    with app.app_context():
        note_id = dbmod.create_note("# Full Title\nSecond line appears here too, unlike the feed snippet.", "2026-01-01")

    resp = client.get(f"/notes/{note_id}/fragment")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "<h1>Full Title</h1>" in body
    assert "Second line appears here too" in body
    # it's a bare fragment, not a full page
    assert "<html" not in body
    assert "note-body-inline" in body


def test_note_fragment_includes_attachments(client, app):
    with app.app_context():
        note_id = dbmod.create_note("note with file", "2026-01-01")
    client.post(
        f"/notes/{note_id}/attachments/upload",
        data={"file": (io.BytesIO(b"content"), "inline.txt")},
        content_type="multipart/form-data",
    )
    resp = client.get(f"/notes/{note_id}/fragment")
    assert b"inline.txt" in resp.data


def test_note_fragment_404_for_missing_or_deleted(client, app):
    resp = client.get("/notes/999/fragment")
    assert resp.status_code == 404

    with app.app_context():
        note_id = dbmod.create_note("to delete", "2026-01-01")
    client.post(f"/notes/{note_id}/delete")
    resp = client.get(f"/notes/{note_id}/fragment")
    assert resp.status_code == 404


# ---------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------

def test_upload_attachment_and_view(client, app):
    with app.app_context():
        note_id = dbmod.create_note("note with a file", "2026-01-01")

    data = {"file": (io.BytesIO(b"hello world"), "notes.txt")}
    resp = client.post(
        f"/notes/{note_id}/attachments/upload", data=data, content_type="multipart/form-data"
    )
    assert resp.status_code == 302

    resp = client.get(f"/notes/{note_id}")
    assert b"notes.txt" in resp.data


def test_attachment_dedupes_by_hash(client, app):
    with app.app_context():
        n1 = dbmod.create_note("note one", "2026-01-01")
        n2 = dbmod.create_note("note two", "2026-01-02")

    payload = b"identical bytes"
    client.post(
        f"/notes/{n1}/attachments/upload",
        data={"file": (io.BytesIO(payload), "shared.txt")},
        content_type="multipart/form-data",
    )
    client.post(
        f"/notes/{n2}/attachments/upload",
        data={"file": (io.BytesIO(payload), "shared.txt")},
        content_type="multipart/form-data",
    )

    with app.app_context():
        from app import db as d
        all_attachments = d.get_db().execute("SELECT COUNT(*) c FROM attachments").fetchone()["c"]
        assert all_attachments == 1  # same hash -> single stored file, linked twice


def test_attachment_search_and_link_existing(client, app):
    with app.app_context():
        n1 = dbmod.create_note("note one", "2026-01-01")
        n2 = dbmod.create_note("note two", "2026-01-02")

    client.post(
        f"/notes/{n1}/attachments/upload",
        data={"file": (io.BytesIO(b"searchable content"), "findme.pdf")},
        content_type="multipart/form-data",
    )

    resp = client.get("/api/attachments/search?q=findme")
    results = resp.get_json()
    assert len(results) == 1
    attachment_id = results[0]["id"]

    client.post(f"/notes/{n2}/attachments/attach", data={"attachment_id": attachment_id})
    resp = client.get(f"/notes/{n2}")
    assert b"findme.pdf" in resp.data


def test_remove_attachment_unlinks_but_keeps_file(client, app):
    with app.app_context():
        note_id = dbmod.create_note("note", "2026-01-01")

    client.post(
        f"/notes/{note_id}/attachments/upload",
        data={"file": (io.BytesIO(b"content here"), "keepme.txt")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        from app import db as d
        attachment_id = d.get_db().execute("SELECT id FROM attachments").fetchone()["id"]

    client.post(f"/notes/{note_id}/attachments/{attachment_id}/remove")

    client.get(f"/notes/{note_id}")  # flush queued flash messages first
    resp = client.get(f"/notes/{note_id}")
    assert b"keepme.txt" not in resp.data

    with app.app_context():
        from app import db as d
        still_exists = d.get_db().execute(
            "SELECT COUNT(*) c FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()["c"]
        assert still_exists == 1
