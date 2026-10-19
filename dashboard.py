"""Browser dashboard: python dashboard.py, then open http://localhost:8001

Left: The Print Company's Shopify catalogue (real public data). Right: an ops toolkit built on it:
data-quality checks with suggested fixes, AI brain files for Claude, a quote helper and a change log.
"""

import copy
import csv
import io
import json
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import brain
import catalog
import quality
import seo_audit
import storefront

BASE_URL = "https://theprintcompany.co.nz"
HERE = Path(__file__).parent
DB = HERE / "data_cache" / "catalogue.db"
PAGE_HTML = (HERE / "dashboard.html").read_bytes()  # read once: page always matches this server's code
_lock = threading.Lock()
SEO_DIR = HERE / "data_cache" / "seo"
_seo = {"running": False, "done": 0, "total": 0, "stage": "", "error": None}


def _seo_job():
    def progress(done, total, stage):
        _seo.update(done=done, total=total, stage=stage)
    try:
        seo_audit.audit(BASE_URL, 30, 1.0, SEO_DIR, log=lambda m: None, progress=progress)
        _seo["error"] = None
    except Exception as e:  # shown on the page
        _seo["error"] = str(e)
    finally:
        _seo["running"] = False


def start_seo():
    if _seo["running"]:
        return False
    _seo.update(running=True, done=0, total=0, stage="Starting", error=None)
    threading.Thread(target=_seo_job, daemon=True).start()
    return True


def seo_state():
    results = None
    f = SEO_DIR / "results.json"
    if f.exists():
        r = json.loads(f.read_text(encoding="utf-8"))
        order = {"High": 0, "Medium": 1, "Low": 2}
        groups = {}
        for i in r["issues"]:
            g = groups.setdefault(i["type"], {"type": i["type"], "severity": i["severity"],
                                              "advice": r["advice"].get(i["type"], ""), "items": []})
            g["items"].append({"url": i["url"], "detail": i["detail"]})
        results = {k: r[k] for k in ("host", "finished_at", "pages_checked", "links_checked", "requests",
                                     "pages_without_meta", "pages_without_h1")}
        results["broken_links"] = len(groups.get("Broken internal link", {}).get("items", []))
        results["groups"] = sorted(groups.values(), key=lambda g: (order[g["severity"]], -len(g["items"])))
    return {**_seo, "results": results}


def db():
    DB.parent.mkdir(exist_ok=True)
    return catalog.connect(DB)


