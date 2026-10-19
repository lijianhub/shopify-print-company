"""Polite SEO and content health check for a public Shopify storefront (standard library only).

Reads the public sitemap, samples pages, and checks titles, meta descriptions, H1s, image alt text,
canonical tags and broken internal links. Writes an HTML report and a CSV of issues.

Usage: python seo_audit.py https://example.co.nz [--max-per-type 40] [--delay 1.0]
Only public pages listed in the sitemap are fetched, at most one request per --delay seconds.
"""

import argparse
import csv
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

UA = "Mozilla/5.0 (compatible; SEO-health-check; job-application research; low rate)"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class Fetcher:
    def __init__(self, delay):
        self.delay, self.last = delay, 0.0
        self.count = 0

    def get(self, url, method="GET"):
        wait = self.delay - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        self.count += 1
        req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                body = r.read() if method == "GET" else b""
                return r.status, r.geturl(), body.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.geturl() or url, ""
        except Exception as e:  # network error
            return 0, url, str(e)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title, self.in_title = "", False
        self.meta_desc, self.canonical, self.noindex = None, None, False
        self.h1, self.in_h1, self.h1_text = [], False, ""
        self.images, self.links = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "meta" and (a.get("name") or "").lower() == "description":
            self.meta_desc = (a.get("content") or "").strip()
        elif tag == "meta" and (a.get("name") or "").lower() == "robots" and "noindex" in (a.get("content") or ""):
            self.noindex = True
        elif tag == "link" and "canonical" in (a.get("rel") or "").lower():
            self.canonical = a.get("href")
        elif tag == "h1":
            self.in_h1, self.h1_text = True, ""
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or ""
            self.images.append((src, a.get("alt")))
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag == "h1" and self.in_h1:
            self.in_h1 = False
            self.h1.append(" ".join(self.h1_text.split()))

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.in_h1:
            self.h1_text += data


def sitemap_urls(f, root):
    status, _, body = f.get(urllib.parse.urljoin(root, "/sitemap.xml"))
    if status != 200:
        raise RuntimeError(f"Could not read sitemap.xml (HTTP {status})")
    by_type = defaultdict(list)
    for loc in ET.fromstring(body).findall("sm:sitemap/sm:loc", NS):
        child = loc.text.strip()
        kind = re.search(r"sitemap_(\w+?)_\d", child)
        kind = kind.group(1) if kind else "other"
        s, _, b = f.get(child)
        if s == 200:
            by_type[kind] += [u.text.strip() for u in ET.fromstring(b).findall("sm:url/sm:loc", NS)]
    return by_type


