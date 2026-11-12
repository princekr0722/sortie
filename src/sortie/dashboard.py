"""`sortie dashboard`: a local, read-only web page over everything Sortie has recorded (see doc/features/dashboard.md)."""

from __future__ import annotations

import json
import re
import sqlite3
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from sortie.config import CONFIG_DIR, PROJECT_ROOT
from sortie.db import DEFAULT_DB, Database

PAGE = Path(__file__).with_name("dashboard.html")
LOG_DIR = PROJECT_ROOT / "data" / "logs"
SCREENSHOT_DIR = PROJECT_ROOT / "data" / "screenshots"
ANSWERS = CONFIG_DIR / "answers.yaml"
PAGE_SIZE = 50
SAFE_NAME = re.compile(r"^[\w.-]+$")  # files are served by bare name from their own folder only
MAX_LOG_BYTES = 2_000_000

# latest attempt per job: a retried job has several application rows
LATEST = "a.id = (SELECT MAX(id) FROM applications WHERE job_id = a.job_id)"

# after applying, set by you in the dashboard; a job's stage is its latest event
STAGES = ["heard_back", "interview", "offer", "rejected", "withdrawn"]
CURRENT_STAGE = """(SELECT stage FROM outcome_events WHERE job_id = a.job_id ORDER BY id DESC LIMIT 1)"""
FIT_BUCKETS = [(80, "80+"), (70, "70–79"), (60, "60–69"), (0, "below 60")]


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def reason_group(reason: str | None, status: str = "skipped") -> str:
    """Collapse a stored reason into a group for the overview ("experience 6-11 Yrs outside 2-5" → "experience
    outside your range"). Scored jobs store "fit N: <reason>"; a skipped one whose reason is the scorer's own
    explanation was skipped for its low score."""
    text = (reason or "").strip()
    scored = re.match(r"fit \d+: (.*)", text, re.S)
    if scored:
        text = scored.group(1).strip()
    if text in ("already applied", "applies on company site"):
        return text
    for pattern, group in [(r"title screen", "title doesn't fit (AI screen)"),
                           (r"excluded keyword", "excluded keyword in title"),
                           (r"location .* not wanted", "location not wanted"),
                           (r"experience .* outside", "experience outside your range"),
                           (r"salary .* below", "salary below expected"),
                           (r"excluded company", "excluded company")]:
        if re.match(pattern, text, re.I):
            return group
    if scored and status == "skipped":
        return "low fit score"
    if not text:
        return "(no reason)"
    head = text.split(":", 1)[0] if ":" in text[:50] else text
    return re.sub(r"\d+", "N", head)[:60]


def overview(conn: sqlite3.Connection, caps: dict[str, int], today: date | None = None) -> dict:
    today = today or date.today()
    day = today.isoformat()
    sites: dict[str, dict] = {}

    def site(name: str) -> dict:
        return sites.setdefault(name, {"site": name, "cap": caps.get(name, 20), "today": {}, "all": {}})

    for name in caps:
        site(name)
    for r in conn.execute("""SELECT j.site, a.status, COUNT(*) n FROM applications a JOIN jobs j ON j.id = a.job_id
                             WHERE substr(a.created_at, 1, 10) = ? GROUP BY 1, 2""", (day,)):
        site(r["site"])["today"][r["status"]] = r["n"]
    for r in conn.execute(f"""SELECT j.site, a.status, COUNT(*) n FROM applications a JOIN jobs j ON j.id = a.job_id
                              WHERE {LATEST} GROUP BY 1, 2"""):
        site(r["site"])["all"][r["status"]] = r["n"]

    start = (today - timedelta(days=29)).isoformat()
    daily = [dict(r) for r in conn.execute(
        """SELECT substr(a.created_at, 1, 10) day, j.site, COUNT(*) n FROM applications a JOIN jobs j ON j.id = a.job_id
           WHERE a.status = 'applied' AND substr(a.created_at, 1, 10) >= ? GROUP BY 1, 2 ORDER BY 1""", (start,))]

    reasons: dict[str, dict[str, int]] = {"skipped": {}, "failed": {}}
    for r in conn.execute(f"""SELECT a.status, a.reason FROM applications a
                              WHERE {LATEST} AND a.status IN ('skipped', 'failed')"""):
        group = reason_group(r["reason"], r["status"])
        reasons[r["status"]][group] = reasons[r["status"]].get(group, 0) + 1
    top = {k: sorted(v.items(), key=lambda kv: -kv[1])[:10] for k, v in reasons.items()}

    fit = conn.execute("SELECT AVG(fit_score) avg, COUNT(fit_score) n FROM applications "
                       "WHERE status = 'applied' AND fit_score IS NOT NULL").fetchone()
    totals: dict[str, int] = {}
    for s in sites.values():
        for status, n in s["all"].items():
            totals[status] = totals.get(status, 0) + n
    first = conn.execute("SELECT MIN(created_at) FROM applications").fetchone()[0]
    return {"outcomes": outcomes_summary(conn),
        "today": day, "since": (first or "")[:10], "sites": list(sites.values()), "totals": totals,
        "daily": daily, "reasons": top,
        "fit": {"avg": round(fit["avg"], 1) if fit["avg"] is not None else None, "n": fit["n"]},
        "needs_you": conn.execute("SELECT COUNT(*) FROM needs_you").fetchone()[0],
        "followups_open": conn.execute("SELECT COUNT(*) FROM followups WHERE status = 'open'").fetchone()[0],
    }


