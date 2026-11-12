# Feature: CutShort Adapter

**Status:** ✅ Done 2026-11-10. A regular site (runs with the others). Verified live end to end: search, applying (14 applications with job-specific messages) and questionnaires (8 sent, confirmed via `screeningSubmitted`). Not yet seen live: a voice/video question (detection tested on sample pages) · **Phase:** 1 · **Code:** `src/sortie/sites/cutshort.py`, `src/sortie/sites/cutshort_inbox.py`, reusing `src/sortie/sites/forms.py`

## How CutShort applying works (from use)
1. **Apply** on a job opens a short form. It usually asks for a **submission message** (why you fit), and sometimes adds screening questions.
2. Shortly after you submit, the recruiter's side posts a **message in CutShort's chat**. Often that's more questions or a form to fill, much like Instahyre's questionnaires.
3. Some forms ask for a **voice (or video) recording**. Sortie can't record your voice and shouldn't fake one, so it leaves those for you.

Goal: every form Sortie can complete honestly gets completed (the apply form, the chat follow-up questions, linked forms), and nothing is left half-done without you knowing.

## Search (verified 2026-11-10, logged out)
- Listing pages: `https://cutshort.io/jobs/<keyword>-jobs-in-<city-slug>`, e.g. `java-jobs-in-bangalore-bengaluru`. 50 jobs per page.
- The page embeds its data as Next.js JSON (`<script id="__NEXT_DATA__">`) at `props.pageProps.dehydratedState.queries[0].state.data.data.pageData.jobs`. Sortie reads that, not the cards. Per job:
  - `_id`, `headline` (title), `companyDetails.name`, `locationsText`, `remoteType`
  - `expRange.min` / `expRange.max` → `Job.experience` (the experience filter runs before any page visit)
  - `salaryRangeText` ("₹10L - ₹14L / yr"), `allSkills`
  - `sanitizedComment`: the **full description** as HTML, so `get_details` needs no page visit and scoring is cheap
  - `publicUrl`: the job page Sortie opens to apply. (`authApplyUrl`, `/profile/view/j/<_id>`, never loads, even for a person: "This is taking unusually long… Refresh Cutshort?", 2026-11-10.)
- Logged out, a job's `publicUrl` redirects to a listing; applying needs login anyway.
- **Keywords (verified 2026-11-10):** listing pages exist only for common keywords ("java", "spring-boot", "backend-developer", "backend-engineer"). Whole role phrases ("python-backend-developer", "full-stack-developer") have no data. So each role becomes, at most 4 keywords (`role_slugs`):
  - the whole phrase;
  - job-family pairs ("backend-developer"). Pairs that are the second half of a phrase ("stack-developer") are skipped, and a lone "backend"/"frontend" becomes "backend-developer";
  - multi-word skills named in the role ("spring-boot", "full-stack");
  - single skills from your profile that the role names ("java", "react").

  Keyword pages with no data are skipped, and each keyword + city is loaded once per run. Live: five roles in one city → 169 jobs, 120 passing the filters.
- **Paging:** `?page=2` returns the same 50 (logged out), so each keyword gives its **50 newest** jobs. Breadth comes from several keywords per role across your cities (169 jobs for five roles in one city). Logged-in paging is unchecked; it's a known limit, not a blocker.
- City slugs: `bangalore-bengaluru` (mapped in `CITY_SLUGS`); `kolkata`, `jaipur` are as written. Countries aren't searched (India only).

## Apply (verified live 2026-11-10, up to Send)
1. **From the job's card on the listing it was found on.** The card's button is located by description at click time: the nearest block around the job's link that has an action button, then that button (`card_action_xpath`). It's never tagged in advance, because the listing re-builds its cards while scrolling and a tagged button is replaced before the click lands. That was why "Apply now" clicks did nothing.
   - The button says **Apply now**, or **View conversation** once applied → `skipped: already applied`.
   - Card not on the listing any more → the job's own page (`publicUrl`); a redirect stops it without clicking.