def audit(root, max_per_type, delay, out_dir, log=print, progress=None):
    """Run the check, write report.html / issues.csv / results.json to out_dir and return the results.
    progress(done, total, stage) is called as pages and links are checked."""
    progress = progress or (lambda done, total, stage: None)
    f = Fetcher(delay)
    host = urllib.parse.urlparse(root).netloc.replace("www.", "")
    log(f"Reading sitemap for {host} ...")
    progress(0, 1, "Reading sitemap")
    by_type = sitemap_urls(f, root)
    totals = {k: len(v) for k, v in by_type.items()}
    log(f"Sitemap URLs: {totals}")

    issues, pages = [], []
    titles, descs = defaultdict(list), defaultdict(list)
    internal_links = Counter()

    def issue(url, kind, severity, detail):
        issues.append({"url": url, "type": kind, "severity": severity, "detail": detail})

    sample = [(k, u) for k, urls in by_type.items() if k != "other"
              for u in urls[:max_per_type] if not re.search(r"\.(md|xml|txt|json)$", u)]
    for i, (kind, url) in enumerate(sample, 1):
        status, final, body = f.get(url)
        log(f"  [{i}/{len(sample)}] {status} {url}")
        progress(i, len(sample), "Checking pages")
        if status != 200:
            issue(url, "Page not loading", "High", f"HTTP {status}")
            continue
        p = PageParser()
        p.feed(body)
        title = " ".join(p.title.split())
        pages.append({"url": url, "kind": kind, "title": title, "desc": p.meta_desc, "h1": p.h1,
                      "images": len(p.images)})
        titles[title].append(url)
        if p.meta_desc:
            descs[p.meta_desc].append(url)

        if not title:
            issue(url, "Missing title", "High", "")
        elif len(title) > 70:
            issue(url, "Title too long", "Low", f"{len(title)} chars: {title}")
        elif len(title) < 25:
            issue(url, "Title too short", "Low", f"{len(title)} chars: {title}")
        if not p.meta_desc:
            issue(url, "Missing meta description", "Medium", "")
        elif len(p.meta_desc) > 165:
            issue(url, "Meta description too long", "Low", f"{len(p.meta_desc)} chars")
        elif len(p.meta_desc) < 70:
            issue(url, "Meta description too short", "Low", f"{len(p.meta_desc)} chars: {p.meta_desc}")
        if not p.h1:
            issue(url, "Missing H1", "Medium", "")
        elif len(p.h1) > 1:
            issue(url, "Multiple H1", "Low", " | ".join(p.h1[:3]))
        if not p.canonical:
            issue(url, "Missing canonical", "Low", "")
        if p.noindex:
            issue(url, "Page set to noindex", "Medium", "")
        no_attr = [src for src, alt in p.images if src and alt is None]
        empty = [src for src, alt in p.images if src and alt is not None and not alt.strip()]
        if no_attr:
            issue(url, "Images missing alt attribute", "Medium", f"{len(no_attr)} of {len(p.images)} images")
        if empty:
            issue(url, "Images with empty alt text", "Low", f"{len(empty)} of {len(p.images)} images (fine if decorative)")
        for href in p.links:
            absu = urllib.parse.urljoin(final, href.split("#")[0])
            pu = urllib.parse.urlparse(absu)
            if pu.scheme in ("http", "https") and pu.netloc.replace("www.", "") == host:
                path = pu.path.rstrip("/") or "/"
                if not re.match(r"^/(cart|account|checkout|search|cdn|collections/(vendors|types|all)$)", path):
                    internal_links[urllib.parse.urlunparse(pu._replace(query="", fragment="", path=path))] += 1

    for t, urls in titles.items():
        if t and len(urls) > 1:
            for u in urls:
                issue(u, "Duplicate title", "Medium", f"Shared by {len(urls)} pages: {t}")
    for d, urls in descs.items():
        if len(urls) > 1:
            for u in urls:
                issue(u, "Duplicate meta description", "Low", f"Shared by {len(urls)} pages")

    checked = set(p["url"].rstrip("/") for p in pages)
    to_check = [u for u, _ in internal_links.most_common() if u.rstrip("/") not in checked][:120]
    log(f"Checking {len(to_check)} internal links ...")
    broken = 0
    for n, u in enumerate(to_check, 1):
        progress(n, len(to_check), "Checking internal links")
        status, _, _ = f.get(u, method="HEAD")
        final = u
        if status >= 400 or status == 0:  # some servers answer HEAD differently; confirm with GET
            status, final, _ = f.get(u)
        if status >= 400 or status == 0:
            broken += 1
            via = urllib.parse.urlparse(final).path
            hop = f", redirects to {via}" if final.rstrip("/") != u.rstrip("/") else ""
            issue(u, "Broken internal link", "High", f"HTTP {status}{hop}, linked {internal_links[u]} time(s)")

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "issues.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["severity", "type", "url", "detail"])
        w.writeheader()
        order = {"High": 0, "Medium": 1, "Low": 2}
        w.writerows(sorted(({k: i[k] for k in w.fieldnames} for i in issues), key=lambda r: (order[r["severity"]], r["type"])))
    write_html(out_dir / "report.html", host, totals, pages, issues, len(to_check), f.count)
    results = {"host": host, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sitemap": totals,
               "pages_checked": len(pages), "links_checked": len(to_check), "requests": f.count,
               "pages_without_meta": sum(1 for p in pages if not p["desc"]), "pages_without_h1": sum(1 for p in pages if not p["h1"]),
               "issues": issues, "advice": ADVICE}
    (out_dir / "results.json").write_text(json.dumps(results), encoding="utf-8")
    log(f"Done: {len(pages)} pages, {len(to_check)} links, {len(issues)} issues, {f.count} requests")
    log(f"Report: {out_dir / 'report.html'}  CSV: {out_dir / 'issues.csv'}")
    return results


ADVICE = {
    "Broken internal link": "Fix or redirect (Shopify: Online Store > Navigation > URL Redirects).",
    "Page not loading": "Check the page still exists; add a redirect if it was removed.",
    "Missing meta description": "Add one in the page's 'Search engine listing' section (70-160 chars).",
    "Images missing alt attribute": "Describe each image in a few words; helps search and screen readers.",
    "Images with empty alt text": "Correct for decorative images; product and hero images need a short description.",
    "Duplicate title": "Give each page a unique title that names the product or topic.",
    "Missing H1": "Make sure each page has one main heading (usually the theme's product or page title).",
    "Multiple H1": "Keep one H1 per page; style other headings as H2.",
    "Title too long": "Keep titles under ~60-70 chars so they are not cut off in Google.",
    "Title too short": "Add the product type or location, e.g. 'Business Cards | Auckland'.",
    "Meta description too long": "Trim to ~155 chars.",
    "Meta description too short": "Expand to a full sentence with a reason to click.",
    "Duplicate meta description": "Write a specific description for each important page.",
    "Missing canonical": "Usually set by the theme; check the theme's head section.",
    "Page set to noindex": "Confirm this is intentional; otherwise the page will not appear in Google.",
    "Missing title": "Add a page title in the 'Search engine listing' section.",
}