def fit_bucket(score: int | None) -> str:
    if score is None:
        return "unscored"
    return next(label for floor, label in FIT_BUCKETS if score >= floor)


def outcomes_summary(conn: sqlite3.Connection) -> dict:
    """Funnel over applied jobs, and reply / interview rates by site and by fit score."""
    rows = conn.execute(f"""
        SELECT j.site, a.fit_score, {CURRENT_STAGE} stage,
               EXISTS(SELECT 1 FROM outcome_events e WHERE e.job_id = a.job_id
                      AND e.stage IN ('interview', 'offer')) interviewed,
               EXISTS(SELECT 1 FROM outcome_events e WHERE e.job_id = a.job_id AND e.stage = 'offer') offered
        FROM applications a JOIN jobs j ON j.id = a.job_id WHERE {LATEST} AND a.status = 'applied'""").fetchall()

    def rates(group) -> dict:
        n = len(group)
        replied = sum(1 for r in group if r["stage"] and r["stage"] != "withdrawn")
        interviewed = sum(r["interviewed"] for r in group)
        return {"applied": n, "replied": replied, "interviewed": interviewed,
                "reply_rate": round(replied / n * 100, 1) if n else None,
                "interview_rate": round(interviewed / n * 100, 1) if n else None}

    by_site: dict[str, list] = {}
    by_fit: dict[str, list] = {label: [] for _, label in FIT_BUCKETS} | {"unscored": []}
    for r in rows:
        by_site.setdefault(r["site"], []).append(r)
        by_fit[fit_bucket(r["fit_score"])].append(r)
    stages = {s: sum(1 for r in rows if r["stage"] == s) for s in STAGES}
    return {
        "funnel": {"applied": len(rows), "replied": rates(rows)["replied"],
                   "interviewed": sum(r["interviewed"] for r in rows), "offers": sum(r["offered"] for r in rows),
                   "rejected": stages["rejected"]},
        "stages": stages,
        "by_site": [{"key": k, **rates(v)} for k, v in by_site.items()],
        "by_fit": [{"key": k, **rates(v)} for k, v in by_fit.items() if v],
    }