2. **The popup** (`[role=dialog][aria-modal=true]`):
   - "You are applying for **<title>** at **<company>**", then "To: <recruiter>"
   - a **"Type your message…"** textarea (`name=message`)
   - your **resume, already attached** ("Upload another resume")
   - **"Follow <company>…"**, ticked by default and left as CutShort sets it
   - **Send** and **Cancel**

   The dialog must mention the job's title or company, else it's closed unfilled. "You already have an active conversation on this job" → `skipped: already applied`.
3. **The message is the application.** `Answerer.compose()` writes 3–5 first-person sentences from your resume, `about.md` and the job description. Profile rules don't apply, and it isn't saved to answers.yaml. If the LLM isn't confident, you're asked, with its draft as the suggestion. If nothing can be written, the job is skipped.
4. Other fields, if any, go through the form filler. Required fields must be filled before Send, voice/video/file steps → needs-you, and unfamiliar steps → the LLM navigator (which may never press Apply or Send).
5. `confirm()` (review mode shows "Message to the hiring team → …"), then **Send**.
6. **Success** (as you described it): "Sending…", a success strip, the dialog closes, and **this job's card** button turns into **View conversation**. Only this card is checked, since other cards may already say "View conversation".
7. Anything not sent → the popup is closed (Cancel), never left half-written.

## After applying: CutShort conversations (built and dry-run verified 2026-11-10)
A moment after you apply, the recruiter's side (often "Voila on <recruiter>'s behalf") posts a **[Questionnaire]** in the job's conversation: a short form of screening questions with a Submit button.

- **Code:** `sites/cutshort_inbox.py` (`CutshortInbox.follow_up`), called by the runner **before applying** (pending ones first) and, if anything was applied, **again after**. `after_jobs` only waits until the newest application is a minute old (`QUESTIONNAIRE_DELAY_S`).
- **Tracked applications (reliable path):** every application Sortie sends is a follow-up record (see tracking-db.md), with its conversation id from the card's "View conversation" link. Each open record's thread is loaded directly:
  - submitted → `answered`;
  - an open questionnaire → answered (`answered`, or `needs_you`);
  - the last message from the recruiter → needs-you;
  - otherwise it stays open.

  Records without a conversation id (backfilled) are matched to their conversation by job id while paging the awaiting list. The first live run found 31 questionnaires the page-based inbox had missed.
- **Read without read receipts** (JSON, through the browser session):
  - `GET /conversations-v2/candidate?page=1&user_role=candidate&convo_status=awaiting`: the list. No account-specific `context` parameter is needed.
  - `GET /loadthread-v2/<conversation id>`: a thread. Opening a thread in the page also POSTs `/markthreadasread`; reading the JSON doesn't.
  - Your user id: `GET /api/auth/user` → `_id`. Conversations whose last message is yours are skipped.
