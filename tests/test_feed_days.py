"""The feed's day headings and jumping to a month (spec §4)."""

import re
from datetime import date, timedelta

from app import db as dbmod
from app.routes import day_heading


def test_day_heading_words():
    today = date(2026, 10, 6)
    assert day_heading("2026-10-06", today) == "Today"
    assert day_heading("2026-10-05", today) == "Yesterday"
    assert day_heading("2026-03-02", today) == "Monday 2 March"
    assert day_heading("2025-12-31", today) == "Wednesday 31 December 2025"


def _seed(app, per_day):
    """Notes on given dates, `per_day` = {date: count}."""
    with app.app_context():
        for day, count in per_day.items():
            for i in range(count):
                dbmod.create_note(f"# {day} #{i}\n\n#d{i % 2}", day)


def _headings(html):
    return re.findall(r'<h2 class="feed-day" id="d-([\d-]+)">([^<]+)</h2>', html)


def test_feed_groups_cards_under_day_headings(client, app):
    today = date.today()
    _seed(app, {today.isoformat(): 2, (today - timedelta(days=1)).isoformat(): 1, "2024-02-29": 1})
    heads = _headings(client.get("/").data.decode())
    assert [h for _, h in heads] == ["Today", "Yesterday", "Thursday 29 February 2024"]


def test_a_day_cut_by_a_page_break_is_headed_on_both_pages(client, app):
    app.config["PAGE_SIZE"] = 3
    _seed(app, {"2026-01-05": 2, "2026-01-04": 3})
    assert [d for d, _ in _headings(client.get("/").data.decode())] == ["2026-01-05", "2026-01-04"]
    assert [d for d, _ in _headings(client.get("/?page=2").data.decode())] == ["2026-01-04"]


def test_label_and_filter_lists_are_dated_but_ranked_searches_are_not(client, app):
    _seed(app, {"2026-01-05": 2, "2026-01-04": 2})
    assert _headings(client.get("/?q=%23d0").data.decode())
    assert _headings(client.get("/?q=is:unlinked+-%23d1").data.decode())
    ranked = client.get("/?q=2026").data.decode()
    assert not _headings(ranked) and "note-card" in ranked
    assert 'class="jump-form"' not in ranked


def test_jump_to_a_month_opens_its_page_at_its_first_day(client, app):
    app.config["PAGE_SIZE"] = 4
    _seed(app, {"2026-03-10": 3, "2026-02-20": 3, "2026-02-01": 1, "2025-12-25": 2})
    r = client.get("/?month=2026-02")
    # 3 notes are newer than February: Feb 20 is the 4th note, on page 1
    assert r.status_code == 302 and r.headers["Location"].endswith("/#d-2026-02-20")
    r = client.get("/?month=2026-01")   # nothing in January: the next older day
    assert r.headers["Location"].endswith("/?page=2#d-2025-12-25")
    r = client.get("/?month=2025")      # a whole year works too
    assert r.headers["Location"].endswith("/?page=2#d-2025-12-25")
    r = client.get("/?month=2027-01")   # newer than everything: the top
    assert r.headers["Location"].endswith("/#d-2026-03-10")
    r = client.get("/?month=2020-01")   # older than everything: the end
    assert r.headers["Location"].endswith("/?page=3")
    page = client.get("/?page=2").data.decode()
    assert 'id="d-2025-12-25"' in page


def test_jump_keeps_the_search(client, app):
    app.config["PAGE_SIZE"] = 2
    _seed(app, {"2026-03-10": 4, "2026-02-20": 4})
    r = client.get("/?q=%23d0&month=2026-02")
    # #d0 is on half the notes: 2 in March, so February starts on page 2
    assert r.headers["Location"].endswith("/?q=%23d0&page=2#d-2026-02-20")


def test_a_bad_month_says_so(client, app):
    _seed(app, {"2026-03-10": 1})
    r = client.get("/?month=soon", follow_redirects=True)
    assert "read the month “soon”; use YYYY-MM." in r.data.decode()


def test_jump_form_is_on_dated_lists(client, app):
    _seed(app, {"2026-03-10": 1})
    page = client.get("/?q=%23d0").data.decode()
    assert 'class="jump-form"' in page
    assert '<input type="hidden" name="q" value="#d0">' in page
