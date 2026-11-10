"""Generic web-form filler: reads the visible fields of one form step and answers the empty ones.
Used for Indeed Apply; written without site specifics so company career sites (phase 3) can reuse it."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from playwright.async_api import Frame, Locator, Page

from sortie.llm.answerer import Answerer, date_format, question_kind
from sortie.models import Answer, Job, Question, QuestionKind

# a whole page, a frame, or one element of it (e.g. an apply dialog, so fields behind it aren't read)
Scope = Page | Frame | Locator


def _root(scope: Scope) -> Locator:
    return scope if isinstance(scope, Locator) else scope.locator("body")


OPTIONAL = re.compile(r"\(optional\)|\boptional\b", re.I)
REQUIRED_MARK = re.compile(r"\s*\*\s*(required)?\s*$|\s*\*", re.I)
# a required consent box with one of these labels is ticked (and listed with the answers for review)
AGREE = re.compile(r"(i )?(agree|accept|acknowledge|consent|confirm|understand)\b.*", re.I)

# Each field gets a data-sortie-key so the Python side can find it again; the counter lives on window
# so keys stay unique across steps of a single-page form.
FIELDS_JS = """root => {
  const visible = e => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const key = el => (el.dataset.sortieKey ||= 'f' + (window.__sortieN = (window.__sortieN || 0) + 1));
  const labelOf = el => {
    if (el.labels && el.labels.length) return clean(el.labels[0].innerText);
    const by = el.getAttribute('aria-labelledby');
    if (by) return clean(by.split(/\\s+/).map(id => document.getElementById(id)?.innerText || '').join(' '));
    return clean(el.getAttribute('aria-label') || el.getAttribute('placeholder') || '');
  };
  const required = el => el.required || el.getAttribute('aria-required') === 'true';
  const fields = [], grouped = new Set();
  for (const group of root.querySelectorAll('fieldset, [role=radiogroup], [role=group]')) {
    const inputs = [...group.querySelectorAll('input[type=radio], input[type=checkbox]')].filter(i => !grouped.has(i));
    if (!inputs.length || !visible(group)) continue;
    inputs.forEach(i => grouped.add(i));
    const type = inputs[0].type;
    const legend = group.querySelector('legend');
    const label = clean(legend ? legend.innerText : labelOf(group));
    // a lone checkbox ("Agree" to a privacy notice) is a consent, not a pick-from-options question
    const kind = type === 'checkbox' && inputs.length === 1 ? 'consent' : type;
    const groupKey = key(group);
    inputs.forEach((input, i) => { input.dataset.sortieOpt = groupKey + '-' + i; });
    fields.push({key: groupKey, kind, label, options: inputs.map(labelOf),
                 filled: inputs.some(i => i.checked), required: inputs.some(required) || /\\*/.test(label)});
  }
  for (const el of root.querySelectorAll('input, textarea, select')) {
    if (grouped.has(el) || !visible(el) || el.disabled || el.readOnly) continue;
    const tag = el.tagName.toLowerCase();
    const type = tag === 'input' ? (el.getAttribute('type') || '').toLowerCase() : tag;
    if (tag === 'input' && !['text', 'number', 'email', 'tel', 'url', 'date', ''].includes(type)) continue;
    // a site's own search box is never a question ("Search for jobs e.g Android jobs in Delhi")
    if (el.getAttribute('role') === 'searchbox' || /^\\s*search\\b/i.test(el.getAttribute('placeholder') || '')) continue;
    const options = tag === 'select' ? [...el.options].filter(o => o.value !== '').map(o => clean(o.text)) : [];
    fields.push({key: key(el), kind: type || 'text', label: labelOf(el), options,
                 filled: el.value.trim() !== '', required: required(el), placeholder: el.getAttribute('placeholder') || ''});
  }
  return fields;
}"""

ERRORS_JS = """root => [...root.querySelectorAll('[role=alert], [id*=error i], [class*=error i]')]
  .filter(e => e.offsetWidth || e.offsetHeight).map(e => e.innerText.replace(/\\s+/g, ' ').trim())
  .filter(Boolean).slice(0, 5)"""


@dataclass
class Field:
    key: str
    kind: str  # text | number | email | tel | url | search | textarea | select | radio | checkbox | consent
    label: str
    options: list[str] = field(default_factory=list)
    filled: bool = False
    required: bool = False
    placeholder: str = ""

    @property
    def optional(self) -> bool:
        return not self.required and bool(OPTIONAL.search(self.label))


def to_question(f: Field) -> Question:
    text = REQUIRED_MARK.sub("", OPTIONAL.sub("", f.label)).strip(" *")
    if f.kind in ("radio", "select", "consent"):
        return Question(text, QuestionKind.CHOICE, f.options)
    if f.kind == "checkbox":
        return Question(text, QuestionKind.MULTI_CHOICE, f.options)
    if f.kind == "date":  # native date input: Playwright fills it with an ISO date
        return Question(text, QuestionKind.DATE, date_format="%Y-%m-%d")
    if fmt := date_format(f.placeholder):  # text box with a date picker, e.g. placeholder "DD/MM/YYYY"
        return Question(text, QuestionKind.DATE, date_format=fmt)
    kind = question_kind(text, [], multi=False)
    if f.kind == "number" and kind == QuestionKind.TEXT:
        kind = QuestionKind.NUMBER
    return Question(text, kind)


async def read_fields(scope: Scope) -> list[Field]:
    raw = await _root(scope).evaluate(FIELDS_JS)
    return [Field(**r) for r in raw if r["label"]]


async def form_errors(scope: Scope) -> list[str]:
    return await _root(scope).evaluate(ERRORS_JS)


async def fill_field(scope: Scope, f: Field, value: str | list[str]) -> None:
    target = scope.locator(f"[data-sortie-key='{f.key}']").first
    if f.kind == "select":
        await target.select_option(label=value if isinstance(value, str) else value[0])
    elif f.kind in ("radio", "checkbox", "consent"):
        for item in value if isinstance(value, list) else [value]:
            await _select_option(scope, f, item)
    else:
        await target.fill(", ".join(value) if isinstance(value, list) else str(value))


SETTLE_MS = 400  # a React form can undo a click it didn't register on its next render; check after it


async def _pause(scope: Scope, ms: int) -> None:
    page = scope if isinstance(scope, Page) else scope.page
    await page.wait_for_timeout(ms)


async def _select_option(scope: Scope, f: Field, item: str) -> None:
    """Tick one radio/checkbox option and make sure it *stays* ticked: a React form ignores a click that doesn't
    reach its handlers and re-renders the option empty. Tries what a person would do, in turn."""
    if item not in f.options:
        raise RuntimeError(f"{item!r} is not an option of {f.label[:60]!r}")
    option = scope.locator(f"[data-sortie-opt='{f.key}-{f.options.index(item)}']").first
    option_id = await option.get_attribute("id")
    label = scope.locator(f"label[for='{option_id}']") if option_id else option.locator("xpath=ancestor::label[1]")

    async def press_space():
        await option.focus()
        await option.press("Space")

    attempts = [lambda: label.first.click(timeout=5000),
                lambda: option.click(force=True, timeout=5000),
                press_space,
                lambda: option.evaluate("e => e.click()")]
    for attempt in attempts:
        if await option.is_checked():
            return  # already selected (e.g. by the previous attempt): clicking again would untick a checkbox
        try:
            await attempt()
        except Exception:
            continue
        await _pause(scope, SETTLE_MS)
        if await option.is_checked():
            return
    raise RuntimeError(f"couldn't select {item!r} for {f.label[:60]!r}")


async def fill_step(scope: Scope, answerer: Answerer, job: Job, answers: dict[str, Answer],
                    fill_optional: frozenset[str] = frozenset()) -> tuple[list[Field], Question | None]:
    """Answer every empty, non-optional field on the current step. Returns the fields seen and the
    first question nobody could answer (None when all were filled)."""
    fields = await read_fields(scope)
    filled: dict[str, tuple[Field, str | list[str]]] = {}
    for f in fields:
        if f.filled or (f.optional and f.kind not in fill_optional):  # e.g. CutShort's optional note to the recruiter
            continue
        q = to_question(f)
        q.context = {k: ", ".join(v.value) if isinstance(v.value, list) else str(v.value) for k, v in answers.items()}
        if f.kind == "consent":
            if not f.required:
                continue  # optional extras (newsletters, talent pools) stay unticked
            answer = Answer(f.options[0], "rule") if f.options and AGREE.fullmatch(f.options[0]) else None
        else:
            answer = await answerer.answer(q, job)
        if answer is None:
            return fields, q
        await fill_field(scope, f, answer.value)
        answers[q.text] = answer
        filled[f.label] = (f, answer.value)

    # re-read the step: a choice the form quietly reset (React) gets one more try, then it's an error
    if filled:
        await _pause(scope, SETTLE_MS)
        for now in await read_fields(scope):  # by label: the form may have re-created the elements
            if now.label in filled and not now.filled:
                await fill_field(scope, now, filled[now.label][1])
                await _pause(scope, SETTLE_MS)
        lost = [f.label[:60] for f in await read_fields(scope) if f.label in filled and not f.filled]
        if lost:
            raise RuntimeError(f"the form kept clearing the answer to {lost[0]!r}")
    return fields, None


# ---- LLM step navigator: picks the next move on a step the rules don't recognise ----

STEP_JS = """root => {
  const visible = e => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const headings = [...root.querySelectorAll('h1, h2, h3, legend')].filter(visible)
    .map(e => clean(e.innerText)).filter(Boolean).slice(0, 8);
  const buttons = [];
  [...root.querySelectorAll('button, [role=button], input[type=submit]')].filter(visible).forEach((b, i) => {
    const text = clean(b.innerText || b.value || b.getAttribute('aria-label'));
    if (!text) return;
    b.dataset.sortieBtn = 'b' + i;
    buttons.push({key: 'b' + i, text: text.slice(0, 80),
                  disabled: !!b.disabled || b.getAttribute('aria-disabled') === 'true'});
  });
  return {headings, buttons, text: clean(root.innerText).slice(0, 1500)};
}"""
MAX_LLM_MOVES = 3
# never let the LLM press these: Submit stays rule-based behind confirm(); the rest leave or undo the form
FORBIDDEN_BUTTON = re.compile(
    r"submit|save|close|exit|back|return|cancel|report|apply|withdraw|delete|remove|upload|edit"
    r"|sign ?out|log ?out", re.I)

NAV_SYSTEM = """You help a job applicant's assistant move through a multi-step online application form.
You see ONE step: its headings, text, fields (with whether each is filled) and buttons (key, label, disabled).
Field answers are filled by someone else; never answer questions yourself.
Pick exactly one next move:
- {"action": "click", "button": "<key>", "why": "..."}: press the button that moves to the next step
  (e.g. Continue, Next, Proceed, Review, Skip for an optional step).
