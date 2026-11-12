# Feature: Dashboard

**Status:** ✅ Done (2026-11-12) · **Phase:** 2 · **Code:** `src/sortie/dashboard.py`, `src/sortie/dashboard.html`

A local web page showing everything Sortie has recorded: applications, why jobs were skipped or failed, the questions it answered, what needs you, recruiter follow-ups, your saved answers and the run logs.

## Running it
```bash
.venv/bin/sortie dashboard               # opens http://127.0.0.1:8765 in your browser
.venv/bin/sortie dashboard --port 9000 --no-browser
```
It can run while `sortie run` is going; refresh the page (or wait for the auto-refresh) to see new rows.

## Design decisions
- **Local, and read-only apart from outcomes.** It binds to 127.0.0.1 only and reads the database read-only (`mode=ro`). The one write is the outcome you set on an application (below). It never submits anything. Requests whose `Host` header isn't `127.0.0.1`/`localhost` are refused, so another website can't read it through DNS rebinding.
- **No new dependencies:** Python's `http.server` plus one HTML file with plain JavaScript. Nothing loads from the internet, so your data never leaves the machine.
- **Data comes from what's already stored:** `data/applier.db`, `config/answers.yaml`, `data/logs/*.log`, `data/screenshots/`. Screenshots are served by file name from that folder only.

## Pages
| Page | Shows |
|---|---|
| Overview | The after-applying funnel and reply rates; today per site (applied / cap and the other outcomes), all-time totals, applications per day for the last 30 days, the most common skip and failure reasons, average fit of applied jobs |
| Applications | Every job Sortie handled (latest attempt per job), filterable by site, status, stage, date range and text. A row opens the details: job description, fit score, reason, every question and answer, screenshot, earlier attempts, link to the job |
| Needs you | Things only you can do (voice/video questions, recruiter messages) |
| Follow-ups | Applications tracked for recruiter questionnaires, with their status (open, answered, none after 14 days) and a note. The questionnaire's own questions and answers aren't stored yet |
| Answers | Your saved answers in `config/answers.yaml` |
| Runs | The run logs, newest first, readable in the page |

## Outcomes: what happened after applying (done 2026-11-12)
- In an applied job's details you set its stage: **Heard back → Interview → Offer**, or **Rejected** / **Withdrawn**, with an optional note ("call with HR on Monday"). Each change is kept as a timeline. **Clear** removes the job's outcome entirely (for a mis-click).
- Stored in the `outcome_events` table (job, stage, note, time); a job's stage is its latest event. This is the dashboard's only write, and it never contacts job sites.
- Overview: a funnel over applied jobs (applied → heard back → interview → offer, plus rejected), and response and interview rates by site and by fit score (below 60, 60–69, 70–79, 80+, unscored). "Heard back" counts any reply, rejections included. "Interview" counts jobs that ever reached interview or offer.
- Applications list: a Stage column and a stage filter ("any reply", "no reply yet", or one stage).
- Writes are protected against other websites: the request must be JSON (`Content-Type: application/json`, which browsers can't send cross-site without a preflight the server never answers) and its `Origin`, when present, must be this dashboard.

## API (JSON, used by the page)
`/api/overview`, `/api/applications?site=&status=&q=&from=&to=&offset=`, `/api/application/<id>`, `/api/needs-you`, `/api/followups`, `/api/answers`, `/api/runs`, `/api/runs/<file>`; files: `/screenshots/<file>`. Write: `POST /api/application/<id>/outcome` with `{"stage": "interview", "note": "…"}` (`stage: "none"` clears).

## Lessons
| Issue | Fix |
|---|---|
| Stored reasons on scored jobs read "fit 75: <reason>", so every failure grouped as "low fit score" | `reason_group` strips the "fit N:" prefix first; only a *skipped* job whose reason is the scorer's explanation counts as a low fit score |
| The day chart's labels were squashed on a phone (a fixed 900-wide drawing stretched to fit) | The chart is drawn at its container's width and redrawn on resize. The axis starts at the first day with data (14 to 30 days), with round gridlines |

## Notes
- Overview counts: *today* counts every attempt (a retried job can show failed and applied), *all time* counts each job once by its latest attempt.
- Colors: sites use the validated categorical palette in a fixed order (a site keeps its color); outcomes use status chips with an icon and a label, never color alone. Light and dark themes follow the system, with a toggle in the sidebar. The day chart has a table view.
- Identity: the Sortie mark (a paper plane taking off along a dotted flight path, on a navy tile) in the sidebar, the top bar on phones and the favicon; "Sortie." wordmark with the tagline "Job mission control"; an afterburner-orange marker on the active page. The brand colors (`--brand`, `--brand-2`, `--brand-night`) are for identity only and never encode data.
- Auto-refresh every 30 seconds while the tab is visible and no details panel is open.
