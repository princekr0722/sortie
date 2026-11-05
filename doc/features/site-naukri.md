# Feature: Naukri Adapter

**Status:** In progress (search, details, apply, success page and chatbot verified live; checkbox questions not seen yet) · **Phase:** 1 · **Code:** `src/sortie/sites/naukri.py`

## Login
- `sortie login naukri` opens `naukri.com/nlogin/login` in the bot's Chrome; log in once, press Enter.
- Logged-in check: open `/mnjuser/homepage`; a redirect to `nlogin` or a visible login form means logged out.

## Search (verified 2026-11-05)
- URL: `/{role-slug}-jobs-in-{city}[-{page}]?k={role}&l={city}&experience={total years}&jobAge=7`.
  - `experience` = your total years, computed from `career_start` (Naukri shows jobs whose range includes it).
  - `jobAge=7` = posted in the last 7 days.
- Cities: any Indian city from `search.yaml`, slugged from its name (`CITY_SLUGS` covers exceptions: Bengaluru → `bangalore`, Gurugram → `gurgaon`). Foreign countries are skipped (Naukri is India only).
- Remote: a remote search URL returned non-remote jobs, so it isn't used; remote/hybrid jobs still pass the location filter when they appear.
- 20 cards per page; a page with fewer than 20 is the last (later pages ignore `jobAge`). Max 5 pages per role × city.
- Same job under several roles/cities is yielded once per run.
- Card fields: `data-job-id`, `a.title` (title + URL), `.comp-name`, `.expwdth`, `.sal` (often missing), `.locWdth` (comma-separated, may start with "Hybrid - ").

## Job page (verified 2026-11-05)
- JD: `[class*='JDC__dang-inner-html']`; key skills appended to the JD for fit scoring.
- Salary: `[class*='jhc__salary__'] span` ("Not Disclosed" → blank).
- Apply state: `#apply-button` (text "Apply"), company-site button (→ `external`), "Applied" (→ skipped).

## Apply
1. Already applied → skipped. Company-site button → `external` (phase 3 queue). No button → failed + debug dump.
2. **Confirm before clicking Apply**: Naukri submits on click (or when the chatbot finishes), so review mode asks here, with the job shown but no answers yet.
3. Click Apply, then loop (max 20 steps, 2.5 s apart):
   - success → `applied` + screenshot. Verified: Naukri navigates to a page titled **"Apply Confirmation"** with `.applied-job-content` ("Applied to …").
   - chatbot question → read the last bot message + radio / checkbox / chip options → answerer → click option or type into the text box → send.
     Options are split on new lines (one element can hold the whole list) and "Load more" is dropped; the option is clicked by its exact text inside the chatbot.
   - unanswerable question → close chatbot, `skipped` with the question as reason, debug dump.
4. No success after 20 steps → `failed` + debug dump.

**Pre-filled text box:** Naukri fills the chatbot text box from your Naukri profile (e.g. your Naukri profile's city for "What is your current location?"). Left there, it is sent together with the clicked option ("Bengaluru / Bangalore, <profile city>"), and typed answers get appended to it. The bot now clears the box (select all + Backspace) before every answer. The chatbot also says "Your profile will be updated with the details that you provide", so answers can change your Naukri profile.

Field type for chatbot text boxes: CTC / lakh / salary → decimal; "how many / years / days" → number; else text.

**Chatbot markup (verified 2026-11-05):** drawer `.chatbot_DrawerContentWrapper`; question = last `.chatbot_ListItem .botMsg`; single choice = `.ssrc__radio-btn-container` (Yes / No / "Skip this question" — the skip option is dropped); chips `.chatbot_Chip`; text box `div.textArea[contenteditable]`; submit `.sendMsg` ("Save", disabled until answered); close `.chatbot_Nav .crossIcon`. Use exact classes: `[class*='chatbot_Chip']` also matches the `.chatbot_Chips` container and reads every option as one. Checkbox (multi-select) questions haven't appeared yet. `DUMP_CHATBOT = True` keeps saving each question to `data/debug/`; switch it off once checkbox questions are confirmed.

### Lessons from live runs (2026-11-05)
| Issue | Fix |
|---|---|
| Success not detected; after applying Naukri navigates to "Apply Confirmation" | Detect that page title / `.applied-job-content` |
| Option list read as one string (`[class*='chatbot_Chip']` also matched the `.chatbot_Chips` container) | Exact class selectors, split options on new lines, click by exact text |
| Radio answer not clicked → Save stayed disabled, job failed | Click the option's own text inside the chatbot; failed jobs are retried once |
| Location sent as the chosen city **plus** Naukri's pre-filled profile city | Clear the text box before every answer |

## Dry run on Naukri
`--dry-run` stops before clicking Apply, so it shows which jobs would be applied to but **can't preview chatbot questions** (they only appear after clicking).

## Extras (later)
- Optional daily profile refresh to stay near the top of recruiter searches.
