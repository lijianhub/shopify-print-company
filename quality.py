"""Catalogue data-quality rules, each with a concrete suggested fix.

Suggestions are drafts for a person to review in Shopify, never applied automatically.
"""

import json
import re
from collections import Counter, defaultdict

# Title keywords -> product type. Uses the store's existing types where one fits;
# types marked (new) do not exist yet and would need to be created.
TYPE_RULES = [
    (r"\bsticker|\blabel", "Stickers"),
    (r"t-?shirt|\btee\b", "T-Shirts"),
    (r"hoodie", "Hoodies"),
    (r"\bcap\b|beanie|\bhat\b", "Headwear"),
    (r"jersey|sweatpant|polo", "Apparel"),
    (r"yearbook|diar(y|ies)|autograph book|booklet|prospectus|guide\b", "Books & Booklets (new)"),
    (r"folder|certificate|card holder", "Stationery (new)"),
    (r"coaster", "Homeware"),
    (r"luggage tag|keyring", "Keyring"),
    (r"chocolate|candy|sweet", "Sweets"),
    (r"box\b|packaging|wrap", "Packaging (new)"),
    (r"banner", "Banners"),
    (r"\bmug|bottle|cup\b|tumbler", "Drinkware"),
    (r"\bpen\b|pens\b", "Pens"),
    (r"\bbag\b|tote", "Bags"),
    (r"flag", "Flags"),
    (r"business card|greeting card|postcard|\bcards?\b", "Cards (new)"),
    (r"flyer|leaflet|brochure", "Flyers & Brochures (new)"),
    (r"sign|corflute", "Signage"),
]

OPTION_SPELLING = {"Color": "Colour", "Varations": "Variations", "Frame Color Options": "Frame Colour"}


def suggest_type(title, tags):
    text = f"{title} {tags}".lower()
    for pattern, ptype in TYPE_RULES:
        if re.search(pattern, text):
            return ptype
    return None


def _abbr(text, n):
    words = re.findall(r"[A-Za-z0-9]+", text or "")
    if not words:
        return ""
    if len(words) == 1:
        return words[0][:n].upper()
    return "".join(w[0] for w in words)[:n].upper()


def suggest_sku(handle, variant):
    parts = [_abbr(handle.replace("-", " "), 5)]
    for opt in (variant["option1"], variant["option2"], variant["option3"]):
        if opt and opt != "Default Title":
            parts.append(re.sub(r"[^A-Za-z0-9]", "", opt)[:6].upper())
    return "TPC-" + "-".join(p for p in parts if p)


def suggest_alt(title, variants_by_id, variant_ids, position):
    names = [variants_by_id[v] for v in variant_ids if v in variants_by_id]
    colour = names[0] if names else None
    alt = f"{title} – {colour}" if colour else (title if position == 1 else f"{title} – view {position}")
    return alt[:125]


