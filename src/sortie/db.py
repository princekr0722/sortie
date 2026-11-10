"""SQLite tracking of seen jobs and application outcomes."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from sortie.config import PROJECT_ROOT
from sortie.models import Job, NeedsYou, Status

DEFAULT_DB = PROJECT_ROOT / "data" / "applier.db"
MAX_ATTEMPTS = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    site TEXT NOT NULL,
    site_job_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    experience TEXT,
    salary TEXT,
    jd TEXT,
    first_seen_at TEXT NOT NULL,
    UNIQUE (site, site_job_id)
);
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    status TEXT NOT NULL,
    reason TEXT,
    fit_score INTEGER,
    answers_json TEXT,
    screenshot_path TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS needs_you (
    id INTEGER PRIMARY KEY,
    site TEXT NOT NULL,
    what TEXT NOT NULL,
    title TEXT,
    company TEXT,
    url TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (site, url, what)
);
"""
NEEDS_YOU_DAYS = 14  # older items drop off the report


class Database:
    def __init__(self, path: Path = DEFAULT_DB):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def is_seen(self, site: str, site_job_id: str) -> bool:
        """Seen = any outcome other than failed, or failed MAX_ATTEMPTS times (failures get retried)."""
        row = self.conn.execute(
            """SELECT COUNT(*) AS total, SUM(a.status = ?) AS failed FROM applications a
               JOIN jobs j ON j.id = a.job_id WHERE j.site = ? AND j.site_job_id = ?""",
            (Status.FAILED.value, site, site_job_id),
        ).fetchone()
        if not row["total"]:
            return False
        return row["failed"] < row["total"] or row["failed"] >= MAX_ATTEMPTS

    def add_needs_you(self, item: NeedsYou) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO needs_you (site, what, title, company, url, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""", (item.site, item.what, item.title, item.company, item.url, _now()))
        self.conn.commit()

    def needs_you(self, days: int = NEEDS_YOU_DAYS) -> list[NeedsYou]:
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self.conn.execute("SELECT * FROM needs_you WHERE created_at >= ? ORDER BY created_at DESC", (since,))
        return [NeedsYou(r["site"], r["what"], r["title"], r["company"], r["url"]) for r in rows]

    def reset_failures(self, site: str) -> int:
        """Forget the failed attempts of jobs that never got any other outcome, so the next run retries them
        (e.g. after a site fix). Returns how many jobs were reset."""
        jobs = [r["job_id"] for r in self.conn.execute(
            """SELECT a.job_id FROM applications a JOIN jobs j ON j.id = a.job_id WHERE j.site = ?
               GROUP BY a.job_id HAVING SUM(a.status != ?) = 0""", (site, Status.FAILED.value))]
        self.conn.executemany("DELETE FROM applications WHERE job_id = ? AND status = ?",
                              [(job_id, Status.FAILED.value) for job_id in jobs])
        self.conn.commit()
        return len(jobs)

    def upsert_job(self, job: Job) -> int:
        self.conn.execute(
            """INSERT INTO jobs (site, site_job_id, url, title, company, location, experience, salary, jd, first_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (site, site_job_id) DO UPDATE SET jd = COALESCE(NULLIF(excluded.jd, ''), jobs.jd)""",
            (job.site, job.site_job_id, job.url, job.title, job.company, ", ".join(job.locations),
             job.experience, job.salary, job.description, _now()),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM jobs WHERE site = ? AND site_job_id = ?", (job.site, job.site_job_id)
        ).fetchone()
        return row["id"]

    def record(self, job: Job, status: Status, reason: str = "", fit_score: int | None = None,
               answers: dict | None = None, screenshot_path: str | None = None) -> None:
        job_id = self.upsert_job(job)
        self.conn.execute(
            """INSERT INTO applications (job_id, status, reason, fit_score, answers_json, screenshot_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job_id, status.value, reason, fit_score, json.dumps(answers) if answers else None,
             screenshot_path, _now()),
        )
        self.conn.commit()

    def applied_count(self, site: str, on: date | None = None) -> int:
        day = (on or date.today()).isoformat()
        row = self.conn.execute(
            """SELECT COUNT(*) AS n FROM applications a JOIN jobs j ON j.id = a.job_id
               WHERE j.site = ? AND a.status = ? AND substr(a.created_at, 1, 10) = ?""",
            (site, Status.APPLIED.value, day),
        ).fetchone()
        return row["n"]

    def status_counts(self, on: date | None = None) -> list[tuple[str, str, int]]:
        day = (on or date.today()).isoformat()
        rows = self.conn.execute(
            """SELECT j.site, a.status, COUNT(*) AS n FROM applications a JOIN jobs j ON j.id = a.job_id
               WHERE substr(a.created_at, 1, 10) = ? GROUP BY j.site, a.status ORDER BY j.site, a.status""",
            (day,),
        ).fetchall()
        return [(r["site"], r["status"], r["n"]) for r in rows]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
