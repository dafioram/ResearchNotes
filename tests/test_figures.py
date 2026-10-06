"""Images in notes (spec §9.5): ![caption](/files/<hash>) shows a stored
image by the first 12+ characters of its hash; any note can show any
stored file, and doing so attaches it to that note."""

import base64
import io

import pytest

from app import db as dbmod
from app import markdown as md

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
FULL = "191ff6f6b235" + "a" * 52
FILES = {
    "191ff6f6b235": {"hash": FULL, "filename": "map.png", "image": True},
    "4372a0cb0d86": {"hash": "4372a0cb0d86" + "b" * 52, "filename": "one.pdf", "image": False},
    "abcabcabcabc": False,                                   # names more than one file
}
IMG = f'<a class="figure-link" href="/files/{FULL}"><img src="/files/{FULL}" alt="Gull Rock" loading="lazy"></a>'


# ---------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------

def test_an_image_alone_on_its_line_is_a_captioned_figure():
    assert md.render("![Gull Rock](/files/191ff6f6b235)", None, FILES) == \
        f"<figure>{IMG}<figcaption>Gull Rock</figcaption></figure>"
    assert md.render("![](/files/191ff6f6b235)", None, FILES).endswith('loading="lazy"></a></figure>')


def test_an_image_line_inside_a_paragraph_is_a_figure_between_paragraphs():
    html = md.render("before\nmore\n![Gull Rock](/files/191ff6f6b235)\nafter", None, FILES)
    assert html == f"<p>before<br>more</p>\n<figure>{IMG}<figcaption>Gull Rock</figcaption></figure>\n<p>after</p>"


def test_an_image_in_a_sentence_is_inline_and_the_hash_is_any_case():
    html = md.render("see ![Gull Rock](/files/191FF6F6B235) here", None, FILES)
    assert html == f"<p>see {IMG} here</p>"


def test_other_files_link_and_unknown_or_ambiguous_ones_show_as_typed():
    assert md.render("![the pdf](/files/4372a0cb0d86)", None, FILES) == \
        f'<p><a class="file-link" href="/files/4372a0cb0d86{"b" * 52}">the pdf</a></p>'
    missing = md.render("![x](/files/000000000000)", None, FILES)
    assert 'class="file-missing" title="No stored file starts with 000000000000">![x](/files/000000000000)' in missing
    assert "More than one stored file starts with abcabcabcabc" in md.render("![x](/files/abcabcabcabc)", None, FILES)


def test_without_a_lookup_an_image_is_a_plain_link():
    assert md.render("![Gull Rock](/files/191ff6f6b235)") == \
        '<p><a class="file-link" href="/files/191ff6f6b235">Gull Rock</a></p>'


@pytest.mark.parametrize("text", [
    "![x](https://elsewhere.org/a.png)",   # nothing from other sites
    "![x](/files/191ff6f6b23)",            # too short
    "![x](/files/../../etc/passwd)",
    "![x](data:image/png;base64,AAAA)",
])
def test_only_stored_files_are_images(text):
    assert "<img" not in md.render(text, None, FILES)


def test_a_caption_is_escaped_and_holds_no_labels():
    html = md.render('![a "b" <c> #lab](/files/191ff6f6b235)', None, FILES)
    assert 'alt="a &quot;b&quot; &lt;c&gt; #lab"' in html
    assert "<figcaption>a \"b\" &lt;c> #lab</figcaption>" in html and "label-tag" not in html
    assert md.extract_labels('![#lab](/files/191ff6f6b235)') == set()


def test_plain_text_shows_the_caption():
    assert md.first_line_text("![Gull Rock](/files/191ff6f6b235)") == "Gull Rock"
    assert md._to_plain(md.render("![Gull Rock](/files/191ff6f6b235)", None, FILES)) == "Gull Rock"


def test_file_refs_are_read_outside_code_only():
    text = "![a](/files/191FF6F6B235) [b](/files/4372a0cb0d86) `![c](/files/cccccccccccc)` $![d](/files/dddddddddddd)$"
    assert md.extract_file_refs(text) == {"191ff6f6b235", "4372a0cb0d86"}


# ---------------------------------------------------------------------
# Stored files, by prefix
# ---------------------------------------------------------------------

def _upload(client, note, data=PNG, name="map.png"):
    r = client.post(f"/notes/{note}/attachments/upload", content_type="multipart/form-data",
                    data={"file": (io.BytesIO(data), name)}, headers={"X-Requested-With": "fetch"})
    return r.get_json()


@pytest.fixture
def note(app):
    with app.app_context():
        return dbmod.create_note("# A note", "2026-01-01")


def test_an_upload_says_how_to_show_it(client, note):
    data = _upload(client, note)
    assert data["file"]["image"] is True and data["file"]["filename"] == "map.png"
    assert len(data["file"]["ref"]) == 12
    assert _upload(client, note)["file"] == data["file"]           # already attached: same answer
    assert _upload(client, note, b"%PDF-1", "one.pdf")["file"]["image"] is False