def run(conn, log=print):
    conn.execute("DELETE FROM issues")
    found = []

    def issue(pid, rule, sev, target, current, suggestion):
        found.append((pid, rule, sev, target, current, suggestion))

    products = [dict(r) for r in conn.execute("SELECT * FROM products")]
    variants = defaultdict(list)
    for v in conn.execute("SELECT * FROM variants ORDER BY position"):
        variants[v["product_id"]].append(dict(v))
    images = defaultdict(list)
    for i in conn.execute("SELECT * FROM images ORDER BY position"):
        images[i["product_id"]].append(dict(i))
    titles = Counter(p["title"].strip().lower() for p in products)

    for p in products:
        pid, title = p["id"], p["title"]
        vs = variants[pid]
        if not p["product_type"]:
            issue(pid, "Missing product type", "Medium", "product", "",
                  suggest_type(title, p["tags"]) or "Choose a type (no keyword match)")
        no_sku = [v for v in vs if not v["sku"]]
        if no_sku:
            # one row per product keeps the list readable; the CSV export lists every variant
            ex = suggest_sku(p["handle"], no_sku[0])
            issue(pid, "Variants without SKU", "Medium", f"{len(no_sku)} of {len(vs)} variants",
                  "", f"e.g. {ex} (pattern: TPC-<product>-<options>)")
        if not images[pid]:
            issue(pid, "No product images", "High", "product", "", "Add at least one product photo")
        by_id = {v["id"]: " / ".join(o for o in (v["option1"], v["option2"], v["option3"]) if o and o != "Default Title")
                 for v in vs}
        missing_alt = [i for i in images[pid] if not (i["alt"] or "").strip()]
        if missing_alt:
            first = missing_alt[0]
            issue(pid, "Images without alt text", "Medium", f"{len(missing_alt)} of {len(images[pid])} images", "",
                  f"e.g. '{suggest_alt(title, by_id, json.loads(first['variant_ids']), first['position'])}'")
        desc_len = len(p["description"] or "")
        if desc_len == 0:
            issue(pid, "Missing description", "High", "product", "", "Write 2-3 sentences: what it is, sizes/finish, who it suits")
        elif desc_len < 120:
            issue(pid, "Short description", "Low", "product", f"{desc_len} chars", "Expand to 2-3 sentences for search and buyers")
        if len(title) > 70:
            issue(pid, "Title too long", "Low", "product", f"{len(title)} chars", "Keep under ~70 chars; move details to the description")
        if titles[title.strip().lower()] > 1:
            issue(pid, "Duplicate title", "Medium", "product", title, "Make each title unique (add size, material or client)")
        if not p["tags"]:
            issue(pid, "No tags", "Low", "product", "", "Add tags used by collections and search filters")
        for o in json.loads(p["options"] or "[]"):
            if o["name"] in OPTION_SPELLING:
                issue(pid, "Inconsistent option name", "Low", "option", o["name"], f"Rename to '{OPTION_SPELLING[o['name']]}'")
        zero = [v for v in vs if (v["price"] or 0) <= 0]
        if vs and len(zero) == len(vs):
            issue(pid, "Price on request ($0)", "Low", "all variants", "$0.00",
                  "Looks quote-only or client-specific: show 'Request a quote' instead of $0")
        elif zero:
            issue(pid, "Zero price on some variants", "High", f"{len(zero)} of {len(vs)} variants", "$0.00",
                  "Set a price or remove these variants")
        if vs and not any(v["available"] for v in vs):
            issue(pid, "Sold out (all variants)", "Low", "product", "", "Check stock or hide from collections")

    conn.executemany("INSERT INTO issues(product_id, rule, severity, target, current, suggestion) VALUES (?,?,?,?,?,?)", found)
    conn.commit()
    log(f"Quality check: {len(found)} findings across {len({f[0] for f in found})} products")
    return len(found)


def variant_fix_rows(conn):
    """Every variant without a SKU and every image without alt text, with suggestions (for CSV export)."""
    rows = []
    for v in conn.execute("""SELECT v.*, p.handle, p.title AS ptitle FROM variants v JOIN products p ON p.id=v.product_id
                             WHERE v.sku='' ORDER BY p.title, v.position"""):
        rows.append({"handle": v["handle"], "product": v["ptitle"], "field": "Variant SKU",
                     "variant_or_image": v["title"], "current": "", "suggested": suggest_sku(v["handle"], v)})
    for p in conn.execute("SELECT id, handle, title FROM products"):
        by_id = {v["id"]: " / ".join(o for o in (v["option1"], v["option2"], v["option3"]) if o and o != "Default Title")
                 for v in conn.execute("SELECT * FROM variants WHERE product_id=?", (p["id"],))}
        for i in conn.execute("SELECT * FROM images WHERE product_id=? AND (alt IS NULL OR trim(alt)='') ORDER BY position", (p["id"],)):
            rows.append({"handle": p["handle"], "product": p["title"], "field": "Image Alt Text",
                         "variant_or_image": i["src"], "current": "",
                         "suggested": suggest_alt(p["title"], by_id, json.loads(i["variant_ids"]), i["position"])})
    return rows