def _after_sync(conn, log):
    quality.run(conn, log=log)
    run = conn.execute("SELECT fetched_at FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
    brain.export(conn, BASE_URL, run["fetched_at"] if run else "", log=log)


def act_fetch_live(conn, log, _):
    log(f"Fetching the public catalogue from {BASE_URL} (1 request per second) ...")
    snap = storefront.fetch_all(BASE_URL, log=log)
    storefront.save_snapshot(snap)
    log(f"Saved offline snapshot ({len(snap['products'])} products).")
    catalog.sync(conn, snap, "live", log=log)
    _after_sync(conn, log)


def act_load_snapshot(conn, log, _):
    snap = storefront.load_snapshot()
    if snap is None:
        raise RuntimeError("No offline snapshot yet: click 'Fetch live data' once while online")
    log(f"Using offline snapshot fetched {snap['fetched_at']}.")
    catalog.sync(conn, snap, "snapshot", log=log)
    _after_sync(conn, log)


def act_simulate(conn, log, _):
    """Apply made-up edits to a local copy, to show change detection. The website is not touched."""
    snap = storefront.load_snapshot()
    if snap is None:
        raise RuntimeError("Fetch live data first")
    sim = copy.deepcopy(snap)
    ps = sorted(sim["products"], key=lambda p: -len(p["variants"]))
    v = ps[0]["variants"][0]
    v["price"] = f"{float(v['price']) * 1.08:.2f}"
    ps[1]["variants"][0]["available"] = not ps[1]["variants"][0]["available"]
    new = copy.deepcopy(ps[2])
    new["id"], new["handle"], new["title"] = 1, "demo-new-product", "DEMO – New product (simulated)"
    for i, nv in enumerate(new["variants"]):
        nv["id"] = 10 + i
    for i, img in enumerate(new.get("images", [])):
        img["id"] = 100 + i
    sim["products"].append(new)
    sim["fetched_at"] = snap["fetched_at"] + " + simulated edits"
    log("Simulated website edits on a local copy: one price +8%, one variant availability flipped, one new product.")
    catalog.sync(conn, sim, "simulated", log=log)
    _after_sync(conn, log)


def act_quote(conn, log, args):
    return brain.quote(conn, args["handle"], args.get("options") or [], args.get("customer"), args.get("sender"), BASE_URL)


ACTIONS = {"fetch_live": act_fetch_live, "load_snapshot": act_load_snapshot, "simulate": act_simulate}


def state():
    conn = db()
    try:
        products = []
        for p in conn.execute("""
                SELECT p.id, p.handle, p.title, p.product_type, p.tags,
                       COUNT(DISTINCT v.id) AS variants, MIN(v.price) AS min_price, MAX(v.price) AS max_price,
                       SUM(CASE WHEN v.sku='' THEN 1 ELSE 0 END) AS no_sku, MAX(v.available) AS available,
                       (SELECT COUNT(*) FROM images i WHERE i.product_id=p.id) AS images,
                       (SELECT COUNT(*) FROM images i WHERE i.product_id=p.id AND (i.alt IS NULL OR trim(i.alt)='')) AS no_alt
                FROM products p LEFT JOIN variants v ON v.product_id=p.id GROUP BY p.id ORDER BY p.title"""):
            products.append(dict(p))
        one = lambda sql: conn.execute(sql).fetchone()[0]
        kpi = {
            "products": len(products), "variants": one("SELECT COUNT(*) FROM variants"),
            "no_sku": one("SELECT COUNT(*) FROM variants WHERE sku=''"),
            "images": one("SELECT COUNT(*) FROM images"),
            "no_alt": one("SELECT COUNT(*) FROM images WHERE alt IS NULL OR trim(alt)=''"),
            "no_type": one("SELECT COUNT(*) FROM products WHERE product_type=''"),
        }
        rules = [dict(r) for r in conn.execute("""SELECT rule, severity, COUNT(*) AS n FROM issues GROUP BY rule, severity
                    ORDER BY CASE severity WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, n DESC""")]
        issues = [dict(r) for r in conn.execute("""SELECT i.rule, i.severity, i.target, i.current, i.suggestion,
                    p.title, p.handle FROM issues i LEFT JOIN products p ON p.id=i.product_id
                    ORDER BY CASE i.severity WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, i.rule, p.title""")]
        brain_files = []
        for name in ("products.md", "pricing.json", "SKILL.md"):
            f = brain.OUT / name
            if f.exists():
                brain_files.append({"name": name, "kb": round(f.stat().st_size / 1024, 1)})
        preview = ""
        if (brain.OUT / "products.md").exists():
            preview = "\n".join((brain.OUT / "products.md").read_text(encoding="utf-8").splitlines()[:40])
        skill = (brain.OUT / "SKILL.md").read_text(encoding="utf-8") if (brain.OUT / "SKILL.md").exists() else ""
        return {
            "base_url": BASE_URL, "products": products, "kpi": kpi, "rules": rules, "issues": issues,
            "changes": [dict(r) for r in conn.execute("SELECT * FROM changes ORDER BY id DESC LIMIT 60")],
            "runs": [dict(r) for r in conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 10")],
            "brain_files": brain_files, "brain_preview": preview, "skill": skill,
            "snapshot": bool(storefront.load_snapshot()),
        }
    finally:
        conn.close()


def product_detail(handle):
    conn = db()
    try:
        p = conn.execute("SELECT * FROM products WHERE handle=?", (handle,)).fetchone()
        if p is None:
            return None
        vs = [dict(v) for v in conn.execute("SELECT * FROM variants WHERE product_id=? ORDER BY position", (p["id"],))]
        imgs = [dict(i) for i in conn.execute("SELECT id, alt, position FROM images WHERE product_id=? ORDER BY position", (p["id"],))]
        return {"product": {k: p[k] for k in ("handle", "title", "product_type", "tags", "description")},
                "options": json.loads(p["options"] or "[]"), "variants": vs, "images": imgs}
    finally:
        conn.close()


def fixes_csv():
    conn = db()
    try:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["severity", "rule", "product", "handle", "target", "current", "suggested"])
        for r in conn.execute("""SELECT i.*, p.title, p.handle FROM issues i LEFT JOIN products p ON p.id=i.product_id
                                 ORDER BY CASE i.severity WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, i.rule"""):
            w.writerow([r["severity"], r["rule"], r["title"], r["handle"], r["target"], r["current"], r["suggestion"]])
        for r in quality.variant_fix_rows(conn):
            w.writerow(["Detail", r["field"], r["product"], r["handle"], r["variant_or_image"], r["current"], r["suggested"]])
        return ("﻿" + buf.getvalue()).encode("utf-8")
    finally:
        conn.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", filename=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, PAGE_HTML, "text/html; charset=utf-8")
        if u.path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        with _lock:
            if u.path == "/api/state":
                return self._send(200, state())
            if u.path == "/api/seo":
                return self._send(200, seo_state())
            if u.path == "/download/seo_report.html" and (SEO_DIR / "report.html").exists():
                return self._send(200, (SEO_DIR / "report.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/download/seo_issues.csv" and (SEO_DIR / "issues.csv").exists():
                return self._send(200, (SEO_DIR / "issues.csv").read_bytes(), "text/csv; charset=utf-8", "tpc_seo_issues.csv")
            if u.path == "/api/product":
                d = product_detail(parse_qs(u.query).get("handle", [""])[0])
                return self._send(200 if d else 404, d or {"error": "not found"})
            if u.path == "/download/fixes.csv":
                return self._send(200, fixes_csv(), "text/csv; charset=utf-8", "tpc_catalogue_fixes.csv")
            if u.path.startswith("/download/brain/"):
                name = u.path.rsplit("/", 1)[1]
                f = brain.OUT / name
                if name in ("products.md", "pricing.json", "SKILL.md") and f.exists():
                    return self._send(200, f.read_bytes(), "text/plain; charset=utf-8", name)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        name = self.path.removeprefix("/api/action/")
        args = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        lines = []
        log = lambda m: lines.append(str(m))
        with _lock:
            conn = db()
            try:
                if name == "seo_run":
                    started = start_seo()
                    return self._send(200, {"ok": True, "log": ["SEO check started: about 4 minutes at 1 request per second."
                                                                 if started else "SEO check is already running."]})
                if name == "quote":
                    return self._send(200, act_quote(conn, log, args))
                if name not in ACTIONS:
                    return self._send(404, {"error": "unknown action"})
                ACTIONS[name](conn, log, args)
                ok = True
            except Exception as e:
                lines.append(f"ERROR: {e}")
                ok = False
            finally:
                conn.close()
        self._send(200, {"ok": ok, "log": lines})


class ExclusiveServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main(port=8001):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        server = ExclusiveServer(("127.0.0.1", port), Handler)
    except OSError:
        sys.exit(f"Port {port} is already in use: another dashboard is still running. Close it (Ctrl+C) and retry.")
    conn = db()
    if not conn.execute("SELECT 1 FROM products").fetchone() and storefront.load_snapshot():
        catalog.sync(conn, storefront.load_snapshot(), "snapshot", log=lambda m: None)
        _after_sync(conn, lambda m: None)
    conn.close()
    url = f"http://localhost:{port}"
    print(f"Dashboard running at {url}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
