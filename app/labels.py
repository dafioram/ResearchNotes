"""
The feed sidebar's label list (spec §6.4): labels packed as chips, labels
sharing a namespace ("physics-mechanics", "physics-quantum") grouped as one
"#physics-*" chip that opens in place, in the order the person chose
(Recent, Most used, A–Z), the first CAP shown with the rest behind "Show
all" -- except A–Z, which shows everything. Labels in the current search
come first, so a highlighted one is never hidden.
"""

from __future__ import annotations

from . import db

ORDERS = {"recent": "Recent", "used": "Most used", "az": "A–Z"}
DEFAULT_ORDER = "recent"
COOKIE = "label_order"
CAP = 36


def namespaces(names) -> dict[str, list[str]]:
    """prefix -> its labels, for each prefix (a label's part before its
    first "-") that two or more labels share. A label that *is* the prefix
    ("physics" beside "physics-quantum") belongs to it too, first. A lone
    "long-term" isn't a namespace."""
    names = set(names)
    by_prefix: dict[str, list[str]] = {}
    for name in sorted(names):
        if "-" in name:
            by_prefix.setdefault(name.split("-", 1)[0], []).append(name)
    groups = {}
    for prefix, members in by_prefix.items():
        if prefix in names:
            members = [prefix] + members
        if len(members) >= 2:
            groups[prefix] = members
    return groups


def _ordered(items, order):
    """Sort label/group dicts in place for `order`: by note count, by when
    last used (labels not used lately follow, by count), or by name."""
    items.sort(key=lambda i: (-i["count"], i["name"]))
    if order == "recent":
        items.sort(key=lambda i: i["recent"] or "", reverse=True)  # stable
    elif order == "az":
        items.sort(key=lambda i: i["name"])
    return items


def sidebar(query, order: str, url_for_token) -> dict:
    """Everything the template needs: `entries` (labels and groups, in
    order, each with its search-toggling url and whether it's in the
    search), how many labels there are, and whether the list is capped.
    `url_for_token("#name")` gives a label's link (search.toggle)."""
    in_search = set(query.labels) | set(query.required_labels)
    counts = {r["name"]: r["count"] for r in db.get_labels_with_counts()}
    recency = db.label_recency() if order == "recent" else {}

    def label(name, short=None):
        return {
            "kind": "label", "name": name, "short": short or "#" + name,
            "count": counts[name], "recent": recency.get(name),
            "url": url_for_token("#" + name), "active": name in in_search,
        }

    groups = namespaces(counts)
    grouped = {name for members in groups.values() for name in members}
    entries = [label(name) for name in counts if name not in grouped]
    for prefix, members in groups.items():
        children = [label(m, "#" + m if m == prefix else "-" + m.split("-", 1)[1]) for m in members]
        bare = [c for c in children if c["name"] == prefix]
        rest = _ordered([c for c in children if c["name"] != prefix], order)
        term = prefix + "-*"
        active = term in in_search or any(c["active"] for c in children)
        entries.append({
            "kind": "group", "name": prefix, "children": bare + rest,
            "count": db.labels_note_count([term]),
            "recent": max((c["recent"] for c in children if c["recent"]), default=None),
            "url": url_for_token("#" + term), "active": term in in_search,
            # opened when anything in it is in the search
            "open": active, "pinned": active,
        })
    _ordered(entries, order)
    entries.sort(key=lambda e: not (e.get("pinned") or e["active"]))  # search first

    capped = order != "az" and len(entries) > CAP
    for i, entry in enumerate(entries):
        entry["over_cap"] = capped and i >= CAP
    return {
        "entries": entries,
        "label_count": len(counts),
        "capped": capped,
        "hidden": max(0, len(entries) - CAP) if capped else 0,
        "order": order,
        "orders": ORDERS,
    }
