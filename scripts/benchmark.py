#!/usr/bin/env python3
"""
Scale check: build a database of realistic notes -- 10 a day, so the
default 36,500 is ten years -- in a throwaway folder, then time the main
pages against it.

    python scripts/benchmark.py                 # ten years
    python scripts/benchmark.py --notes 3650    # one year

Your own data/ folder is never touched. Run it before and after a change:
a page should cost about the same however many notes there are (spec §4.2).
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app import db  # noqa: E402

COMMON = (
    "the of and to a in is that for it as with was on be by this are from or at an which "
    "not have but has were their more can we they these also other been model data results "
    "method study effect between than both however our using paper theory evidence analysis "
    "approach question problem idea argument"
).split()
SYLLABLES = ["ka", "lo", "mi", "ne", "ru", "ta", "vo", "shi", "pa", "de", "gra", "pho",
             "tri", "stel", "mon", "quar", "zen", "lux", "fer", "bio"]


def seed(app, count: int, rng: random.Random) -> list[str]:
    """`count` notes, ten a day up to today. Text follows a Zipf curve like
    real prose; 1-3 labels each from a vocabulary that grows over time;
    0-3 [[links]], mostly to recent notes, with 5% hub notes of 10-30 links
    and 1% links to notes that don't exist; 2% in Trash; 3% with a file.
    Returns the vocabulary, most common word first."""
    rare = sorted({"".join(rng.choice(SYLLABLES) for _ in range(rng.randint(2, 4)))
                   for _ in range(9000)})
    vocab = COMMON + rare
    weights = [1 / (i + 1) ** 1.05 for i in range(len(vocab))]

    def words(k):
        return " ".join(rng.choices(vocab, weights=weights, k=k))

    start = date.today() - timedelta(days=count // 10)
    hubs: list[int] = []
    notes, history, files = [], [], []
    for i in range(1, count + 1):
        day = start + timedelta(days=(i - 1) // 10)
        parts = [f"# {words(rng.randint(3, 7)).capitalize()}"]
        for _ in range(rng.randint(1, 4)):
            if rng.random() < 0.2:
                parts.append("\n".join(f"- {words(rng.randint(4, 12))}" for _ in range(rng.randint(2, 5))))
            else:
                parts.append("\n".join(words(rng.randint(8, 20)) for _ in range(rng.randint(1, 4))))
        is_hub = i > 50 and rng.random() < 0.05
        refs = set()
        for _ in range(rng.randint(10, 30) if is_hub else rng.choices([0, 1, 2, 3], [35, 35, 20, 10])[0]):
            if i == 1:
                break
            r = rng.random()
            if r < 0.6:
                ref = rng.randint(max(1, i - 300), i - 1)
            elif r < 0.85 or not hubs:
                ref = rng.randint(1, i - 1)
            else:
                ref = rng.choice(hubs[-50:])
            refs.add(count + rng.randint(1, 1000) if rng.random() < 0.01 else ref)
        if is_hub:
            hubs.append(i)
        if refs:
            parts.append("See " + ", ".join(f"[[{r}]]" for r in sorted(refs)) + f" -- {words(6)}")
        vocab_size = 20 + i // 100
        labels = {f"topic{j}" for j in rng.choices(range(vocab_size), weights=[1 / (j + 1) for j in range(vocab_size)],
                                                    k=rng.randint(1, 3))}
        parts.append(" ".join(f"#{label}" for label in sorted(labels)))

        created = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(minutes=rng.randint(420, 1380))
        ts = created.isoformat(timespec="seconds")
        deleted = ts if rng.random() < 0.02 else None
        notes.append((i, "\n\n".join(parts), day.isoformat(), ts, ts, deleted))
        history.append(("created", i, ts, ts, "{}"))
        if rng.random() < 0.35:
            edited = (created + timedelta(days=rng.randint(0, 30))).isoformat(timespec="seconds")
            history.append(("edited", i, edited, edited, json.dumps({"lines_added": 2, "lines_removed": 1})))
        if deleted:
            history.append(("deleted", i, ts, ts, "{}"))
        if rng.random() < 0.03:
            files.append((i, f"{i:064x}", f"paper-{i}.pdf", ts))

    with app.app_context():
        conn = db.get_db()
        with conn:
            conn.executemany("INSERT INTO notes (id, body, sort_date, created_at, updated_at, deleted_at) "
                             "VALUES (?, ?, ?, ?, ?, ?)", notes)
            conn.executemany("INSERT INTO activity (kind, note_id, created_at, updated_at, detail) "
                             "VALUES (?, ?, ?, ?, ?)", history)
            for note_id, file_hash, name, ts in files:
                cur = conn.execute("INSERT INTO attachments (hash, filename, extension, mime_type, size, created_at) "
                                   "VALUES (?, ?, '.pdf', 'application/pdf', 1024, ?)", (file_hash, name, ts))
                conn.execute("INSERT INTO note_attachments (note_id, attachment_id) VALUES (?, ?)",
                             (note_id, cur.lastrowid))
        db.reindex()  # labels, links and search, by the app's own rules
    return vocab


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--notes", type=int, default=36500, help="how many notes (default: ten years' worth)")
    parser.add_argument("--runs", type=int, default=5, help="timed runs per page; the median is shown")
    args = parser.parse_args()
    rng = random.Random(42)

    with tempfile.TemporaryDirectory() as tmp:
        config = {"DATABASE_PATH": f"{tmp}/notes.db", "UPLOAD_DIR": f"{tmp}/uploads",
                  "SECRET_KEY": "benchmark", "PAGE_SIZE": 50}
        app = create_app(config)
        started = time.perf_counter()
        vocab = seed(app, args.notes, rng)
        uncommon = vocab[len(COMMON) + 1000]  # in a few notes per thousand
        print(f"Seeded {args.notes:,} notes in {time.perf_counter() - started:.1f} s; "
              f"database {Path(config['DATABASE_PATH']).stat().st_size / 1e6:.1f} MB.")

        started = time.perf_counter()
        app = create_app(config)
        startup_ms = (time.perf_counter() - started) * 1000

        with app.app_context():
            conn = db.get_db()
            live = [r[0] for r in conn.execute("SELECT id FROM notes WHERE deleted_at IS NULL ORDER BY id")]
            linked = {r[0] for r in conn.execute("SELECT from_note_id FROM note_links")}
            recent = next(n for n in reversed(live) if n in linked)
            label = conn.execute("SELECT name FROM note_labels GROUP BY name ORDER BY COUNT(*) DESC").fetchone()[0]
            incoming: dict[int, int] = {}
            for (target,) in conn.execute("SELECT to_note_id FROM note_links"):
                incoming[target] = incoming.get(target, 0) + 1
            hub = max((n for n in live if n in incoming), key=incoming.get)

        client = app.test_client()
        fetch = {"X-Requested-With": "fetch"}
        pages = [
            ("Feed, page 1", "get", "/", {}),
            ("Feed, page 10", "get", "/?page=10", {}),
            (f"Label filter (#{label})", "get", f"/?label={label}", {}),
            ("Search: common word", "get", "/?q=model", {}),
            (f"Search: uncommon word ({uncommon})", "get", f"/?q={uncommon}", {}),
            ("Open a note", "get", f"/notes/{recent}", {}),
            (f"Open a hub note ({incoming[hub]} backlinks)", "get", f"/notes/{hub}", {}),
            ("Edit a note", "get", f"/notes/{recent}/edit", {}),
            ("New note page", "get", "/notes/new", {}),
            ("Expand a card", "get", f"/notes/{recent}/fragment", {}),
            ("Save a note", "post", f"/notes/{recent}/edit",
             {"data": {"body": "# Benchmark\n\nedited #topic1 [[3]]", "sort_date": date.today().isoformat()},
              "headers": fetch}),
            ("Orphans", "get", "/orphans", {}),
            ("Attachments", "get", "/attachments", {}),
            ("History", "get", "/history", {}),
            ("Trash", "get", "/trash", {}),
            ("Graph data, 1 hop", "get", f"/api/graph/{recent}?hops=1", {}),
            ("Graph data, 3 hops", "get", f"/api/graph/{recent}?hops=3", {}),
        ]
        print(f"\n{'Page':44s} {'median ms':>10s}")
        print(f"{'Startup (create_app)':44s} {startup_ms:10.1f}")
        for name, method, url, kwargs in pages:
            call = getattr(client, method)
            response = call(url, **kwargs)  # warm-up
            assert response.status_code in (200, 302), (name, response.status_code)
            times = []
            for _ in range(args.runs):
                started = time.perf_counter()
                call(url, **kwargs)
                times.append((time.perf_counter() - started) * 1000)
            print(f"{name:44s} {statistics.median(times):10.1f}")


if __name__ == "__main__":
    main()