- **Which:** every conversation awaiting your reply from the **last 14 days**, whoever applied (you or Sortie). Sortie reads page after page (CutShort sends 8 per page, newest first) and stops when a page reaches older conversations. No cap on the count; only a 200-page safety stop. Reading only page 1 had covered just the newest 8, so a run with many applications missed most of its questionnaires. Review mode shows every questionnaire before it's sent.
- **Questionnaire data:** in the thread, the screening message has `questions[]`. Each has `question.title`, `responseType` (`mcq` seen so far: 23 of 23 on 2026-11-10), `options[].label` and `isMandatory`, plus `screeningSubmitted` on the message.
- **Answering:** the usual order (profile → answers.yaml → LLM → you). CutShort's standard questions are covered by profile rules:
  - "The location of this job will be X. Are you okay with this?": in your city → "currently in this location"; else relocate → "can relocate"; else "not okay".
  - "This job offers around ₹AL – ₹BL / yr. Does this work for you?": Yes if B ≥ your minimum expected CTC.
  - "What is your official notice period?": your notice (or what's left of it when serving).
- **Filling (page):**
  - Open the thread. The questionnaire is a `<form>` with one list item per question. Choice questions are a `fieldset` titled by its `legend`, with options as clickable `div[tabindex=0]` blocks holding the option text; Sortie clicks the block whose text equals the answer. Text questions are a `label` with a `textarea` (placeholder "Text"), which Sortie fills. Each question is found by its list item and title.
  - `confirm()`, then the form's own `button[type=submit]`. CutShort then asks, inside the form, "Responses cannot be changed after submission. Are you sure?" **[Yes] [No]**, where Yes is the new submit button. Sortie presses Yes, since the answers were just confirmed.
  - Success = `screeningSubmitted` true in the thread JSON.
- **Can't complete:** a mandatory question of an unsupported type (audio, video, file, anything unknown), or a mandatory question nobody can answer → not sent, and it goes on the needs-you list with a link to the thread. Optional unsupported questions are left blank.
- **Other messages** (not a questionnaire) → never answered; logged and put on the needs-you list with the thread link.

## Needs-you list (built 2026-11-10, shared by all sites)
Anything Sortie deliberately leaves for you should be easy to find, not buried in the log.
- Recorded in the database with the site, job, URL and what's needed ("voice recording", "file upload", "message to reply to").
- `sortie report` gets a **Needs you** section listing them with links. Items drop off once they're done or older than 14 days.
- The Instahyre inbox's "left for you" messages go there too (built).
- Code: `NeedsYou` (models), table `needs_you` (db, unique per site + link + what), `ApplyResult.needs_you` for a job's own form, and `SiteAdapter.needs_you` for items found outside a job (inbox), saved by the runner after `before_jobs` / `after_jobs`.
- Detection (`forms.needs_you_reason`): an `input[type=file]` accepting audio/video, a visible Record / mic button, or "record a voice/video answer" wording → voice or video recording. Any other required upload (not the resume) → file upload.

## Safety
- Daily cap `daily_caps.cutshort` (example 30). Visible browser, `normal` pace.
- Chat replies and linked forms count as submissions: they go through `confirm()`, so review mode asks before each one.
- Nothing is sent from your CutShort account that isn't an answer to a recruiter's question about a job you applied to through Sortie.

## Build order
1. Search from `__NEXT_DATA__`: role → slug mapping, paging, JSON → `Job`, full description. Tests with an anonymised fixture.
2. Login check + apply form with you present (`--review --limit 1`): map the Apply button, the form, the submission message, success, already-applied.
3. Voice/file detection + the needs-you list + `sortie report` section.
4. Chat: find the conversation API, map a follow-up message, answer in chat / fill linked forms (review mode first).
5. Docs and lessons table after each live run.

## Lessons from live runs
| Issue | Fix |
|---|---|
| First review run (2026-11-10): "no apply button". The logged-in job view (`/profile/view/j/<id>`) was still showing its loading dots when Sortie looked after 2.5 s | Poll for up to 25 s until an Apply or Applied button appears. Apply labels now also cover "Express interest" / "I am interested" |
| `/profile/view/j/<id>` (the data's `authApplyUrl`) shows "This is taking unusually long… Refresh Cutshort?" and never loads, even opened by hand | Apply from the public job page (`publicUrl`). Two guards: (1) if CutShort redirects (logged out it sends you to a listing full of other jobs' Apply buttons), stop without clicking; (2) after Apply, the form must mention the job's company or title, else it's closed unfilled |
| Logged in, `publicUrl` loads for some jobs but redirects to a listing for others | Apply from the job's own card on the listing it was found on (link → card → that card's Apply now); the public page is only a fallback, guarded against redirects |
| **"Apply now" opens a message box ("Type your message…"), and that message *is* the application.** Its placeholder was read as a question, the LLM answered "Yes", and the application went out with "Yes" as the message. Afterwards the card showed "View conversation", which wasn't recognised as applied | The message box (`MESSAGE_BOX`) is filled with a 3–5 sentence note written for the job (resume, notes, description), shown at the submit prompt as "Message to the hiring team". If no note can be written, the job is skipped. "View conversation" = applied (card and success check) |
| A re-apply on that job showed the popup "You already have an active conversation on this job" | That text → `skipped: already applied`, and the popup is closed without typing anything |
| No dialog was found after Apply, so the whole listing was used as "the form": the filler typed into CutShort's search box ("Search for jobs e.g Android jobs in Jaipur"), and the LLM navigator clicked "Apply now" on the listing. Nothing was sent | **The form is found from the message box:** the smallest container around the "Type your message…" box that also holds its Send button (`MESSAGE_FORM_JS`). No message box or dialog within 10 s → fail with nothing filled. The job check looks up from that form for the title or company and stops at any container with more than one job link (`FORM_MATCHES_JS`). Generic: the LLM navigator may never press an **Apply** button (any site), and search boxes are never read as questions |

