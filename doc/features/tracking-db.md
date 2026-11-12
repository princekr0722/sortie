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

## Retrying failures
- A failed job is retried once (`MAX_ATTEMPTS = 2`), then skipped silently.
- `sortie retry <site>` deletes the failed attempts of jobs that never had another outcome, so the next run tries them again. Use it after fixing a site adapter. Jobs that were applied to or skipped are untouched.

## Follow-ups (2026-11-11)
Sites where recruiters follow up an application (Instahyre and CutShort questionnaires) must never miss one, however many applications a run sends. Inbox paging alone depended on how many conversations arrived. So every application Sortie sends is tracked until its follow-up is settled.

- Table `followups(site, site_job_id, title, company, conversation_id, status, note, created_at, updated_at)`, unique per site + job.
  - `status`: `open`, `answered` (questionnaire sent, or already submitted), `needs_you` (left for you; also on the needs-you list) or `none` (no questionnaire within 14 days).
- **Opened:** when a job on a follow-up site (`SiteAdapter.FOLLOWUPS`) is recorded `applied`. `ApplyResult.conversation_id` is stored when known (CutShort: the `thread=` of the card's "View conversation" link).
- **Backfill:** before each follow-up pass, applied jobs from the last 14 days with no row get one. This covers applications made before tracking existed, or by a run that stopped early.
- **Checked:** every run, the adapter gets all `open` follow-ups (`adapter.followups`), checks each one directly, and reports `adapter.followup_updates`, which the runner saves.
- **Expired:** open follow-ups older than 14 days → `none`.
- The general inbox scan still runs for conversations Sortie didn't start, and skips conversations a follow-up already handled. It covers **every** conversation from the last 14 days (no count cap since 2026-11-11, only a 200-page safety stop).