def applications(conn: sqlite3.Connection, site: str = "", status: str = "", q: str = "",
                 since: str = "", until: str = "", offset: int = 0, limit: int = PAGE_SIZE,
                 stage: str = "") -> dict:
    where, args = [LATEST], []
    if stage == "any":
        where.append(f"{CURRENT_STAGE} IS NOT NULL")
    elif stage == "none":
        where.append(f"a.status = 'applied' AND {CURRENT_STAGE} IS NULL")
    elif stage in STAGES:
        where.append(f"{CURRENT_STAGE} = ?"); args.append(stage)
    if site:
        where.append("j.site = ?"); args.append(site)
    if status:
        where.append("a.status = ?"); args.append(status)
    if since:
        where.append("substr(a.created_at, 1, 10) >= ?"); args.append(since)
    if until:
        where.append("substr(a.created_at, 1, 10) <= ?"); args.append(until)
    if q:
        where.append("(j.title LIKE ? OR j.company LIKE ? OR a.reason LIKE ? OR j.location LIKE ?)")
        args += [f"%{q}%"] * 4
    sql = f"FROM applications a JOIN jobs j ON j.id = a.job_id WHERE {' AND '.join(where)}"
    total = conn.execute(f"SELECT COUNT(*) {sql}", args).fetchone()[0]
    rows = conn.execute(
        f"""SELECT a.id, j.site, j.title, j.company, j.location, a.status, a.reason, a.fit_score, a.created_at, j.url,
                   {CURRENT_STAGE} stage,
                   a.answers_json IS NOT NULL AND a.answers_json NOT IN ('', '{{}}') has_answers
            {sql} ORDER BY a.id DESC LIMIT ? OFFSET ?""", [*args, limit, offset]).fetchall()
    return {"total": total, "offset": offset, "limit": limit, "rows": [dict(r) for r in rows]}


def application(conn: sqlite3.Connection, app_id: int) -> dict | None:
    r = conn.execute("""SELECT a.*, j.site, j.site_job_id, j.url, j.title, j.company, j.location, j.experience,
                               j.salary, j.jd, j.first_seen_at
                        FROM applications a JOIN jobs j ON j.id = a.job_id WHERE a.id = ?""", (app_id,)).fetchone()
    if r is None:
        return None
    out = dict(r)
    try:
        out["answers"] = json.loads(out.pop("answers_json") or "{}")
    except json.JSONDecodeError:
        out["answers"] = {}
    shot = out.pop("screenshot_path") or ""
    out["screenshot"] = Path(shot).name if shot and (SCREENSHOT_DIR / Path(shot).name).exists() else ""
    out["attempts"] = [dict(a) for a in conn.execute(
        "SELECT id, status, reason, fit_score, created_at FROM applications WHERE job_id = ? ORDER BY id",
        (r["job_id"],))]
    out["outcomes"] = [dict(e) for e in conn.execute(
        "SELECT stage, note, created_at FROM outcome_events WHERE job_id = ? ORDER BY id", (r["job_id"],))]
    out["stage"] = out["outcomes"][-1]["stage"] if out["outcomes"] else None
    out["followup"] = next((dict(f) for f in conn.execute(
        "SELECT status, note, updated_at FROM followups WHERE site = ? AND site_job_id = ?",
        (r["site"], r["site_job_id"]))), None)
    return out


def set_outcome(db_path: Path, app_id: int, stage: str, note: str = "") -> dict:
    """Record a stage for the application's job; "none" clears its outcome (undo a mis-click). The dashboard's
    only write."""
    if stage != "none" and stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    conn = sqlite3.connect(db_path, timeout=10)  # a running `sortie run` may hold the write lock briefly
    try:
        row = conn.execute("SELECT job_id FROM applications WHERE id = ?", (app_id,)).fetchone()
        if row is None:
            raise LookupError("no such application")
        if stage == "none":
            conn.execute("DELETE FROM outcome_events WHERE job_id = ?", (row[0],))
        else:
            conn.execute("INSERT INTO outcome_events (job_id, stage, note, created_at) VALUES (?, ?, ?, ?)",
                         (row[0], stage, note.strip()[:2000], datetime.now().isoformat(timespec="seconds")))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def needs_you(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM needs_you ORDER BY created_at DESC")]


def followups(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM followups ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, updated_at DESC")]


def saved_answers(path: Path = ANSWERS) -> list[dict]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [{"question": e.get("question", ""), "answer": e.get("answer"), "options": e.get("options") or [],
             "answered": str(e.get("answered", ""))} for e in data if isinstance(e, dict)]


def runs(log_dir: Path = LOG_DIR) -> list[dict]:
    files = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True) if log_dir.exists() else []
    out = []
    for p in files:
        stat = p.stat()
        out.append({"name": p.name, "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")})
    return out


