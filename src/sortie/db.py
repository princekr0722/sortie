"""SQLite tracking of seen jobs and application outcomes."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from sortie.config import PROJECT_ROOT
from sortie.models import Followup, Job, NeedsYou, Status

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
CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY,
    site TEXT NOT NULL,
    site_job_id TEXT NOT NULL,
    title TEXT,
    company TEXT,
    conversation_id TEXT,
    status TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (site, site_job_id)
);
CREATE TABLE IF NOT EXISTS done_questionnaires (
    site TEXT NOT NULL,
    questionnaire_id TEXT NOT NULL,
    outcome TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (site, questionnaire_id)
);
"""
FOLLOWUP_DAYS = 14  # open follow-ups older than this close as "none"; backfill reaches this far back
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

    # ---- follow-ups (see doc/features/tracking-db.md) ----

    def open_followup(self, site: str, site_job_id: str, title: str = "", company: str = "",
                      conversation_id: str = "") -> None:
        now = _now()
        self.conn.execute(
            """INSERT OR IGNORE INTO followups (site, site_job_id, title, company, conversation_id, status,
               created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)""",
            (site, site_job_id, title, company, conversation_id, now, now))
        if conversation_id:
            self.conn.execute("""UPDATE followups SET conversation_id = ?, updated_at = ? WHERE site = ?
                                 AND site_job_id = ? AND (conversation_id IS NULL OR conversation_id = '')""",
                              (conversation_id, now, site, site_job_id))
        self.conn.commit()

    def backfill_followups(self, site: str, days: int = FOLLOWUP_DAYS) -> int:
        """Open follow-ups for jobs applied to in the last `days` that have none (older runs, interrupted runs)."""
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self.conn.execute(
            """SELECT j.site_job_id, j.title, j.company, MIN(a.created_at) AS applied_at FROM applications a
               JOIN jobs j ON j.id = a.job_id WHERE j.site = ? AND a.status = ? AND a.created_at >= ?
               AND NOT EXISTS (SELECT 1 FROM followups f WHERE f.site = j.site AND f.site_job_id = j.site_job_id)
               GROUP BY j.id""", (site, Status.APPLIED.value, since)).fetchall()
        for r in rows:
            self.conn.execute(
                """INSERT OR IGNORE INTO followups (site, site_job_id, title, company, conversation_id, status,
                   created_at, updated_at) VALUES (?, ?, ?, ?, '', 'open', ?, ?)""",
                (site, r["site_job_id"], r["title"], r["company"], r["applied_at"], _now()))
        self.conn.commit()
        return len(rows)

    def expire_followups(self, site: str, days: int = FOLLOWUP_DAYS) -> int:
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        cur = self.conn.execute(
            """UPDATE followups SET status = 'none', note = 'no questionnaire within the follow-up window',
               updated_at = ? WHERE site = ? AND status = 'open' AND created_at < ?""", (_now(), site, since))
        self.conn.commit()
        return cur.rowcount

    def open_followups(self, site: str) -> list[Followup]:
        rows = self.conn.execute("SELECT * FROM followups WHERE site = ? AND status = 'open' ORDER BY created_at",
                                 (site,))
        return [Followup(r["site"], r["site_job_id"], r["title"] or "", r["company"] or "",
                         r["conversation_id"] or "", r["created_at"]) for r in rows]

    def update_followup(self, site: str, site_job_id: str, status: str, note: str = "",
                        conversation_id: str = "") -> None:
        self.conn.execute(
            """UPDATE followups SET status = ?, note = ?, updated_at = ?,
               conversation_id = CASE WHEN ? != '' THEN ? ELSE conversation_id END
               WHERE site = ? AND site_job_id = ?""",
            (status, note, _now(), conversation_id, conversation_id, site, site_job_id))
        self.conn.commit()

    def done_questionnaires(self, site: str) -> set[str]:
        """Questionnaires already sent, found submitted, or closed: never opened again."""
        return {r[0] for r in self.conn.execute(
            "SELECT questionnaire_id FROM done_questionnaires WHERE site = ?", (site,))}

    def mark_questionnaire_done(self, site: str, questionnaire_id: str, outcome: str) -> None:
        self.conn.execute("""INSERT OR IGNORE INTO done_questionnaires (site, questionnaire_id, outcome, created_at)
                             VALUES (?, ?, ?, ?)""", (site, questionnaire_id, outcome, _now()))
        self.conn.commit()

    def followup_counts(self, site: str) -> dict[str, int]:
        return {r[0]: r[1] for r in self.conn.execute(
            "SELECT status, COUNT(*) FROM followups WHERE site = ? GROUP BY status", (site,))}

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
