# Feature: Instahyre Adapter

**Status:** In progress (search + job page mapped live; apply awaiting first logged-in review run) · **Phase:** 1 · **Code:** `src/sortie/sites/instahyre.py`

## Login
- `sortie login instahyre` opens `instahyre.com/login/` in the bot's Chrome; log in once (email, Google or LinkedIn), press Enter.
- Logged-in check: open `/candidate/opportunities/`; a redirect to a `login` URL means logged out.

## Recommended opportunities first (verified 2026-11-05, logged in)
- Before searching, Sortie goes through the jobs Instahyre recommends to you (the **Opportunities** page): `GET /api/v1/candidate_opportunities/candidate_matching?interest_facet=0&limit=30&offset=<n>` (+ empty filter params). `interest_facet=0` = not acted on yet; `meta.next` is set when there are more pages.
- Each entry wraps the same job as search (`job.id`, `job.opportunity_url`, `job.locations`, `job.keywords` as a `"['Java', 'SQL']"` string, `job.is_sourced`) plus `employer.company_name`, so the normal job page / apply flow is used. A job found in both is handled once.
- Recommendations still go through your filters and fit score (e.g. a Chennai recommendation is skipped if Chennai isn't in `locations`).
- The page also has "Not interested" (`submitChoice(opp, false)`); Sortie never clicks it.

## Search (verified 2026-11-05)
- Public JSON API, fetched through the browser session: `GET /api/v1/job_search?skills=<role>&years=<total years>&company_size=0&job_type=0&offset=<n>`.
  - `skills` takes free text (the role), `years` filters by experience (your total years, computed from `career_start`).
  - `locations` is **ignored** by the API, so cities are filtered by `filters.skip_reason`.
  - 35 results per page; up to 3 pages per role. A page with fewer than 35 is the last.
- Each result has: `id`, `candidate_title`, `employer.company_name`, `locations` (comma-separated), `keywords`, `public_url`.
- `is_sourced: true` + `sourced_apply_url` → the job is applied to on the company's site → recorded as `external`.
- The API has no description or experience range; those come from the job page.

## Job page (verified 2026-11-05, logged out)
- Description: `.profile-content.job-description` (key skills from the API are appended).
- Experience: `.job-locations .experience` (e.g. "5-8 Years"). Location: `.job-locations .location-span`.
- Apply, logged in (verified 2026-11-05): `.apply-button button[ng-click^='submitChoice']` ("Apply now"). Logged out it's `button#job_details_top`; both are in `SELECTORS["apply_button"]`.
- Already applied, logged in: `.apply-button .application-sent` ("Application sent! on <date>") → skipped.

## Apply
1. `external` (sourced) → not applied here. Button already reads "Applied" → skipped.
2. **Confirm before clicking Apply now** (review mode asks here).
3. After clicking, Instahyre shows *"Application sent to <company>. Want to apply to other similar jobs?"* with **Apply to N jobs / Cancel**. Sortie **always clicks Cancel**: it never bulk-applies to jobs you didn't review. The modal means success → `applied` + screenshot.
4. `.application-sent` appearing also counts as success. Anything else after ~15 s (questions, resume prompt, …) → `failed` + debug dump to map it.

**Not verified yet:** the click itself and the post-apply modal while logged in. First live run (2026-11-05): 4 of 5 jobs had been applied to before Sortie (shown as "Application sent!"), and the logged-in "Apply now" button differs from the logged-out one; both fixed. Confirm the click on the next `--review --limit 1` run.

### Lessons from live runs
| Issue | Fix |
|---|---|
| Logged-in job page has a different Apply button and an "Application sent!" state; jobs were marked "no apply button" | Logged-in selectors added; already-applied → skipped |
| Clicking "Apply now" timed out: a wrapping `<a>` intercepts pointer events | Fire the button's click event directly (`dispatch_event("click")`) so Angular's `ng-click` runs |

## Inbox & recruiter questionnaires (`sites/instahyre_inbox.py`, verified 2026-11-05)
Runs through the site hooks: `before_jobs` scans the inbox, `after_jobs` answers questionnaires once the applications are done. Verified states 2026-11-05: open, already sent, closed/redirect.

**Scan (start of the Instahyre run)**
- `GET /api/v1/inbox_page/candidate_conversation?limit=20&offset=<n>`, paged: every conversation from the **last 14 days**, read or unread, however many. No cap on the count; only a 200-page safety stop, and 10 pages if conversations carry no readable date. It stops at the first page reaching older ones, or when `meta.next` is empty. It was the last 10 until 2026-11-11; a run with many applications brings more than 10. The date comes from the first date field present (`latest_message_time`, `modified`, …); without one, only the cap applies (field to confirm live).
- **Pending** = the latest message isn't yours. The preview (`latest_message`) prefixes your own messages with "You:" (e.g. "You: done", "You: I've filled the form"); those are ignored.
- Pending conversations are classified from the preview, which doesn't mark anything read. Instahyre's questionnaire mails start "Thanks for showing interest in working with <company>. As part of the application process…" (previews are cut off mid-word).
- Questionnaire → its messages are fetched (`GET /api/v1/resume_modal/emails/message?conv_id=<id>`) to find the link `instahyre.com/questionnaire/<questionnaire id>/<opportunity id>` (newest message wins).
- Anything else (or a questionnaire-looking preview without a link) → **not opened**, logged: `💬 message from <recruiter> (<job>)[, unread]: "<preview>" (left for you to reply)`.
- Never open the inbox page itself in the bot browser: it auto-opens (and marks read) the first conversation.

**Answer (end of the run)**
- You may have filled a questionnaire without replying in chat, so every questionnaire page is checked first:
  - already filled → the page says "Questionnaire has been sent. Thank you for your time." with no form → logged "already submitted", nothing sent;
  - closed → the page redirects to the inbox → logged "no longer available";
  - open → answered as below.
- Questions: `GET /api/v1/questionnaires/candidate_questionnaire/<id>` → `questions[]` with `text`, `type` (0 single line, 1 multi line, 2 single choice; the page also supports multiple choice, dropdown, numeric, rating scale, video, file, info box), `choice_options[]` (`label`, `reject_action`).
- Page `/questionnaire/<id>/<opportunity id>`: one `div[ng-repeat='question in questionnaire.questions']` per question, text in `.question-label span`, answer in `.answer-container` (textarea / text or number input / `.choice-options label` / select). Submit: `button[ng-click='submitQuestionnaire()']`.
- Each question goes through the normal answerer (profile → `answers.yaml` → LLM → you); field type comes from the choices or `question_kind`.
- Any question that can't be answered, or an unsupported field (rating, video, file), → the whole questionnaire is skipped and logged with its URL to do by hand.
- Picking a choice the recruiter marked `reject_action` logs a warning.
- Review mode shows all answers before Submit; dry run never submits. After Submit, if the button is still there and no confirmation text appears → warning + debug dump.

Dry run on a real questionnaire (12 questions): 7 answered automatically (Java/DSA/total experience from dates, CTCs from the profile, Angular 0, HLD/LLD and self-rating by the LLM); 4 came to the user: Jaipur 5-day WFO, serving notice, last working day, offers in hand. Fixes made from it: "5 days" no longer makes a question numeric; "share details about any offer … CTC" is text and goes to the user instead of the current-CTC rule; "Are you serving notice?" goes to the user; "total full-time YOE (exclude internships)" = total experience.

## Bot protection
- Instahyre is behind **Cloudflare**. Repeated headless visits get a "Just a moment… / Performing security verification" page (403).
- `SiteAdapter.check_blocked` waits up to 20 s for that check to clear (it usually does in a visible browser), then stops the site for the run if it's still there.
- Run Instahyre visible (default), not `--headless`.

## Follow-ups (2026-11-11)
- Instahyre tracks follow-ups (`FOLLOWUPS = True`). The runner calls `follow_up` **before applying** and, if anything was applied, again after. This replaces scanning in `before_jobs` and answering in `after_jobs`.
- The inbox is paged past the 14-day window (up to 10 extra pages) until every tracked application's conversation (`job_id`) has been seen.
- Outcomes go back to each record:
  - questionnaire sent / already submitted → `answered`
  - closed → `none`
  - couldn't answer or unsupported field → `needs_you` (also on the needs-you list)
  - a recruiter message → `needs_you`
- **Finished questionnaires are remembered** (`done_questionnaires` table: questionnaire id + outcome). Submitting a questionnaire adds no chat message from you, so its conversation keeps looking like it awaits your reply. Questionnaires that were sent, found already submitted, or closed are never opened again; the scan only counts them. Their follow-ups close as `answered`. (Before 2026-11-11 every run re-opened them, e.g. 8 already-submitted pages each run.) The log says "questionnaire … found" for new ones, not "pending".