def write_html(path, host, totals, pages, issues, links_checked, requests):
    by_type = Counter(i["type"] for i in issues)
    sev = {t: next(i["severity"] for i in issues if i["type"] == t) for t in by_type}
    order = {"High": 0, "Medium": 1, "Low": 2}
    rows = ""
    for t, n in sorted(by_type.items(), key=lambda x: (order[sev[x[0]]], -x[1])):
        ex = [i for i in issues if i["type"] == t][:3]
        examples = "".join(f"<li><a href='{html.escape(e['url'])}'>{html.escape(e['url'].split('//', 1)[-1])}</a>"
                           f"{' — ' + html.escape(e['detail']) if e['detail'] else ''}</li>" for e in ex)
        rows += (f"<tr><td><span class='sev {sev[t].lower()}'>{sev[t]}</span></td><td><b>{html.escape(t)}</b>"
                 f"<div class='muted'>{html.escape(ADVICE.get(t, ''))}</div><ul>{examples}</ul></td><td class='n'>{n}</td></tr>")
    pages_with = len({i["url"] for i in issues if i["type"] in ("Images missing alt attribute", "Images with empty alt text")})
    with_desc = sum(1 for p in pages if p["desc"])
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SEO Health Check</title><style>
:root{{--bg:#fff;--ink:#1f2933;--muted:#64748b;--line:#e2e8f0;--soft:#f5f7fa;--hi:#b91c1c;--med:#b45309;--low:#475569}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#11161c;--ink:#e5e9ee;--muted:#9aa4b0;--line:#2a323c;--soft:#1a2028;--hi:#f87171;--med:#fbbf24;--low:#94a3b8}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",Calibri,Arial,sans-serif}}
main{{max-width:980px;margin:0 auto;padding:28px 16px}} h1{{margin:0 0 4px}} .muted{{color:var(--muted);font-size:13px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}}
.kpi{{background:var(--soft);border-radius:10px;padding:12px 14px}} .kpi b{{font-size:26px;display:block}}
table{{width:100%;border-collapse:collapse}} td,th{{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
td.n{{text-align:right;font-weight:700;font-size:18px}} ul{{margin:6px 0 0;padding-left:18px;font-size:13px}} a{{color:inherit}}
.sev{{font-size:12px;font-weight:700;padding:2px 8px;border-radius:99px;border:1px solid}} .high{{color:var(--hi)}} .medium{{color:var(--med)}} .low{{color:var(--low)}}
</style></head><body><main>
<h1>SEO &amp; content health check: {html.escape(host)}</h1>
<div class="muted">{date.today():%d %B %Y} · public sitemap pages only · {len(pages)} pages sampled, {links_checked} internal links checked, {requests} polite requests</div>
<div class="kpis">
<div class="kpi"><b>{sum(totals.values())}</b><span class="muted">URLs in sitemap</span></div>
<div class="kpi"><b>{len(pages)}</b><span class="muted">pages checked</span></div>
<div class="kpi"><b>{sum(1 for i in issues if i['type']=='Broken internal link')}</b><span class="muted">broken internal links</span></div>
<div class="kpi"><b>{sum(1 for i in issues if i['type']=='Missing H1')}</b><span class="muted">pages without a main heading (H1)</span></div>
<div class="kpi"><b>{len(pages) - with_desc}</b><span class="muted">pages without meta description</span></div>
</div>
<h2>Issues by type</h2><table><tr><th>Severity</th><th>Issue and suggested fix</th><th>Count</th></tr>{rows}</table>
<p class="muted">Sitemap breakdown: {html.escape(', '.join(f'{k}: {v}' for k, v in totals.items()))}. A sample of each type was checked, so counts are indicative.
Automated checks can produce false positives; each item should be confirmed in the Shopify admin before changing anything.</p>
</main></body></html>"""
    path.write_text(doc, encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("site")
    ap.add_argument("--max-per-type", type=int, default=40)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--out", default=str(Path(__file__).parent / "data_cache" / "seo"))
    a = ap.parse_args()
    audit(a.site.rstrip("/"), a.max_per_type, a.delay, Path(a.out))