- {"action": "check", "field": "<key>", "why": "..."}: tick a REQUIRED acknowledgement/consent box
  (privacy notice, terms). Never marketing, newsletters or talent-pool opt-ins.
- {"action": "skip_job", "why": "..."}: the page says the applicant isn't eligible, the job is closed,
  or the form can't be completed.
- {"action": "wait", "why": "..."}: the step is still loading.
Never press buttons that submit the application, save and close, go back, leave, cancel, edit, upload or report.
Reply with only the JSON object."""


@dataclass
class StepMove:
    action: str  # click | check | skip_job | wait
    target: str = ""  # button key, or field key for check
    label: str = ""  # button / field label, for the log
    why: str = ""


def step_prompt(snapshot: dict, fields: list[Field], job: Job) -> str:
    lines = [f"Job: {job.title} at {job.company}"]
    if snapshot.get("headings"):
        lines.append("Headings: " + " | ".join(snapshot["headings"]))
    lines.append("Fields:" if fields else "Fields: none")
    for f in fields:
        state = "filled" if f.filled else ("empty, required" if f.required else "empty")
        options = f" options: {', '.join(f.options)}" if f.options else ""
        lines.append(f"  [{f.key}] {f.kind} \"{f.label[:150]}\" ({state}){options}")
    lines.append("Buttons:")
    for b in snapshot.get("buttons", []):
        lines.append(f"  [{b['key']}] \"{b['text']}\"" + (" (disabled)" if b["disabled"] else ""))
    if snapshot.get("errors"):
        lines.append("Errors shown: " + "; ".join(snapshot["errors"]))
    lines.append(f"Page text: {snapshot.get('text', '')}")
    return "\n".join(lines)


def validate_move(reply: dict, snapshot: dict, fields: list[Field]) -> StepMove | None:
    """The LLM's move, if it's allowed; None otherwise (unknown key, forbidden button, bad shape)."""
    action, why = str(reply.get("action", "")).lower(), str(reply.get("why", ""))[:200]
    if action == "click":
        button = next((b for b in snapshot.get("buttons", []) if b["key"] == reply.get("button")), None)
        if button and not button["disabled"] and not FORBIDDEN_BUTTON.search(button["text"]):
            return StepMove("click", button["key"], button["text"], why)
    elif action == "check":
        field_ = next((f for f in fields if f.key == reply.get("field")), None)
        if field_ and field_.kind == "consent" and not field_.filled and field_.options:
            return StepMove("check", field_.key, field_.label[:80], why)
    elif action in ("skip_job", "wait"):
        return StepMove(action, why=why)
    return None


async def llm_step_move(llm, scope: Scope, job: Job, errors: list[str], avoid: str = "") -> StepMove | None:
    """One LLM-chosen move on the step. `avoid` is a button label that was already pressed without effect."""
    if not llm.enabled:
        return None
    fields = await read_fields(scope)
    snapshot = await _root(scope).evaluate(STEP_JS)
    snapshot["errors"] = errors
    if avoid:  # pressing it again can't help; don't offer it
        snapshot["buttons"] = [b for b in snapshot["buttons"] if b["text"] != avoid]
    try:
        reply = await llm.complete_json(NAV_SYSTEM, step_prompt(snapshot, fields, job))
    except Exception:  # LLM down / bad reply: no move, the caller fails the step as before
        return None
    move = validate_move(reply, snapshot, fields)
    if move and move.action == "check":
        f = next(f for f in fields if f.key == move.target)
        await fill_field(scope, f, f.options[0])
    elif move and move.action == "click":
        await scope.locator(f"[data-sortie-btn='{move.target}']").first.click(timeout=10000)
    return move


# ---- things only you can do: voice / video recordings, file uploads ----

NEEDS_YOU_JS = """root => {
  const visible = e => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  const files = [...root.querySelectorAll('input[type=file]')];
  const media = files.find(f => /audio|video/i.test(f.accept || ''));
  if (media) return /video/i.test(media.accept) ? 'video recording' : 'voice recording';
  const text = (root.innerText || '').toLowerCase();
  const recorder = [...root.querySelectorAll('button, [role=button]')].filter(visible)
    .some(b => /\\b(start |begin )?record(ing)?\\b|\\bmic(rophone)?\\b/i.test(b.innerText || b.getAttribute('aria-label') || ''));
  if (recorder || /record (a |an |your )?(voice|audio|video)|voice (note|answer|recording)|video (answer|recording)/.test(text))
    return /video/.test(text) ? 'video recording' : 'voice recording';
  const upload = files.find(f => f.required || visible(f.closest('label, div') || f));
  if (upload && !/resume|cv\\b/i.test((upload.closest('label, div, fieldset')?.innerText || '') + ' ' + (upload.accept || '')))
    return 'file upload';
  return null;
}"""


async def needs_you_reason(scope: Scope) -> str | None:
    """'voice recording' / 'video recording' / 'file upload' when the step asks for something only you can give."""
    return await _root(scope).evaluate(NEEDS_YOU_JS)


async def find_button(scope: Scope, label: re.Pattern):
    """The visible button whose whole label matches (so "Review your application" isn't "View full job description")."""
    buttons = scope.locator("button:visible")
    for i in range(await buttons.count()):
        if label.fullmatch((await buttons.nth(i).inner_text()).strip()):
            return buttons.nth(i)
    return None
