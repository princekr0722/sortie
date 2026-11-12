# Feature: Indeed Adapter

**Status:** ⏸ Disabled since 2026-11-09. Built and merged, but switched off in `sites/__init__.py` (`DISABLED`): `sortie run --sites indeed` refuses and gives the reason. Blockers and how to re-enable: [TRACKER 2.2d](../TRACKER.md) · **Phase:** 2 · **Code:** `src/sortie/sites/indeed.py`, `src/sortie/sites/forms.py`

## Why it's disabled (2026-11-09)
1. **Choice answers don't register.** On some employers' question steps, Sortie's clicks on Yes/No radios show as selected and pass every DOM check, but Indeed's React form never records them, and Continue fails with "Choose an option to continue". Next step: on a live form, try each click method (label, input, keyboard, JS) and see which one Indeed accepts.
2. **CAPTCHA before almost every Submit** after a day of many runs and partial form attempts. Sortie won't solve CAPTCHAs; it waits for you. Before re-enabling: Indeed cap about 10/day, `careful` pace, a Google sign-in in the bot's browser, one run a day.

The search, job page, form walking, consent, dates, the "requirements not met" step, CAPTCHA hand-off and the LLM step navigator all worked live (about 10 applications sent on 2026-11-07).

## Login
- `sortie login indeed` opens `secure.indeed.com/auth` in the bot's Chrome. Log in once (email + code, or Google), then press Enter.
- One Indeed account works on every country site (`in.`, `ph.`, `uk.`, …), so a single login is enough.
- Logged-in check: open `myjobs.indeed.com/applied`. A redirect to `secure.indeed.com/auth` means you're logged out.

## Search (verified 2026-11-07, logged out)
- Page: `https://<country>.indeed.com/jobs?q=<role>&l=<city>&fromage=7&iafilter=1&start=<0,10,20>`. The last 7 days, up to 3 pages per role and location.
- **Easily apply only (`iafilter=1`):** Sortie can't apply on company sites yet, so the search asks Indeed for Easily apply jobs only. That saves opening, title-screening and recording jobs it would only mark `external`. `iafilter` comes from the page's own filter list ("Easily apply on Indeed"). Logged out, Indeed accepts it but doesn't narrow the results (verified 2026-11-07), so the per-job `indeedApplyable` check below stays as a safety net. Logged in, it only partly works (run 2026-11-07: company-site jobs still came through). Search therefore **drops** cards with `indeedApplyable: false` and counts them in the search log line ("N on company sites, left out"); they are not recorded. Remove the parameter once company-site applying (phase 3) needs those jobs.
- Indeed has a separate site per country. Locations in `search.yaml` are mapped as follows:
  - Indian cities → `in.indeed.com` with `l=<city>`.
  - Countries (Philippines, Singapore, UAE, USA, UK) → that country's Indeed (`ph.indeed.com`, …) with no city, so the whole country is searched.
- Job cards come from the JSON the page embeds (`window.mosaic.providerData["mosaic-provider-jobcards"].metaData.mosaicProviderJobCardsModel.results`), not from scraping the cards. Each result has:
  - `jobkey`, `displayTitle`, `company`, `formattedLocation`, `remoteLocation`
  - `salarySnippet.text` (when shown)
  - `indeedApplyable`: true = **Easily apply** on Indeed; false = the job is applied to on the company's site, so it's left out of the results (counted in the log)
  - `appliedOrGreater`: you've already applied, so the job isn't returned
- Experience isn't on the cards. The experience range is checked by the fit score, from the description.
- A page that adds no new jobs ends that search (Indeed repeats results past the last page).

## Job page (verified 2026-11-07, logged out)
- `/viewjob?jk=<jobkey>` on the same country site.
- Description: `sanitizedJobDescription` from the page's `window._initialData`, cleaned to plain text. Fallback: the text of `[data-testid='viewjob-main-content']`.
- **Easily apply**: "Apply now" is a link, `a[data-testid='viewjob-indeed-apply']`. Its `href` goes straight to the form (`smartapply.indeed.com/beta/indeedapply/applybyapplyablejobid?…`).
- **Company site**: a button, `[data-testid='viewjob-apply']`, labelled "Apply on company site". These jobs are recorded as `external`.
- Logged out, Indeed shows only page 1 of results; page 2 redirects to sign-in. Sortie logs "sign-in needed" and moves on.

