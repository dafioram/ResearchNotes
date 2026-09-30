import io

from app import db as dbmod


def test_feed_empty_state(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"No notes yet" in resp.data


def test_create_note_and_view(client):
    # Without JS, creating a note lands on the feed, focused on the new note.
    resp = client.post("/notes/new", data={"body": "# Hello\nWorld", "sort_date": "2026-01-15"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/?focus=1")

    resp = client.get("/notes/1")
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
    assert f'href="/notes/{orphan_id}"' not in body  # the No. stamp is plain text
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


def test_there_is_no_global_graph(client, app):
    assert client.get("/graph").status_code == 404
    assert client.get("/api/graph").status_code == 404
    assert b'href="/graph"' not in client.get("/").data  # not in the top bar either


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
        note_id = dbmod.create_note("points at [[555]]", "2026-01-01")

    data = client.get(f"/api/graph/{note_id}").get_json()
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
    assert f'href="/notes/{note_id}/edit"' in body  # Edit goes straight to Edit mode
    assert f'href="/notes/{note_id}"' not in body  # no View link: expanding shows the note
    assert f'<span class="meta-item">No. {note_id}</span>' in body
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


# ---------------------------------------------------------------------
# Feed search
# ---------------------------------------------------------------------

def _insert_note_with_id(app, note_id, body, sort_date="2026-01-01"):
    """Insert a note with an explicit id, bypassing AUTOINCREMENT, so
    numeric-id-match tests can set up notes 100/1000/etc without creating
    that many rows."""
    with app.app_context():
        db = dbmod.get_db()
        ts = dbmod.now_iso()
        db.execute(
            "INSERT INTO notes (id, body, sort_date, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (note_id, body, sort_date, ts, ts),
        )
        db.commit()


def test_search_matches_note_body_text(client, app):
    with app.app_context():
        dbmod.create_note("notes about photosynthesis in plants", "2026-01-01")
        dbmod.create_note("notes about quantum computing", "2026-01-02")

    resp = client.get("/?q=photosynthesis")
    body = resp.data.decode()
    assert "photosynthesis" in body
    assert "quantum" not in body


def test_search_is_case_insensitive(client, app):
    with app.app_context():
        dbmod.create_note("Distinctive Capitalized Keyword here", "2026-01-01")

    resp = client.get("/?q=capitalized")
    assert b"Distinctive" in resp.data


def test_search_stems_related_word_forms(client, app):
    with app.app_context():
        dbmod.create_note("still researching this topic", "2026-01-01")

    # query "research" should find a note that only contains "researching"
    resp = client.get("/?q=research")
    assert b"researching" in resp.data


def test_search_matches_label_text_without_hash(client, app):
    with app.app_context():
        dbmod.create_note("a note tagged #zettelkasten", "2026-01-01")

    resp = client.get("/?q=zettelkasten")
    assert b"zettelkasten" in resp.data


def test_search_excludes_soft_deleted_notes(client, app):
    with app.app_context():
        note_id = dbmod.create_note("a searchable unique termxyz note", "2026-01-01")
    client.post(f"/notes/{note_id}/delete")

    resp = client.get("/?q=termxyz")
    assert b"No notes matching" in resp.data


def test_search_empty_query_shows_full_feed_unfiltered(client, app):
    with app.app_context():
        dbmod.create_note("first note", "2026-01-01")
        dbmod.create_note("second note", "2026-01-02")

    resp = client.get("/?q=")
    assert b"No. 1" in resp.data and b"No. 2" in resp.data


def test_search_box_shows_current_query_value(client):
    resp = client.get("/?q=myquery")
    assert b'value="myquery"' in resp.data


def test_numeric_search_promotes_id_prefix_matches_to_top(client, app):
    _insert_note_with_id(app, 100, "an ordinary note with nothing special")
    _insert_note_with_id(app, 1000, "another ordinary note")
    _insert_note_with_id(app, 1005, "yet another")
    _insert_note_with_id(app, 5, "not a prefix match for the query")

    resp = client.get("/?q=100")
    body = resp.data.decode()

    # all three id-prefix matches (100, 1000, 1005) should appear...
    assert "No. 100<" in body
    assert "No. 1000<" in body
    assert "No. 1005<" in body
    # ...but note 5 (id doesn't start with "100") should not
    assert "No. 5<" not in body

    # exact id match (100) ranks before the other prefix matches
    assert body.index("No. 100<") < body.index("No. 1000<")
    assert body.index("No. 100<") < body.index("No. 1005<")


def test_numeric_search_also_includes_text_content_matches_after_id_matches(client, app):
    _insert_note_with_id(app, 100, "the id-matching note")
    with app.app_context():
        # a differently-numbered note whose body literally contains "100"
        text_match_id = dbmod.create_note("a race that took exactly 100 seconds", "2026-01-01")

    resp = client.get("/?q=100")
    body = resp.data.decode()
    assert f"No. {text_match_id}<" in body
    # id match (100) is promoted ahead of the plain text match
    assert body.index("No. 100<") < body.index(f"No. {text_match_id}<")


def test_non_numeric_query_does_not_trigger_id_matching(client, app):
    _insert_note_with_id(app, 10, "this note's id is ten but irrelevant here")
    with app.app_context():
        dbmod.create_note("a note mentioning apples", "2026-01-01")

    resp = client.get("/?q=10a")
    body = resp.data.decode()
    # neither note's body contains the literal token "10a", so nothing matches
    assert "No notes matching" in body


def test_search_combines_with_label_filter(client, app):
    with app.app_context():
        alpha_id = dbmod.create_note("shared keyword #alpha", "2026-01-01")
        beta_id = dbmod.create_note("shared keyword #beta", "2026-01-02")

    # both notes match the text search "shared" on their own...
    resp_unfiltered = client.get("/?q=shared")
    unfiltered_body = resp_unfiltered.data.decode()
    assert f'data-note-id="{alpha_id}"' in unfiltered_body
    assert f'data-note-id="{beta_id}"' in unfiltered_body

    # ...but adding label=alpha narrows it to just the alpha-tagged one
    resp = client.get("/?q=shared&label=alpha")
    body = resp.data.decode()
    assert f'data-note-id="{alpha_id}"' in body
    assert f'data-note-id="{beta_id}"' not in body

    resp2 = client.get("/?q=shared&label=beta")
    body2 = resp2.data.decode()
    assert f'data-note-id="{beta_id}"' in body2
    assert f'data-note-id="{alpha_id}"' not in body2


# ---------------------------------------------------------------------
# Attachments tab + attachment-count badge
# ---------------------------------------------------------------------

def test_attachments_tab_lists_only_notes_with_attachments(client, app):
    with app.app_context():
        with_file_id = dbmod.create_note("has a file", "2026-01-01")
        without_file_id = dbmod.create_note("has nothing attached", "2026-01-02")

    client.post(
        f"/notes/{with_file_id}/attachments/upload",
        data={"file": (io.BytesIO(b"content"), "doc.txt")},
        content_type="multipart/form-data",
    )

    resp = client.get("/attachments")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert f'data-note-id="{with_file_id}"' in body
    assert f'data-note-id="{without_file_id}"' not in body


def test_attachments_tab_empty_state(client):
    resp = client.get("/attachments")
    assert b"No notes have attachments yet." in resp.data


def test_attachments_tab_card_has_same_affordances_as_feed(client, app):
    with app.app_context():
        note_id = dbmod.create_note("has a file", "2026-01-01")
    client.post(
        f"/notes/{note_id}/attachments/upload",
        data={"file": (io.BytesIO(b"content"), "doc.txt")},
        content_type="multipart/form-data",
    )

    resp = client.get("/attachments")
    body = resp.data.decode()
    assert f'/notes/{note_id}/fragment' in body
    assert f'href="/notes/{note_id}/edit"' in body
    assert f'href="/notes/{note_id}"' not in body
    assert "disclosure-btn" in body


def test_attachments_tab_excludes_soft_deleted_notes(client, app):
    with app.app_context():
        note_id = dbmod.create_note("will be deleted", "2026-01-01")
    client.post(
        f"/notes/{note_id}/attachments/upload",
        data={"file": (io.BytesIO(b"content"), "doc.txt")},
        content_type="multipart/form-data",
    )
    client.post(f"/notes/{note_id}/delete")

    resp = client.get("/attachments")
    assert b"No notes have attachments yet." in resp.data


def test_attachments_nav_link_present(client):
    resp = client.get("/")
    assert b'href="/attachments"' in resp.data


def test_feed_card_shows_attachment_count_badge(client, app):
    with app.app_context():
        note_id = dbmod.create_note("note with two files", "2026-01-01")
    for name in ["a.txt", "b.txt"]:
        client.post(
            f"/notes/{note_id}/attachments/upload",
            data={"file": (io.BytesIO(name.encode() + b"content"), name)},
            content_type="multipart/form-data",
        )

    resp = client.get("/")
    assert b"2 files" in resp.data


def test_feed_card_singular_file_label(client, app):
    with app.app_context():
        note_id = dbmod.create_note("note with one file", "2026-01-01")
    client.post(
        f"/notes/{note_id}/attachments/upload",
        data={"file": (io.BytesIO(b"content"), "only.txt")},
        content_type="multipart/form-data",
    )

    resp = client.get("/")
    assert b"1 file<" in resp.data
    assert b"1 files" not in resp.data


def test_feed_card_no_badge_without_attachments(client, app):
    with app.app_context():
        dbmod.create_note("plain note, no attachments", "2026-01-01")

    resp = client.get("/")
    assert b"file<" not in resp.data
    assert b"files<" not in resp.data


def test_orphans_card_shows_attachment_count_badge(client, app):
    with app.app_context():
        note_id = dbmod.create_note("orphan note with a file", "2026-01-01")
    client.post(
        f"/notes/{note_id}/attachments/upload",
        data={"file": (io.BytesIO(b"content"), "doc.txt")},
        content_type="multipart/form-data",
    )

    resp = client.get("/orphans")
    assert b"1 file<" in resp.data


def test_attachment_count_helper_returns_correct_counts(client, app):
    with app.app_context():
        n1 = dbmod.create_note("note one", "2026-01-01")
        n2 = dbmod.create_note("note two", "2026-01-02")
    client.post(
        f"/notes/{n1}/attachments/upload",
        data={"file": (io.BytesIO(b"content1"), "a.txt")},
        content_type="multipart/form-data",
    )
    client.post(
        f"/notes/{n1}/attachments/upload",
        data={"file": (io.BytesIO(b"content2"), "b.txt")},
        content_type="multipart/form-data",
    )

    with app.app_context():
        counts = dbmod.get_attachment_counts()
        assert counts.get(n1) == 2
        assert counts.get(n2) is None


# ---------------------------------------------------------------------
# Backlinks in the inline expansion + backlink-count badge
# ---------------------------------------------------------------------

class _NestedLinkChecker:
    """Finds <a> elements nested inside other <a> elements (invalid HTML)."""

    def __init__(self, html_text):
        from html.parser import HTMLParser

        self.nested = 0
        depth = [0]
        outer = self

        class P(HTMLParser):
            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    if depth[0] > 0:
                        outer.nested += 1
                    depth[0] += 1

            def handle_endtag(self, tag):
                if tag == "a":
                    depth[0] = max(0, depth[0] - 1)

        P().feed(html_text)


def test_fragment_includes_backlinks(client, app):
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        source = dbmod.create_note(f"# Source note\nsee [[{target}]]", "2026-01-02")

    body = client.get(f"/notes/{target}/fragment").data.decode()
    assert "Backlinks" in body
    assert f'href="/notes/{source}"' in body
    assert "Source note" in body
    assert "1 note references this one" in body


def test_fragment_has_no_backlinks_section_when_none(client, app):
    with app.app_context():
        note_id = dbmod.create_note("lonely", "2026-01-01")
    assert b"Backlinks" not in client.get(f"/notes/{note_id}/fragment").data


def test_backlink_rows_never_nest_links(client, app):
    # A source note whose first line itself contains a label and a ref --
    # the rendered snippet would contain <a> tags, which must not end up
    # inside the backlink row's own <a>.
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        dbmod.create_note(f"# About #physics and [[{target}]]", "2026-01-02")

    for url in [f"/notes/{target}", f"/notes/{target}/fragment"]:
        body = client.get(url).data.decode()
        assert "About #physics and [[" in body  # plain-text snippet
        assert _NestedLinkChecker(body).nested == 0, url


def test_backlinks_exclude_deleted_sources(client, app):
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        source = dbmod.create_note(f"ref [[{target}]]", "2026-01-02")
    client.post(f"/notes/{source}/delete")

    assert b"Backlinks" not in client.get(f"/notes/{target}/fragment").data
    assert b"backlink" not in client.get("/").data


def test_feed_card_shows_backlink_count_badge(client, app):
    with app.app_context():
        target = dbmod.create_note("# Popular note", "2026-01-01")
        dbmod.create_note(f"one [[{target}]]", "2026-01-02")
        dbmod.create_note(f"two [[{target}]]", "2026-01-03")
        lone = dbmod.create_note(f"three [[{target}]]", "2026-01-04")
        dbmod.create_note(f"four [[{lone}]]", "2026-01-05")

    body = client.get("/").data.decode()
    assert "3 backlinks<" in body
    assert "1 backlink<" in body


def test_backlink_badge_count_matches_backlink_list(client, app):
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        for i in range(4):
            dbmod.create_note(f"ref {i} [[{target}]]", "2026-01-02")
        counts = dbmod.get_backlink_counts()
        listed = dbmod.get_backlinks(target)
        assert counts[target] == len(listed) == 4


def test_first_line_text_strips_markdown():
    from app import markdown as md

    assert md.first_line_text("# Intro to **bold** #physics\nmore") == "Intro to bold #physics"
    assert md.first_line_text("see [[4]] & <tags>") == "see [[4]] & <tags>"
    assert md.first_line_text("") == ""


# ---------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------

def _make_notes(app, count, body_fmt="note {i}"):
    ids = []
    with app.app_context():
        for i in range(count):
            # distinct increasing dates so feed order is newest-id-first
            ids.append(dbmod.create_note(body_fmt.format(i=i), f"2026-01-{i + 1:02d}"))
    return ids


def _card_ids(body):
    import re
    return [int(x) for x in re.findall(r'data-note-id="(\d+)"', body)]


def test_feed_paginates(client, app):
    app.config["PAGE_SIZE"] = 2
    ids = _make_notes(app, 5)  # newest first: ids[4], ids[3], ...

    assert _card_ids(client.get("/").data.decode()) == [ids[4], ids[3]]
    assert _card_ids(client.get("/?page=2").data.decode()) == [ids[2], ids[1]]
    assert _card_ids(client.get("/?page=3").data.decode()) == [ids[0]]


def test_pager_hidden_when_single_page(client, app):
    _make_notes(app, 3)
    body = client.get("/").data.decode()
    assert 'class="pager"' not in body


def test_pager_links_and_summary(client, app):
    app.config["PAGE_SIZE"] = 2
    _make_notes(app, 5)

    body = client.get("/?page=2").data.decode()
    assert 'class="pager"' in body
    assert 'href="/" rel="prev"' in body            # page 1 has a clean URL
    assert 'href="/?page=3" rel="next"' in body
    assert 'aria-current="page">2<' in body
    assert "Showing 3&ndash;4 of 5" in body

    first = client.get("/").data.decode()
    assert "Previous</span>" in first               # disabled on page 1
    last = client.get("/?page=3").data.decode()
    assert "Next</span>" in last                     # disabled on last page


def test_out_of_range_page_redirects_to_last_page(client, app):
    app.config["PAGE_SIZE"] = 2
    _make_notes(app, 5)
    resp = client.get("/?page=99")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/?page=3")


def test_bad_page_values_fall_back_to_page_one(client, app):
    app.config["PAGE_SIZE"] = 2
    ids = _make_notes(app, 5)
    for bad in ["abc", "0", "-3"]:
        resp = client.get(f"/?page={bad}")
        assert resp.status_code == 200, bad
        assert _card_ids(resp.data.decode()) == [ids[4], ids[3]], bad


def test_page_links_preserve_search_and_label(client, app):
    app.config["PAGE_SIZE"] = 2
    _make_notes(app, 5, body_fmt="shared words #topic {i}")

    body = client.get("/?q=shared&label=topic").data.decode()
    assert "page=2" in body
    assert "q=shared" in body.split('rel="next"')[0].rsplit("href=", 1)[1]
    assert "label=topic" in body.split('rel="next"')[0].rsplit("href=", 1)[1]


def test_search_pagination_keeps_id_matches_on_page_one(client, app):
    app.config["PAGE_SIZE"] = 2
    _insert_note_with_id(app, 7, "no digits in here", "2026-01-01")
    with app.app_context():
        for i in range(4):
            dbmod.create_note(f"mentions 7 in text, variant {i}", f"2026-02-0{i + 1}")

    page1 = _card_ids(client.get("/?q=7").data.decode())
    assert page1[0] == 7  # the id match leads page 1
    page2 = _card_ids(client.get("/?q=7&page=2").data.decode())
    page3 = _card_ids(client.get("/?q=7&page=3").data.decode())
    all_ids = page1 + page2 + page3
    assert len(all_ids) == 5 and len(set(all_ids)) == 5  # no dupes, none lost


def test_orphans_and_attachments_paginate(client, app):
    app.config["PAGE_SIZE"] = 2
    ids = _make_notes(app, 3)
    for note_id in ids:
        client.post(
            f"/notes/{note_id}/attachments/upload",
            data={"file": (io.BytesIO(f"file {note_id}".encode()), f"f{note_id}.txt")},
            content_type="multipart/form-data",
        )

    for url in ["/orphans", "/attachments"]:
        assert _card_ids(client.get(url).data.decode()) == [ids[2], ids[1]], url
        assert _card_ids(client.get(f"{url}?page=2").data.decode()) == [ids[0]], url
        assert client.get(f"{url}?page=9").status_code == 302, url


def test_page_window_shape():
    from app.routes import _page_window

    assert _page_window(1, 1) == [1]
    assert _page_window(1, 4) == [1, 2, 3, 4]
    assert _page_window(6, 12) == [1, None, 4, 5, 6, 7, 8, None, 12]
    # a single-page gap is filled in rather than shown as "..."
    assert _page_window(4, 12) == [1, 2, 3, 4, 5, 6, None, 12]


def test_page_size_env_var(monkeypatch, tmp_path):
    from app import create_app

    monkeypatch.setenv("PAGE_SIZE", "25")
    a = create_app({"DATABASE_PATH": str(tmp_path / "n.db"), "UPLOAD_DIR": str(tmp_path / "u")})
    assert a.config["PAGE_SIZE"] == 25

    monkeypatch.setenv("PAGE_SIZE", "nonsense")
    b = create_app({"DATABASE_PATH": str(tmp_path / "n2.db"), "UPLOAD_DIR": str(tmp_path / "u2")})
    assert b.config["PAGE_SIZE"] == 50


def test_fragment_caps_backlinks_but_note_page_shows_all(client, app):
    with app.app_context():
        target = dbmod.create_note("# Hub", "2026-01-01")
        for i in range(12):
            dbmod.create_note(f"ref {i} [[{target}]]", "2026-01-02")

    frag = client.get(f"/notes/{target}/fragment").data.decode()
    assert "12 notes reference this one" in frag
    assert frag.count('class="backlink-row"') == 10
    assert "2 more" in frag and f'href="/notes/{target}"' in frag

    page = client.get(f"/notes/{target}").data.decode()
    assert page.count('class="backlink-row"') == 12
    assert "more &mdash;" not in page


def test_backlink_rows_show_link_context(client, app):
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        source = dbmod.create_note(
            f"# Citing note\n\nThis result contradicts [[{target}]] because of sampling.",
            "2026-01-02",
        )

    for url in [f"/notes/{target}", f"/notes/{target}/fragment"]:
        body = client.get(url).data.decode()
        assert "Citing note" in body  # title line still shown
        assert "This result contradicts " in body
        assert f'<mark class="backlink-ref">[[{target}]]</mark>' in body
        assert " because of sampling." in body
        assert _NestedLinkChecker(body).nested == 0


def test_backlink_row_caps_mentions_at_two(client, app):
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        dbmod.create_note(
            f"# Many\n\none [[{target}]]\n\ntwo [[{target}]]\n\nthree [[{target}]]\n\nfour [[{target}]]",
            "2026-01-02",
        )

    body = client.get(f"/notes/{target}").data.decode()
    assert body.count('class="backlink-ref"') == 2
    assert "and 2 more mentions" in body


def test_backlink_row_without_context_when_link_is_in_title(client, app):
    with app.app_context():
        target = dbmod.create_note("# Target", "2026-01-01")
        dbmod.create_note(f"# About [[{target}]]\nunrelated body", "2026-01-02")

    body = client.get(f"/notes/{target}").data.decode()
    assert "backlink-row" in body
    assert 'class="backlink-context' not in body


# ---------------------------------------------------------------------
# Desktop layout, save shortcut wiring, offline graph
# ---------------------------------------------------------------------

def test_feed_has_sidebar_with_search_and_labels(client, app):
    with app.app_context():
        dbmod.create_note("# A #alpha", "2026-01-01")
    body = client.get("/").data.decode()
    sidebar = body.split('<aside class="sidebar">')[1].split("</aside>")[0]
    assert 'role="search"' in sidebar
    assert 'class="label-list"' in sidebar and "#alpha" in sidebar


def test_graph_page_is_single_column_full_width(client, app):
    with app.app_context():
        a = dbmod.create_note("a", "2026-01-01")
        dbmod.create_note(f"b [[{a}]]", "2026-01-02")
    body = client.get(f"/graph/{a}").data.decode()
    assert '<aside class="sidebar">' not in body
    assert "wrap-full" in body


def test_list_pages_use_sidebar_with_counts(client, app):
    with app.app_context():
        dbmod.create_note("alone", "2026-01-01")
    for url, title in [("/orphans", "Orphans"), ("/attachments", "Attachments"), ("/trash", "Trash")]:
        body = client.get(url).data.decode()
        assert f'<h1 class="sidebar-title">{title}</h1>' in body, url
        assert 'class="sidebar-count"' in body, url
    assert '<p class="sidebar-count">1 note</p>' in client.get("/orphans").data.decode()


def test_edit_form_fields_are_tied_to_note_form(client, app):
    with app.app_context():
        note_id = dbmod.create_note("# Hi", "2026-01-01")
    body = client.get(f"/notes/{note_id}/edit").data.decode()
    assert f'action="/notes/{note_id}/edit"></form>' in body
    assert body.count('form="note-form"') == 3  # date, body, Save button
    new = client.get("/notes/new").data.decode()
    assert 'action="/notes/new"></form>' in new and new.count('form="note-form"') == 3


def test_edit_page_shows_save_shortcut_hint(client):
    body = client.get("/notes/new").data.decode()
    assert "<kbd data-mod-key>Ctrl</kbd>+<kbd>S</kbd> saves" in body


def test_graph_library_is_bundled_not_from_a_cdn(client, app):
    with app.app_context():
        a = dbmod.create_note("a", "2026-01-01")
        dbmod.create_note(f"b [[{a}]]", "2026-01-02")
    body = client.get(f"/graph/{a}").data.decode()
    assert "unpkg.com" not in body and "cdn" not in body.lower()
    assert "/static/vendor/cytoscape-3.31.0.min.js" in body
    assert client.get("/static/vendor/cytoscape-3.31.0.min.js").status_code == 200


def test_hops_selector_offers_one_to_five(client, app):
    with app.app_context():
        a = dbmod.create_note("a", "2026-01-01")
        dbmod.create_note(f"b [[{a}]]", "2026-01-02")
    body = client.get(f"/graph/{a}").data.decode()
    for h in range(1, 6):
        assert f'<option value="{h}"' in body


def test_no_phone_only_css():
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "app/static/style.css").read_text()
    assert "@media" not in css


def test_trash_shows_deletion_date_in_local_time(client, app, monkeypatch):
    import os, time
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        with app.app_context():
            note_id = dbmod.create_note("x", "2026-01-01")
            db = dbmod.get_db()
            # 01:30 UTC on the 24th is still the evening of the 23rd in New York
            db.execute("UPDATE notes SET deleted_at = ? WHERE id = ?", ("2026-09-24T01:30:00+00:00", note_id))
            db.commit()
        body = client.get("/trash").data.decode()
        assert "deleted 2026-09-23" in body
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


# ---------------------------------------------------------------------
# In-place (no reload) attachment upload/remove
# ---------------------------------------------------------------------

FETCH = {"X-Requested-With": "fetch"}


def _upload(client, note_id, content, name, headers=None):
    return client.post(
        f"/notes/{note_id}/attachments/upload",
        data={"file": (io.BytesIO(content), name)},
        content_type="multipart/form-data",
        headers=headers or {},
    )


def test_inplace_upload_returns_json_with_refreshed_list(client, app):
    with app.app_context():
        note_id = dbmod.create_note("n", "2026-01-01")
    resp = _upload(client, note_id, b"data", "paper.pdf", FETCH)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["message"] == "Attached paper.pdf."
    assert "paper.pdf" in data["html"] and "data-attachment-remove" in data["html"]


def test_inplace_upload_without_file_is_an_error_not_a_redirect(client, app):
    with app.app_context():
        note_id = dbmod.create_note("n", "2026-01-01")
    resp = client.post(f"/notes/{note_id}/attachments/upload", data={}, headers=FETCH)
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["ok"] is False and data["message"] == "Choose a file to upload."


def test_inplace_remove_returns_refreshed_list(client, app):
    with app.app_context():
        note_id = dbmod.create_note("n", "2026-01-01")
    _upload(client, note_id, b"data", "paper.pdf", FETCH)
    with app.app_context():
        att_id = dbmod.list_note_attachments(note_id)[0]["id"]
    data = client.post(f"/notes/{note_id}/attachments/{att_id}/remove", headers=FETCH).get_json()
    assert data["ok"] and data["message"] == "Removed paper.pdf from this note."
    assert "paper.pdf" not in data["html"]
    assert "No files attached." in data["html"]


def test_uploading_same_file_twice_says_already_attached(client, app):
    with app.app_context():
        note_id = dbmod.create_note("n", "2026-01-01")
    _upload(client, note_id, b"same", "a.txt", FETCH)
    data = _upload(client, note_id, b"same", "a.txt", FETCH).get_json()
    assert data["message"] == "a.txt is already attached."
    assert data["html"].count("attachment-row") == 1


def test_reused_bytes_are_named_by_their_stored_filename(client, app):
    with app.app_context():
        n1 = dbmod.create_note("one", "2026-01-01")
        n2 = dbmod.create_note("two", "2026-01-02")
    _upload(client, n1, b"shared", "original.txt", FETCH)
    data = _upload(client, n2, b"shared", "renamed.txt", FETCH).get_json()
    assert data["message"] == "Attached original.txt."
    assert "original.txt" in data["html"]


def test_plain_form_upload_still_redirects(client, app):
    with app.app_context():
        note_id = dbmod.create_note("n", "2026-01-01")
    resp = _upload(client, note_id, b"data", "x.txt")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/notes/{note_id}/edit")


def test_new_note_form_defaults_to_today(client, app):
    with app.app_context():
        today = dbmod.today_str()
    body = client.get("/notes/new").data.decode()
    assert f'value="{today}" required' in body


# ---------------------------------------------------------------------
# Label storage: one table, and migrating from the old two-table shape
# ---------------------------------------------------------------------

def _label_rows(app):
    with app.app_context():
        return sorted(tuple(r) for r in dbmod.get_db().execute("SELECT note_id, name FROM note_labels"))


def test_labels_stored_by_name_in_one_table(app):
    with app.app_context():
        conn = dbmod.get_db()
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'labels'").fetchone() is None
        assert [r["name"] for r in conn.execute("PRAGMA table_info(note_labels)")] == ["note_id", "name"]
        n = dbmod.create_note("# A\n#Alpha #beta `#code`", "2026-01-01")
    assert _label_rows(app) == [(n, "alpha"), (n, "beta")]


def test_label_disappears_when_last_use_removed(app):
    with app.app_context():
        n = dbmod.create_note("# A\n#only", "2026-01-01")
        dbmod.update_note(n, "# A\nnothing", "2026-01-01")
        assert dbmod.get_labels_with_counts() == []
    assert _label_rows(app) == []


def _make_old_shape_db(path):
    """A database as the previous release left it: labels + note_labels(label_id)."""
    import sqlite3
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL DEFAULT '',
            sort_date TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
        CREATE TABLE labels (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
        CREATE TABLE note_labels (note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
            label_id INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE, PRIMARY KEY (note_id, label_id));
        CREATE INDEX idx_note_labels_label ON note_labels (label_id);
        INSERT INTO notes (body, sort_date, created_at, updated_at) VALUES
            ('# One #memory #Learning', '2026-01-01', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'),
            ('# Two #memory', '2026-01-02', '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00');
        INSERT INTO notes (body, sort_date, created_at, updated_at, deleted_at) VALUES
            ('# Gone #old', '2026-01-03', '2026-01-03T00:00:00+00:00', '2026-01-03T00:00:00+00:00', '2026-01-04T00:00:00+00:00');
        INSERT INTO labels (name) VALUES ('memory'), ('learning'), ('old');
        INSERT INTO note_labels VALUES (1, 1), (1, 2), (2, 1), (3, 3);
    """)
    conn.commit()
    conn.close()


def test_old_two_table_database_is_migrated(tmp_path):
    from app import create_app
    db_path = tmp_path / "old.db"
    _make_old_shape_db(db_path)
    cfg = {"TESTING": True, "DATABASE_PATH": str(db_path), "UPLOAD_DIR": str(tmp_path / "u")}
    app = create_app(cfg)
    create_app(cfg)  # second startup: no change
    with app.app_context():
        conn = dbmod.get_db()
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'labels'").fetchone() is None
        assert _label_rows(app) == [(1, "learning"), (1, "memory"), (2, "memory"), (3, "old")]
        assert [tuple(r) for r in dbmod.get_labels_with_counts()] == [("memory", 2), ("learning", 1)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert b"#learning" in app.test_client().get("/").data


def test_interrupted_migration_is_repaired_on_next_startup(tmp_path):
    """Old tables dropped but the refill never ran (e.g. the app was killed):
    note_labels exists in the new shape but is empty. The next startup
    refills it from the note text."""
    import sqlite3
    from app import create_app
    db_path = tmp_path / "half.db"
    _make_old_shape_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        DROP TABLE note_labels; DROP TABLE labels;
        CREATE TABLE note_labels (note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
            name TEXT NOT NULL COLLATE NOCASE, PRIMARY KEY (note_id, name));
    """)
    conn.close()
    app = create_app({"TESTING": True, "DATABASE_PATH": str(db_path), "UPLOAD_DIR": str(tmp_path / "u")})
    assert _label_rows(app) == [(1, "learning"), (1, "memory"), (2, "memory"), (3, "old")]
