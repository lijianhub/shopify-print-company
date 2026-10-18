"""Read a Shopify store's public product catalogue (/products.json) and keep an offline snapshot.

/products.json is the public storefront feed every Shopify store exposes: products, variants,
prices, images and options. No login or API key is involved. Requests are paced (default 1 per
second) and the snapshot is saved locally so the demo also works without internet.
The snapshot contains the store's own content, so it is git-ignored and never published.
"""

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT = Path(__file__).parent / "data_cache" / "products_snapshot.json"
UA = "Mozilla/5.0 (compatible; catalogue-demo; job-application research; low rate)"


def fetch_all(base_url, delay=1.0, log=print):
    products, page = [], 1
    while True:
        url = f"{base_url.rstrip('/')}/products.json?limit=250&page={page}"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=40) as r:
            batch = json.loads(r.read())["products"]
        log(f"page {page}: {len(batch)} products")
        if not batch:
            break
        products += batch
        if len(batch) < 250:
            break
        page += 1
        time.sleep(delay)
    return {"source": base_url, "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "products": products}


def save_snapshot(snapshot, path=SNAPSHOT):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot), encoding="utf-8")


def load_snapshot(path=SNAPSHOT):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import sys
    snap = fetch_all(sys.argv[1] if len(sys.argv) > 1 else "https://theprintcompany.co.nz")
    save_snapshot(snap)
    print(f"Saved {len(snap['products'])} products to {SNAPSHOT}")
