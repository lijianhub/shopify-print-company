"""Load a catalogue snapshot into SQLite and record what changed since the previous sync."""

import json
import re
import sqlite3
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, d):
        self.parts.append(d)


def html_to_text(body):
    p = _Text()
    p.feed(body or "")
    return re.sub(r"\s+", " ", " ".join(p.parts)).strip()


def _previous(conn):
    prods = {r["id"]: dict(r) for r in conn.execute("SELECT id, title, product_type FROM products")}
    vars_ = {r["id"]: dict(r) for r in conn.execute("SELECT id, product_id, title, price, available FROM variants")}
    return prods, vars_


def sync(conn, snapshot, source, log=print):
    """Replace the local copy with the snapshot; log differences against the previous copy."""
    old_p, old_v = _previous(conn)
    first_load = not old_p
    ts = now_iso()
    changes = []

    def change(pid, title, kind, detail):
        changes.append((ts, pid, title, kind, detail))

    conn.execute("DELETE FROM images")
    conn.execute("DELETE FROM variants")
    conn.execute("DELETE FROM products")
    n_var = 0
    for p in snapshot["products"]:
        conn.execute("""INSERT INTO products(id, handle, title, product_type, vendor, tags, description, options,
                        published_at, updated_at, synced_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                     (p["id"], p["handle"], p["title"], p.get("product_type") or "", p.get("vendor"),
                      ", ".join(p.get("tags") or []), html_to_text(p.get("body_html")),
                      json.dumps([{"name": o["name"], "values": o["values"]} for o in p.get("options", [])]),
                      p.get("published_at"), p.get("updated_at"), ts))
        for v in p["variants"]:
            n_var += 1
            conn.execute("""INSERT INTO variants(id, product_id, title, option1, option2, option3, sku, price,
                            compare_at, available, position) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                         (v["id"], p["id"], v["title"], v.get("option1"), v.get("option2"), v.get("option3"),
                          v.get("sku") or "", float(v["price"]),
                          float(v["compare_at_price"]) if v.get("compare_at_price") else None,
                          int(bool(v.get("available"))), v.get("position")))
            if not first_load:
                ov = old_v.get(v["id"])
                if ov and ov["price"] != float(v["price"]):
                    change(p["id"], p["title"], "price",
                           f"{v['title']}: ${ov['price']:.2f} -> ${float(v['price']):.2f}")
                if ov and ov["available"] != int(bool(v.get("available"))):
                    change(p["id"], p["title"], "availability",
                           f"{v['title']}: {'available' if v.get('available') else 'sold out'}")
        for img in p.get("images", []):
            conn.execute("INSERT OR IGNORE INTO images(id, product_id, src, alt, position, variant_ids) VALUES (?,?,?,?,?,?)",
                         (img["id"], p["id"], img.get("src"), img.get("alt"), img.get("position"),
                          json.dumps(img.get("variant_ids") or [])))
        if not first_load:
            op = old_p.get(p["id"])
            if op is None:
                change(p["id"], p["title"], "new", f"New product ({len(p['variants'])} variants)")
            else:
                if op["title"] != p["title"]:
                    change(p["id"], p["title"], "title", f"'{op['title']}' -> '{p['title']}'")
                if (op["product_type"] or "") != (p.get("product_type") or ""):
                    change(p["id"], p["title"], "type", f"'{op['product_type']}' -> '{p.get('product_type')}'")
    if not first_load:
        current = {p["id"] for p in snapshot["products"]}
        for pid, op in old_p.items():
            if pid not in current:
                change(pid, op["title"], "removed", "No longer on the website")
    conn.executemany("INSERT INTO changes(at, product_id, title, kind, detail) VALUES (?,?,?,?,?)", changes)
    conn.execute("INSERT INTO sync_runs(at, source, fetched_at, products, variants, changes) VALUES (?,?,?,?,?,?)",
                 (ts, source, snapshot.get("fetched_at"), len(snapshot["products"]), n_var, len(changes)))
    conn.commit()
    log(f"Synced {len(snapshot['products'])} products, {n_var} variants"
        + (" (first load)" if first_load else f", {len(changes)} change(s) since last sync"))
    return len(changes)