## Apply (Indeed Apply / "smartapply")
Not verified live yet. The flow below follows Indeed's documented multi-step form, and anything unexpected is saved with `debug_dump`.
1. Already applied or external → skipped or `external`.
2. Click **Apply now** and follow the form (`smartapply.indeed.com`) into its new tab, or the same tab, or an embedded frame. Each step is read only after it renders. Sortie looks every half second, and a step gets **12 checks, 5 s apart (1 min)**, to show its buttons before Sortie gives up. Continue/Review counts once no button shows a spinner (`aria-busy`). A **Submit** button counts immediately, even when it's disabled and the page is still marked loading: that's the review page waiting on a CAPTCHA, which the submit logic hands to you. A form tab Sortie opened is closed afterwards.
3. Each step (contact info, resume, screener questions, relevant experience, …) goes through the **generic form filler** (`sites/forms.py`):
   - It reads every visible field: text, number, textarea, select, radio group (`fieldset` + `legend`; radios outside any group are grouped by `name`, with the wrapping label or the preceding text as the question) and checkbox group; searchable dropdowns (react-select, `aria-autocomplete=list` / `role=combobox`) are opened to read their options, then the answer is clicked and checked; disabled groups are skipped. A phone number the form flags as invalid is retyped in other formats, always with the country code (`fix_rejected_phone`). If none is accepted, an optional field is left empty; a required one fails the job, together with its label and options.
   - Fields Indeed already filled in (name, phone, chosen resume, earlier answers) are left alone. Fields labelled "(optional)" are skipped.
   - Each empty field becomes a `Question` (number input → number; select/radio → choice; checkbox group → multi-choice) and goes through the usual answer order: profile → `answers.yaml` → LLM → you.
   - A **single checkbox** counts as a consent. If it's required and labelled Agree / I agree / Accept / I acknowledge (e.g. "You declare that you have read the privacy notice"), Sortie ticks it and lists it with the answers as `(rule)`, so review mode shows it before Submit. A required single checkbox with any other label is skipped as "couldn't answer". Optional ones (job alerts, talent pools) stay unticked.
   - Date fields (`type=date`, or a placeholder like DD/MM/YYYY) are answered in that exact format. It can't fill file uploads; if a required one blocks the step, the step gets stuck (below).
   - Then it clicks the step's button: **Continue**, or **Review your application** on the last question step. Buttons are matched on their whole label.
