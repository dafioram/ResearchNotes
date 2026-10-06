"""The graph's limits (spec §10): at most 3 hops, at most ~300 notes."""

from app import db as dbmod


def _star(app, spokes):
    """A centre note linked to `spokes` notes, each with one note of its own
    beyond it. Returns (centre, spokes, outer)."""
    with app.app_context():
        centre = dbmod.create_note("# Centre", "2026-01-01")
        inner = [dbmod.create_note(f"# Spoke {i}\\n\\n[[{centre}]]", "2026-01-01") for i in range(spokes)]
        outer = [dbmod.create_note(f"# Beyond {i}\\n\\n[[{s}]]", "2026-01-01") for i, s in enumerate(inner)]
    return centre, inner, outer


def test_api_hops_are_capped_at_three(client, app):
    with app.app_context():
        chain = [dbmod.create_note("# 0", "2026-01-01")]
        for i in range(1, 6):
            chain.append(dbmod.create_note(f"# {i}\\n\\n[[{chain[-1]}]]", "2026-01-01"))
    data = client.get(f"/api/graph/{chain[0]}?hops=5").get_json()
    assert sorted(n["id"] for n in data["nodes"]) == chain[:4]
    assert data["left_out"] == 0


def test_a_ring_that_would_pass_the_cap_is_cut_and_reported(app):
    centre, inner, outer = _star(app, 30)
    with app.app_context():
        data = dbmod.get_graph_data(centre, hops=2, max_nodes=40)
    ids = {n["id"] for n in data["nodes"]}
    assert len(ids) == 40
    assert set(inner) | {centre} <= ids          # the nearer ring is whole
    assert len(ids & set(outer)) == 9
    assert data["left_out"] == 21
    assert all(e["from"] in ids and e["to"] in ids for e in data["edges"])


def test_the_cut_keeps_the_most_tightly_linked_notes(app):
    with app.app_context():
        centre = dbmod.create_note("# Centre", "2026-01-01")
        a = dbmod.create_note("# A", "2026-01-01")
        b = dbmod.create_note("# B", "2026-01-01")
        loose = [dbmod.create_note(f"# Loose {i}\\n\\n[[{centre}]]", "2026-01-01") for i in range(5)]
        tight = dbmod.create_note(f"# Tight\\n\\n[[{centre}]]", "2026-01-01")
        dbmod.update_note(centre, f"# Centre\\n\\n[[{tight}]] [[{a}]] [[{b}]]", "2026-01-01")
        data = dbmod.get_graph_data(centre, hops=1, max_nodes=4)
    ids = {n["id"] for n in data["nodes"]}
    # tight links both ways; then the lowest numbers
    assert ids == {centre, tight, a, b} and data["left_out"] == 5
    assert not ids & set(loose)


def test_a_small_graph_is_not_cut(app):
    centre, inner, outer = _star(app, 5)
    with app.app_context():
        data = dbmod.get_graph_data(centre, hops=3)
    assert len(data["nodes"]) == 11 and data["left_out"] == 0


def test_page_has_a_place_for_the_message(client, app):
    centre, _, _ = _star(app, 2)
    assert 'id="graph-capped" hidden' in client.get(f"/graph/{centre}").data.decode()
