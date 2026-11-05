# Feature: Application Tracking (SQLite)

**Status:** Done · **Phase:** 1 · **Code:** `src/sortie/db.py`, `data/applier.db`

## Purpose
Never apply twice, keep a log of every decision, and feed reports.

## Tables
- **jobs** — `site`, `site_job_id` (unique together), `url`, `title`, `company`, `location`, `experience`, `salary`, `jd`, `first_seen_at`.
- **applications** — `job_id`, `status` (`applied` / `skipped` / `failed` / `external` / `pending_review`), `reason`, `fit_score`, `answers_json`, `screenshot_path`, `created_at`.
- (Your answers to screening questions moved to `config/answers.yaml` on 2026-11-05; the old `answer_cache` table is no longer used.)

## Uses
- Dedupe before any work: a job is skipped if it has any outcome other than `failed`. Failed jobs are retried, up to 2 attempts in total.
- Daily cap check (count of `applied` today per site).
- `external` rows form the phase 3 queue.
- `sortie report` reads from here.
