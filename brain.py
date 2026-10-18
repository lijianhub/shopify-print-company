"""Export the catalogue as 'AI brain' files Claude can use, and look up prices for quote replies.

Outputs (in ai_brain/, git-ignored because they contain the store's content):
- products.md   readable product guide grouped by type (for a Claude Project's knowledge)
- pricing.json  every variant with its options and price (exact lookups, no guessing)
- SKILL.md      draft Claude skill for answering quote requests from pricing.json
"""

import json
import re
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).parent / "ai_brain"


def _products(conn):
    rows = []
    for p in conn.execute("SELECT * FROM products ORDER BY product_type, title"):
        vs = [dict(v) for v in conn.execute("SELECT * FROM variants WHERE product_id=? ORDER BY position", (p["id"],))]
        rows.append((dict(p), vs))
    return rows


def _summary(text, limit=240):
    text = text or ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for s in sentences:
        if len(out) + len(s) > limit:
            break
        out += s + " "
    return (out.strip() or text[:limit]).strip()


def export(conn, base_url, fetched_at, log=print):
    OUT.mkdir(exist_ok=True)
    groups = defaultdict(list)
    pricing = {}
    for p, vs in _products(conn):
        opts = json.loads(p["options"] or "[]")
        groups[p["product_type"] or "Uncategorised"].append((p, vs, opts))
        pricing[p["handle"]] = {
            "title": p["title"], "type": p["product_type"] or None, "url": f"{base_url}/products/{p['handle']}",
            "options": [o["name"] for o in opts if o["name"] != "Title"],
            "price_on_request": all(v["price"] <= 0 for v in vs),
            "variants": [{"options": [x for x in (v["option1"], v["option2"], v["option3"]) if x and x != "Default Title"],
                          "price": v["price"], "sku": v["sku"] or None, "available": bool(v["available"])} for v in vs],
        }

    lines = [f"# Product guide", "",
             f"Source: {base_url} public catalogue, fetched {fetched_at}. {len(pricing)} products.",
             "Prices are as listed on the website. Confirm the GST basis and lead times before quoting.", ""]
    for ptype in sorted(groups):
        lines += [f"## {ptype}", ""]
        for p, vs, opts in groups[ptype]:
            prices = [v["price"] for v in vs]
            lines.append(f"### {p['title']}")
            lines.append(f"- Link: {base_url}/products/{p['handle']}")
            shown = [o for o in opts if o["name"] != "Title"]
            if shown:
                lines.append("- Options: " + "; ".join(
                    f"{o['name']}: {', '.join(o['values'][:8])}{' …' if len(o['values']) > 8 else ''}" for o in shown))
            if max(prices) <= 0:
                lines.append("- Price: on request (custom quote)")
            else:
                listed = [x for x in prices if x > 0]
                lines.append(f"- Price: ${min(listed):,.2f}" + (f" to ${max(listed):,.2f}" if max(listed) != min(listed) else ""))
            if p["description"]:
                lines.append(f"- About: {_summary(p['description'])}")
            lines.append("")
    (OUT / "products.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "pricing.json").write_text(json.dumps(pricing, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "SKILL.md").write_text(SKILL, encoding="utf-8")
    log(f"AI brain files written: products.md ({len(lines)} lines), pricing.json ({len(pricing)} products), SKILL.md")
    return {"products": len(pricing), "lines": len(lines)}


SKILL = """---
name: quote-replies
description: Draft replies to customer quote requests for The Print Company using the exact prices in pricing.json. Use when a customer asks how much a printed product costs or asks for a quote.
---

# Quote replies

1. Find the product in `pricing.json` (match on title or handle). If several products could fit, list them and ask which one.
2. Match every option the product needs (for example Quantity and Colour). If an option is missing, ask for it instead of guessing.
3. Quote the exact variant price from `pricing.json`. Never estimate, round or invent a price. If no variant matches the requested quantity, give the nearest listed quantities and their prices.
4. If `price_on_request` is true (or the price is 0), never quote $0: say a custom quote will be prepared and ask for the details needed.
5. If the variant is marked unavailable, say you will confirm stock and timing.
6. Include the product link. Keep the tone friendly and short, sign off as the team member.
7. Flag for a human anything outside the price list: custom sizes, rush jobs, design work, delivery outside Auckland.

Open questions to confirm with the team before using this skill with customers:
- Are website prices incl. or excl. GST?
- Standard lead times per product type.
"""


def quote(conn, handle, chosen, customer, sender, base_url):
    p = conn.execute("SELECT * FROM products WHERE handle=?", (handle,)).fetchone()
    if p is None:
        return {"error": "Product not found"}
    vs = [dict(v) for v in conn.execute("SELECT * FROM variants WHERE product_id=? ORDER BY position", (p["id"],))]
    match = next((v for v in vs if [x for x in (v["option1"], v["option2"], v["option3"]) if x] == chosen), None)
    if match is None:
        return {"error": "No variant matches those options"}
    names = [o["name"] for o in json.loads(p["options"] or "[]")]
    opts = ", ".join(f"{n}: {c}" for n, c in zip(names, chosen) if c != "Default Title")
    url = f"{base_url}/products/{handle}"
    on_request = match["price"] <= 0
    if on_request:
        line = ("this is quoted individually, so I'll put together a custom quote for you. "
                "Could you send through your artwork (or what you have in mind) and when you need it by?")
    else:
        line = f"the price is ${match['price']:,.2f} as listed on our website."
    body = (f"Hi {customer or 'there'},\n\n"
            f"Thanks for getting in touch. For {p['title']}{f' ({opts})' if opts else ''}, {line}\n\n"
            + (f"Product details: {url}\n\n" if on_request else f"You can order online here: {url}\n\n")
            + ("" if match["available"] else "This option is showing as unavailable online at the moment, "
               "so I'll confirm stock and timing for you.\n\n")
            + "If you'd like a different quantity or a proof first, just let me know.\n\n"
            f"Cheers,\n{sender or 'The Print Company team'}")
    return {"price": match["price"] if match["price"] > 0 else None, "sku": match["sku"], "available": bool(match["available"]), "text": body}