def test_files_are_served_by_a_short_hash(client, note):
    ref = _upload(client, note)["file"]["ref"]
    for url in (f"/files/{ref}", f"/files/{ref.upper()}", f"/files/{ref}{'0' * 0}"):
        r = client.get(url)
        assert r.status_code == 200 and r.data == PNG and r.mimetype == "image/png"
    assert client.get(f"/files/{ref[:11]}").status_code == 404      # too short
    assert client.get("/files/not-a-hash-at-all").status_code == 404


def test_a_prefix_naming_two_files_names_neither(app, client):
    with app.app_context():
        for i in range(2):
            dbmod.create_attachment("abcabcabcabc" + str(i) * 52, f"f{i}.png", ".png", "image/png", 1)
        assert dbmod.files_by_prefix(["abcabcabcabc", "abcabcabcabc0"]) == {
            "abcabcabcabc": False,
            "abcabcabcabc0": {"hash": "abcabcabcabc" + "0" * 52, "filename": "f0.png", "image": True,
                              "present": False},            # a record only: no file written
        }
    assert client.get("/files/abcabcabcabc").status_code == 404


# ---------------------------------------------------------------------
# Showing a file attaches it
# ---------------------------------------------------------------------

def test_showing_another_notes_image_attaches_it_and_says_so(app, client, note):
    ref = _upload(client, note)["file"]["ref"]
    with app.app_context():
        other = dbmod.create_note(f"# Elsewhere\n\n![the map](/files/{ref})\n\n![gone](/files/000000000000)", "2026-01-02")
        assert [a["filename"] for a in dbmod.list_note_attachments(other)] == ["map.png"]
        kinds = [r["kind"] for r in dbmod.get_db().execute(
            "SELECT kind FROM activity WHERE note_id = ? ORDER BY id", (other,))]
        assert kinds == ["created", "attached"]
    page = client.get(f"/notes/{other}").data.decode()
    assert '<figure><a class="figure-link" href="/files/' in page and "<figcaption>the map</figcaption>" in page
    assert 'class="file-missing"' in page
    with app.app_context():
        # Taking it out of the text keeps it attached: removing is Remove.
        dbmod.update_note(other, "# Elsewhere", "2026-01-02")
        assert len(dbmod.list_note_attachments(other)) == 1
        # And it survives the first note going for good.
        dbmod.soft_delete_note(note)
        dbmod.delete_forever([note])
        assert len(dbmod.list_note_attachments(other)) == 1


def test_an_edit_adding_a_reference_attaches_it(app, client, note):
    ref = _upload(client, note)["file"]["ref"]
    with app.app_context():
        other = dbmod.create_note("# Later", "2026-01-02")
        dbmod.update_note(other, f"# Later\n\nsee [the map](/files/{ref})", "2026-01-02")
        assert len(dbmod.list_note_attachments(other)) == 1


def test_attachment_lists_offer_the_text_to_show_a_file(client, note):
    ref = _upload(client, note, name="gull-rock_layout-v4.png")["file"]["ref"]
    _upload(client, note, b"%PDF-1", "one draft.pdf")
    edit = client.get(f"/notes/{note}/edit").data.decode()
    assert f'data-insert="![gull rock layout v4](/files/{ref})"' in edit
    assert f'data-copy="![gull rock layout v4](/files/{ref})"' in edit
    assert 'data-copy="[one_draft.pdf](/files/' in edit       # names are made safe on upload
    card = client.get(f"/notes/{note}/fragment").data.decode()       # a feed card, opened: Copy only
    assert f'data-copy="![gull rock layout v4](/files/{ref})"' in card and "data-insert" not in card


# ---------------------------------------------------------------------
# A data folder copied without all its files
# ---------------------------------------------------------------------

def test_an_image_whose_file_is_gone_says_missing_and_the_note_still_works(app, client, note):
    data = _upload(client, note, name="chart.png")
    _upload(client, note, b"%PDF-1", "paper.pdf")
    with app.app_context():
        dbmod.update_note(note, f"# A note\n\nbefore\n![Results, run 3](/files/{data['file']['ref']})\nafter", "2026-01-01")
        for a in dbmod.list_note_attachments(note):
            dbmod.attachment_path(a["hash"], a["extension"]).unlink()      # uploads/ not copied along
    page = client.get(f"/notes/{note}")
    assert page.status_code == 200
    html = page.data.decode()
    assert "Missing image: Results, run 3" in html and "<img" not in html
    assert "before" in html and "after" in html
    # The attachment list is unchanged; opening a missing file says why.
    assert "paper.pdf" in html
    with app.app_context():
        pdf = next(a for a in dbmod.list_note_attachments(note) if a["filename"] == "paper.pdf")
    r = client.get(f"/files/{pdf['hash'][:12]}")
    assert r.status_code == 404
    text = r.data.decode()
    assert "paper.pdf is missing" in text and f"uploads/{pdf['hash'][:2]}/{pdf['hash'][2:4]}/" in text