| A card's "Apply now" click showed no message box on the page within 10 s (nothing filled, nothing sent) | Waits 15 s and follows a new tab if Apply opens one (closed afterwards, back to the listing). Where the box really opens is to be confirmed by the user |
| "Apply now" clicks opened nothing, though the same click by hand works. The listing re-builds its cards on scroll, so the button tagged before scrolling was gone when the click landed | The card button is located at click time with an XPath locator (`card_action_xpath`); tagging was removed |
| The message came out as "3 years 5 months": the note request mentioned "experience", so the profile's experience rule answered it | `Answerer.compose()` for job-specific writing: LLM (or you), no profile rules, not saved to answers.yaml |
| A listing load failed with `ERR_ABORTED` and crashed the run | A listing that doesn't load is logged and skipped; any unsent popup is closed with Cancel |

| First real Send (2026-11-10) went through, with the generated message, but was logged "submit did not complete": once applied, the card's **View conversation** is an `<a>` (to `/profile/conversations/candidate?…&thread=<id>&stage=awaiting`), not a `<button>` | Card actions match `button` or `a`. The thread link is what the chat follow-up step will use |
| First real questionnaire runs: "may not have been sent". After Submit, the form turns into "Responses cannot be changed after submission. Are you sure? [Yes] [No]", which Sortie didn't press, so nothing was sent and the questionnaires stayed open | After Submit, press the form's **Yes** if it appears, then confirm via `screeningSubmitted` |
| 4 of 8 questionnaires answered "No" to the salary question: those jobs top out at ₹13–15L, below the 16 LPA minimum. They shouldn't have been applied to, but the salary pre-filter only read Naukri's "Lacs" format | `filters.skip_reason` also reads lakh amounts like "₹10L - ₹14L / yr" (CutShort), so such jobs are skipped before applying |

| Questionnaire with a free-text question: "Locator.fill: Timeout". Text questions are `<li><label>title</label>…<textarea placeholder="Text">`, not a fieldset with a legend | Each question is found by its list item (`li` containing a `legend` or `label` with the title) |
| "What is your current annual CTC and expected CTC? (fixed + variable + ESOPs)" would have got only the expected CTC | Profile rule: a text question asking for both → "Current CTC: X LPA; expected: A-B LPA" |
| 4 questionnaires "failed" because CutShort's server was down: thread pages were Cloudflare's "Gateway time-out" (504) | A 5xx thread page stops the questionnaire step for that run with one clear message; they stay open for the next run |
| The text question "current annual CTC and expected CTC?" was sent as "18": the kind was guessed from the wording (CTC → decimal), so the both-values rule (text only) was skipped | CutShort `responseType: text` is always a TEXT question |
| Only page 1 of conversations (8) was read, so questionnaires beyond the newest 8 waited for another run (13 applications took 2 runs) | Page through until conversations are older than 14 days (cap 60) |
| Questionnaires beyond the newest 8 conversations were never answered: 31 in the last 14 days (found 2026-11-11) | Follow-up records: every application is tracked and its own conversation checked every run (before and after applying), backfilled for the last 14 days |
