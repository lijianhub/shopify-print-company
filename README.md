# Catalogue Ops Demo: The Print Company

A small, working toolkit built on The Print Company's **real public Shopify catalogue**, showing the kind of work in the Marketing & Automation Intern role: keeping Shopify product data in shape, basic SEO, and keeping a Claude "AI brain" up to date with current products and prices.

Built with Claude Code. Python standard library only; nothing to install.

> Read-only: it uses the public catalogue feed (`/products.json`) and public pages that every Shopify store exposes, at most one request per second. Nothing on the website is changed. The store's product data is saved locally for offline use and is **not** included in this repository.

## What it does

| Tool | What you get | Why it matters |
|---|---|---|
| **Catalogue sync** | All products, variants, prices, options and images in a local database; a change log of what changed on the website since the last sync | One source of truth for checks, reports and AI files |
| **Data-quality checks** | Missing SKUs, missing image alt text, products without a type, short descriptions, inconsistent option names (Colour/Color), duplicate titles, $0 "price on request" items, each with a suggested fix and a CSV fix list | Cleaner Shopify data, better search and accessibility, easier stock and reporting |
| **AI brain files** | `products.md` (product guide by type), `pricing.json` (every variant and price), `SKILL.md` (draft Claude skill for quote replies) | Claude answers from exact, current prices instead of guessing |
| **Quote helper** | Pick a product and options: exact price plus a drafted reply for a person to check and send; quote-only items get a "custom quote" reply instead of $0 | Faster, consistent quote replies |
| **SEO page check** (`seo_audit.py`) | Broken internal links, missing meta descriptions and H1s, image alt text, duplicate titles across pages | Quick SEO wins and a tidy site |

## Snapshot of findings (18 October 2026)

From the public catalogue (419 products, 2,693 variants) and a sample of 98 pages:

- **2,428 of 2,693 variants (90%) have no SKU**
- **649 of 649 product images have no alt text**
- **89 products have no product type**, many of them client-specific items
- **5 broken links in the main menu**: four "Eco Friendly" collections, and "How to Supply Artwork", which redirects twice and ends on a 404
- **About 40 pages have no meta description**; 35 pages have no main heading (H1)
- Option names are inconsistent: "Colour" and "Color", "Count" and "Quantity", one "Varations"

Automated checks can produce false positives; everything above was spot-checked by hand, and each fix should be confirmed in the Shopify admin first.

## How to run

```
cd tpc_catalogue_demo
python dashboard.py
```

Opens **http://localhost:8001**. Click **Fetch live data** once (it saves an offline copy).

**Demo guide:** the bar at the top walks through the demo in 7 steps. Click **Next** and each step opens the right view, runs the action and shows a one-line talking point. Or explore freely:

1. **Left:** browse the real catalogue. Filter by "No product type", "Variants without SKU" or "Images without alt", and click a product to see its variants, SKUs and alt text.
2. **Data quality:** click a check to list affected products with suggested fixes. **Fix list CSV** downloads every variant and image to fix.
3. **AI brain:** download `products.md`, `pricing.json` and `SKILL.md` for a Claude Project or skill.
4. **Quote helper:** choose a product and options, draft a reply, copy it.
5. **Changes:** click **Simulate a change** to see how the next sync reports price, availability and new-product changes (applied to a local copy only).

SEO page check (about 4 minutes, polite rate):

```
python seo_audit.py https://theprintcompany.co.nz --max-per-type 30
```

Writes `output/report.html` and `output/issues.csv`.

Requirements: Python 3.9 or later. Without internet, use **Use offline copy** after one successful fetch.

## Files

| File | Purpose |
|---|---|
| `storefront.py` | Reads the public catalogue, paced; saves and loads the offline copy |
| `catalog.py` | Loads it into SQLite and records changes between syncs |
| `quality.py` | Data-quality rules and suggested fixes (SKU pattern, alt text, product type) |
| `brain.py` | AI brain export and the quote helper |
| `dashboard.py` / `dashboard.html` | Browser dashboard |
| `seo_audit.py` | Page-level SEO and broken-link check |
| `schema.sql` | Database tables |

## Next steps (not built)

- **Run on a schedule** (for example nightly) and post a short summary to the team, e.g. with n8n or a scheduled job.
- **Write fixes back** through the Shopify Admin API or a Shopify CSV import, after a person approves them.
- **Use the AI brain files** in a Claude Project, and test the quote-reply skill against real enquiries.
- **Add client portals and pricing rules** (GST basis, lead times, rush jobs) once confirmed with the team.

## Notes

- Suggested SKUs follow a simple pattern (`TPC-<product>-<options>`) as a starting point; the real scheme should come from the team.
- Suggested product types reuse the store's existing types where possible; types marked "(new)" don't exist yet.
- The SEO check reads the public sitemap and pages only, one request per second.