4. **Review page** ("Review your application" or a **Submit your application** button): `confirm()` runs here (review mode asks you), then Sortie clicks Submit.
   - **CAPTCHA:** Indeed may put a reCAPTCHA in front of Submit: an "I'm not a robot" box on the review page, often followed by an image challenge. Submit stays disabled until it's solved. **Sortie never solves CAPTCHAs.**
     - Any mode: Sortie beeps, sends a macOS notification and logs "🧩 solve the CAPTCHA in the browser window". It waits up to 3 min and clicks Submit once the box is ticked.
     - Not solved in time → `failed: CAPTCHA not solved in time`. In review mode the run carries on. In auto mode Indeed stops for the run (probably nobody's at the screen), and the other sites carry on.
   - Detection: a visible `iframe[title='reCAPTCHA']` (excluding the invisible `size=invisible` one Indeed loads on every step) whose `#recaptcha-anchor` isn't `aria-checked="true"`, or a visible challenge iframe (title contains "challenge").
5. Success: the URL contains `post-apply`, or the page says "application has been submitted". Sortie records `applied` with a screenshot and closes the form tab.
6. If a step still looks the same after Continue (for example a required field it couldn't fill), it counts as stuck: Sortie saves a debug dump, records `failed` with the field errors Indeed shows, and closes the form. The application is never half-submitted, because only the review page's Submit sends it.
7. A question nobody can answer → `skipped` ("couldn't answer: …"), and the form is closed.
8. **"It looks like you don't meet these employer requirements"** (step `intervention`, shown after the screening answers, listing items such as "Kafka: 3 years (Required)") → `skipped`, with reason `Indeed: doesn't meet employer requirements (Kafka: 3 years)`. Sortie never clicks **Apply anyway**: the employer filters on those answers. The reason names the requirement, so you can apply by hand if you disagree.

## LLM step navigator (fallback for unfamiliar steps)
Application forms differ by employer and change over time, so fixed rules can't cover every step. The rules handle the common case at no LLM cost: fill empty fields through the answerer, then click Continue / Next / Review your application. When they can't decide, the LLM looks at the step and picks the next move.

- **When:** a step has no recognised next button (after a re-check), or clicking it didn't move on (the same step comes back, usually with an error message). At most **3** LLM moves per application.
- **What it sees** (text only): the step's headings, its visible text (cut to about 1500 characters), the fields with their labels, options and filled/empty state, the visible buttons by key and label (with disabled state), and any error messages.
- **What it can do** (one move per call): `click` a button to move on, `check` a required acknowledgement/consent box, `skip_job` (e.g. "not eligible", "job closed"), or `wait` (still loading).
- **Guardrails, enforced in code** (`sites/forms.py`):
  - It never answers questions. Field values always come from the answerer (profile → answers.yaml → LLM answer rules → you), so sensitive questions still never get an LLM answer.
  - Buttons matching submit / save / close / exit / back / return / cancel / report / apply anyway / withdraw / delete / remove / upload / edit / sign out are refused. **Submit stays rule-based and always goes through `confirm()`.**
  - Only fields of kind `consent` can be checked.
  - An invalid reply (an unknown key, a refused button, bad JSON) counts as no move, and the step fails as before, with a debug dump.
- Every LLM move is logged as `🤖 form step: click "Proceed"` (or `check` / `skip_job`), so you can see when the fallback was used and add a rule if the same step keeps coming back.
- The navigator is generic (it works on any page or frame), so company career sites can reuse it.

## Bot protection
- Indeed uses Cloudflare. The run stays visible (`headless: false`), and Sortie waits out "Just a moment…" pages for up to 20 s.
- "Additional Verification Required" (a Cloudflare challenge that needs a click) counts as blocked: Indeed stops for this run, and the other sites carry on. If it shows up, solve it in the bot's window during `sortie login indeed`.

## Caps
- `daily_caps.indeed` in `search.yaml`. The example config uses 25: Indeed restricts accounts that apply in bulk more readily than Naukri does.

### Lessons from live runs
| Issue | Fix |
|---|---|
| Normal form pages were stopped as "blocked": the Indeed Apply footer says "This site is protected by reCAPTCHA", which matched the `captcha` block pattern | The block check ignores that footer (`CAPTCHA_FOOTER` in `sites/base.py`) |
| "unknown form step": the step (29% on the progress bar) was read before it rendered; only the header, "Save and close" and the footer were on the page | Wait up to 20 s for a visible field or Continue/Submit before reading a step (`_wait_for_step`) |
| The form was opened by visiting the Apply now link's URL in a new tab | Click the link like a person and follow the new tab / same tab |
| The questions step (`/form/questions-module`, 43%) stays on a spinner forever for every job (run 2026-11-07); the page keeps `class="loading"` | Under investigation. Likely reCAPTCHA Enterprise scoring the browser as a bot, so Chrome now launches without Playwright's `--enable-automation` flag. A step that never renders is now reported as "never finished loading", with any Indeed request errors seen while the form was open |
| A job was recorded **applied** while Sortie was still on the "Review your application" page: text on the review page matched the success pattern, which was checked before every step. Submit was never clicked and no confirm prompt was shown (run 2026-11-07) | Before Submit, only Indeed's confirmation URL (`post-apply`) counts as success. Success text is checked only after Submit is clicked, and the pattern is strict ("Your application has been submitted"). The wrong record was corrected to `failed` so the job is retried |
| Some company-site jobs still came through with `iafilter=1` while logged in | Search drops cards with `indeedApplyable: false` and counts them in the log line. The runner also checks `external` before the LLM title screen |
| The "step rendered" check counted buttons such as "View full job description"; a regex inside a CSS `text-matches()` selector lost its backslashes | Buttons are read as text and matched in Python (`STEP_BUTTON`, full label match: Continue / Review your application / Submit your application) |
| "stuck on a form step" on the resume step: the snapshot shows Continue with a spinner (`aria-busy="true"`) while Indeed loads the next step. The earlier questions-step spinners were most likely the same slow hand-over | After Continue, wait up to 45 s for the URL to change or the button to stop being busy. Steps get up to 45 s (was 20) to render |
| Voluntary self-identification step (`demographic-questions-module`, 86%): "unknown form step". Its button reads "Review your application", not Continue, and it has a required "Agree" box for the employer's privacy notice | Next button = Continue / Next / Review your application (whole label). Required single checkboxes labelled Agree/Accept/Acknowledge are ticked and listed as `(rule)` |
| Submit timed out after 30 s: a reCAPTCHA checkbox plus a traffic-light image challenge sat in front of a disabled Submit (run 2026-11-07) | Never solve it. Review mode waits up to 3 min for you to solve it in the window, then clicks Submit. Auto mode stops Indeed for the run |
| Step `intervention`: "It looks like you don't meet these employer requirements · Kafka: 3 years (Required)", with Return to job search / Apply anyway. Reported as "never finished loading" (run 2026-11-07) | Skip the job with the listed requirements as the reason. Never "Apply anyway" |
| Waiting 45 s on every step was too long; only the resume (file) step is slow | 10 s for text steps; 45 s on the resume step and for the step after it (`_step_wait`). The resume step's Continue appears only after the PDF preview renders ("resume-selection never finished loading (10 s)") |
| Review page reported as "unknown form step": it was still loading (spinner at 100%); the check caught the previous step's Continue mid-transition, then found no buttons | Loading = `<html class="loading">` or a busy button, which works on any step. Sortie waits while loading (up to 45 s). "No button" is re-checked once before a step counts as unknown |
| One employer's form, "Choose an option to continue": two radios Sortie had answered (Acknowledge, the in-person location) were still empty. The Yes/No radios that looked handled had been pre-filled by Indeed. Clicks on Indeed's React radios didn't register | Each option input gets a key. Selection clicks the option's `label[for]`, then fires a click on the input, then force-checks it, and **verifies `is_checked()`**. If it still isn't selected, the job fails with "couldn't select …" instead of moving on |
| Fixed per-step timeouts (10 s / 45 s by URL) were guesswork | Wait is page-driven: continue while loading or while the page keeps changing; fail after 8 s idle (cap 60 s) |
| "resume-selection stopped changing (8 s idle)" was really the **review page** with Submit disabled behind an unsolved CAPTCHA. Indeed keeps `class="loading"` on the page meanwhile, so Sortie waited for loading to end instead of handling the CAPTCHA | A Submit button (even disabled) means the review page is ready, and the CAPTCHA flow takes over. The wait is 12 checks × 5 s (user's call) instead of the idle rule |
| Run sat on a loaded step without clicking Continue: Indeed leaves `<html class="loading">` on finished steps, so "wait until not loading" never ended | `class="loading"` is no longer used as a signal; only a busy button (`aria-busy="true"`) means "still working" |
| Another employer's form: 7 Yes/No radios answered, all empty again by the time Continue was pressed ("Choose an option to continue"). The click looked selected at first, then Indeed's React form reset it on its next render | A choice only counts if it's **still** selected after a short settle (400 ms). Sortie tries the label click, then a click on the input, then focus + Space, then a JS click. After the step, it re-reads the fields (by label, since elements may be re-created) and re-selects anything cleared once. If an answer still won't stick, the job fails with "the form kept clearing the answer to …" |
| The LLM fallback clicked the same "Continue" 3 times on a step stuck on unregistered radios | When a step is stuck with its own button, the LLM gets **one** move and isn't offered that button again |
| A job reported "no apply button". The job page showed a greyed-out **Applied** button | A visible "Applied" button → `skipped: already applied` |