def run_log(name: str, log_dir: Path = LOG_DIR) -> str | None:
    path = log_dir / name
    if not SAFE_NAME.match(name) or not path.is_file():
        return None
    return path.read_bytes()[-MAX_LOG_BYTES:].decode("utf-8", errors="replace")  # the end of a huge log


class Handler(BaseHTTPRequestHandler):
    db_path: Path = DEFAULT_DB
    caps: dict[str, int] = {}
    port: int = 0

    def log_message(self, *args) -> None:  # keep the terminal quiet
        pass

    def _local_host(self) -> bool:
        # DNS rebinding: another site's page could point its own hostname at 127.0.0.1 and read the data
        return (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_POST(self) -> None:
        # Cross-site writes: another site can't send a JSON content type without a CORS preflight (never answered
        # here), and a browser always reports its Origin, which must be this dashboard
        origin = self.headers.get("Origin")
        own = {f"http://127.0.0.1:{self.server.server_address[1]}", f"http://localhost:{self.server.server_address[1]}"}
        if (not self._local_host() or (origin and origin not in own)
                or not (self.headers.get("Content-Type") or "").startswith("application/json")):
            return self._send(403, b"forbidden", "text/plain")
        m = re.fullmatch(r"/api/application/(\d+)/outcome", urlparse(self.path).path)
        if not m:
            return self._send(404, b"not found", "text/plain")
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 20_000)) or b"{}")
            return self._json(set_outcome(self.db_path, int(m.group(1)), str(body.get("stage", "")),
                                          str(body.get("note") or "")))
        except (ValueError, LookupError) as e:
            return self._json({"error": str(e)}, 400)

    def do_GET(self) -> None:
        if not self._local_host():
            return self._send(403, b"forbidden", "text/plain")
        url = urlparse(self.path)
        path, query = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if path in ("/", "/index.html"):
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path.startswith("/screenshots/"):
                return self._file(SCREENSHOT_DIR, unquote(path.removeprefix("/screenshots/")))
            if path.startswith("/api/"):
                return self._api(path.removeprefix("/api/"), query)
        except Exception as e:  # a bad row must not take the page down
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        self._send(404, b"not found", "text/plain")

    def _api(self, route: str, query: dict) -> None:
        if route == "answers":
            return self._json(saved_answers())
        if route == "runs":
            return self._json(runs())
        if route.startswith("runs/"):
            text = run_log(unquote(route.removeprefix("runs/")))
            return self._json({"text": text}) if text is not None else self._json({"error": "no such log"}, 404)
        conn = connect(self.db_path)
        try:
            if route == "overview":
                return self._json(overview(conn, self.caps))
            if route == "applications":
                return self._json(applications(
                    conn, query.get("site", ""), query.get("status", ""), query.get("q", "").strip(),
                    query.get("from", ""), query.get("to", ""), int(query.get("offset", 0) or 0),
                    stage=query.get("stage", "")))
            if m := re.fullmatch(r"application/(\d+)", route):
                found = application(conn, int(m.group(1)))
                return self._json(found) if found else self._json({"error": "no such application"}, 404)
            if route == "needs-you":
                return self._json(needs_you(conn))
            if route == "followups":
                return self._json(followups(conn))
        finally:
            conn.close()
        self._json({"error": "unknown endpoint"}, 404)

    def _file(self, folder: Path, name: str) -> None:
        path = folder / name
        if not SAFE_NAME.match(name) or not path.is_file():
            return self._send(404, b"not found", "text/plain")
        kind = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(path.suffix.lower(),
                                                                                       "application/octet-stream")
        self._send(200, path.read_bytes(), kind)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, default=str).encode(), "application/json")

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve(caps: dict[str, int], port: int = 8765, open_browser: bool = True, db_path: Path = DEFAULT_DB) -> None:
    if not db_path.exists():
        raise SystemExit(f"No database yet at {db_path}: run `sortie run` first.")
    Database(db_path).close()  # adds tables newer than the database (outcome_events)
    Handler.db_path, Handler.caps = db_path, caps
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Sortie dashboard: {url}  (local only, read-only; Ctrl+C to stop)")
    if open_browser:
        webbrowser.open_new_tab(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
