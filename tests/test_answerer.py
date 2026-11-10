from datetime import date

import pytest

from sortie.llm.answer_book import AnswerBook
from sortie.llm.answerer import UNKNOWN_SKILL, Answerer, find_skill, pick_option, profile_answer
from sortie.models import Job, Question, QuestionKind as K

TODAY = date(2026, 11, 5)
JOB = Job(site="naukri", site_job_id="1", url="u", title="Java Developer", company="Acme", locations=["Bengaluru"])
ABROAD_JOB = Job(site="linkedin", site_job_id="2", url="u", title="Java Developer", company="Acme", locations=["Dubai, UAE"])


def ans(profile, text, kind=K.TEXT, options=(), job=JOB):
    a = profile_answer(profile, Question(text, kind, list(options)), job, TODAY)
    return a.value if hasattr(a, "value") else a


def test_pick_option():
    opts = ["0-1 years", "1-3 years", "3-5 years", "5+ years"]
    assert pick_option("3.1", opts) == "3-5 years"
    assert pick_option("1", opts) == "1-3 years"
    assert pick_option("7", opts) == "5+ years"
    assert pick_option("yes", ["Yes", "No"]) == "Yes"
    assert pick_option("Bangalore", ["Bangalore/Bengaluru", "Jaipur"]) == "Bangalore/Bengaluru"
    assert pick_option("Chennai", ["Jaipur"]) is None


def test_find_skill(profile):
    assert find_skill(profile, "years of experience in spring boot?") == "Spring Boot"
    assert find_skill(profile, "experience with Google Cloud") == "GCP"
    assert find_skill(profile, "javascript experience") == "JavaScript"
    assert find_skill(profile, "java experience") == "Java"
    assert find_skill(profile, "node.js") == "Node.js"
    assert find_skill(profile, "rust experience") is None


def test_experience(profile):
    assert ans(profile, "How many years of experience do you have in Java?", K.NUMBER) == "3"
    assert ans(profile, "Years of experience in Spring Boot", K.DECIMAL) == "3.4"
    assert ans(profile, "Experience with Golang?", K.NUMBER) == "1"
    assert ans(profile, "Experience in Python", K.NUMBER) == "0"
    assert ans(profile, "Total years of IT experience?", K.NUMBER) == "3"
    assert ans(profile, "Experience in Kafka", K.CHOICE, ["0-1", "1-2", "2-3", "3+"]) == "3+"
    assert ans(profile, "Experience in Java") == "3 years 5 months"
    assert ans(profile, "Do you have experience with Docker?", K.CHOICE, ["Yes", "No"]) == "Yes"
    assert ans(profile, "Do you have experience with Python?", K.CHOICE, ["Yes", "No"]) == "No"
    assert ans(profile, "Years of experience in Rust", K.NUMBER) is UNKNOWN_SKILL
    assert ans(profile, "Do you have experience integrating payment gateways?", K.CHOICE, ["Yes", "No"]) is None


def test_money_and_notice(profile):
    assert ans(profile, "What is your current CTC (in lakhs)?", K.DECIMAL) == "9.5"
    assert ans(profile, "Current CTC in INR", K.NUMBER) == "950000"
    assert ans(profile, "Expected CTC (LPA)", K.NUMBER) == "15"
    assert ans(profile, "What is your expected salary?") == "13-17 LPA"
    assert ans(profile, "Expected CTC", K.CHOICE, ["10-15 LPA", "15-20 LPA", "20+ LPA"]) == "15-20 LPA"
    assert ans(profile, "What is your notice period?", K.CHOICE, ["Immediate", "15 days", "1 month", "2 months"]) == "1 month"
    assert ans(profile, "Notice period in days", K.NUMBER) == "30"


def test_location_relocation_authorization(profile):
    assert ans(profile, "What is your current location?") == "Chennai"
    assert ans(profile, "Are you willing to relocate?", K.CHOICE, ["Yes", "No"]) == "Yes"
    assert ans(profile, "Will you require visa sponsorship?", K.CHOICE, ["Yes", "No"]) == "No"
    assert ans(profile, "Will you require visa sponsorship?", K.CHOICE, ["Yes", "No"], job=ABROAD_JOB) == "Yes"
    assert ans(profile, "Are you legally authorized to work in the UAE?", K.CHOICE, ["Yes", "No"]) == "No"


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.model, self.calls = reply, "fake", 0

    @property
    def enabled(self):
        return True

    async def complete_json(self, system, user):
        self.calls += 1
        return self.reply


async def test_flow_llm_then_user_then_cache(profile, tmp_path):
    book = AnswerBook(tmp_path / "answers.yaml")
    asked = []

    async def ask(q, job, suggestion):
        asked.append((q.text, suggestion.value if suggestion else None))
        return "No"

    confident = Answerer(profile, book, FakeLLM({"answer": "Yes", "confidence": 0.9}), ask)
    q = Question("Do you have experience integrating payment gateways?", K.CHOICE, ["Yes", "No"])
    assert (await confident.answer(q, JOB)).source == "llm"
    assert asked == []

    unsure = Answerer(profile, book, FakeLLM({"answer": "Yes", "confidence": 0.3}), ask)
    q2 = Question("Are you comfortable with night shifts?", K.CHOICE, ["Yes", "No"])
    a = await unsure.answer(q2, JOB)
    assert (a.value, a.source) == ("No", "user")
    assert asked == [("Are you comfortable with night shifts?", "Yes")]  # LLM guess shown as suggestion

    # second time comes from the cache without asking
    n = len(asked)
    a = await unsure.answer(q2, JOB)
    assert (a.value, a.source) == ("No", "your answers") and len(asked) == n


async def test_unknown_skill_inferred_by_llm_and_capped(profile, tmp_path):
    book = AnswerBook(tmp_path / "answers.yaml")
    q = Question("Years of experience in JUnit", K.NUMBER)
    sure = Answerer(profile, book, FakeLLM({"answer": "5", "confidence": 0.8, "based_on": "Java backend work"}), None)
    a = await sure.answer(q, JOB)
    assert (a.value, a.source) == (str(profile.total_experience().years), "llm")  # 5 capped to total

    fairly_unsure = Answerer(profile, book, FakeLLM({"answer": "2", "confidence": 0.4}), None)
    assert (await fairly_unsure.answer(q, JOB)).value == "2"


async def test_unknown_skill_goes_to_user_when_llm_not_sure_at_all(profile, tmp_path):
    asked = []

    async def ask(q, job, suggestion):
        asked.append(suggestion.value if suggestion else None)
        return "0"

    llm = FakeLLM({"answer": "1", "confidence": 0.1})
    a = Answerer(profile, AnswerBook(tmp_path / "answers.yaml"), llm, ask)
    result = await a.answer(Question("Years of experience in Rust", K.NUMBER), JOB)
    assert (result.value, result.source) == ("0", "user") and asked == ["1"] and llm.calls == 1


async def test_sensitive_llm_answer_needs_user(profile, tmp_path):
    llm = FakeLLM({"answer": "No", "confidence": 1.0})
    a = Answerer(profile, AnswerBook(tmp_path / "answers.yaml"), llm, None)
    q = Question("Have you ever been convicted of a criminal offence?", K.CHOICE, ["Yes", "No"])
    assert await a.answer(q, JOB) is None


def test_resume_skills_and_aliases(profile):
    assert ans(profile, "How many years of experience do you have in Rest?", K.NUMBER) == "3"
    assert ans(profile, "Experience with RESTful APIs", K.NUMBER) == "3"
    assert ans(profile, "Years of experience in JPA", K.NUMBER) == "3"
    assert ans(profile, "Experience in Data Structures and Algorithms", K.NUMBER) == "3"
    assert ans(profile, "How many years of experience in event driven architecture?", K.NUMBER) == "3"


def test_answer_book_file(tmp_path):
    path = tmp_path / "answers.yaml"
    book = AnswerBook(path)
    book.add("Are you open to night shifts?", "No", ["Yes", "No"])
    path.write_text(path.read_text() + "\n# my own note\n- question: Preferred interview time?\n  answer: After 6 PM\n")
    book = AnswerBook(path)  # reload picks up hand edits
    assert book.lookup("are you open to NIGHT shifts") == "No"
    assert book.lookup("Preferred interview time?") == "After 6 PM"
    book.add("Do you have a passport?", "Yes")
    text = path.read_text()
    assert "# my own note" in text and "Do you have a passport?" in text
    assert "Preferred interview time?" in book.as_prompt()


async def test_reworded_question_answered_from_book(profile, tmp_path):
    book = AnswerBook(tmp_path / "answers.yaml")
    book.add("Are you available for a interview?(Virtual)", "Yes", ["Yes", "No"])
    llm = FakeLLM({"answer": "Yes", "confidence": 0.9, "from_previous_answer": True})
    a = Answerer(profile, book, llm, None)
    q = Question("Can you attend a virtual interview this week?", K.CHOICE, ["Yes", "No"])
    assert (await a.answer(q, JOB)).value == "Yes"

    # sensitive question: allowed only when the LLM reuses your own answer
    sensitive = Question("Have you ever been convicted of a criminal offence?", K.CHOICE, ["Yes", "No"])
    assert await Answerer(profile, book, FakeLLM({"answer": "No", "confidence": 1.0}), None).answer(sensitive, JOB) is None
    book.add("Any criminal record?", "No", ["Yes", "No"])
    reuse = FakeLLM({"answer": "No", "confidence": 0.95, "from_previous_answer": True})
    assert (await Answerer(profile, book, reuse, None).answer(sensitive, JOB)).value == "No"


def test_country_and_education_come_from_profile(profile):
    sg = profile.model_copy(update={"work_authorization": {"india": True, "singapore": False}})
    sg_job = Job(site="x", site_job_id="9", url="u", title="Dev", company="Acme", locations=["Singapore"])
    assert ans(sg, "Will you require visa sponsorship?", K.CHOICE, ["Yes", "No"], job=sg_job) == "Yes"
    assert ans(sg, "Will you require visa sponsorship?", K.CHOICE, ["Yes", "No"]) == "No"
    assert ans(profile, "Highest qualification?", K.CHOICE, ["B.Tech", "B.Sc", "MCA"]) == "B.Sc"


def test_questionnaire_style_questions(profile):
    from sortie.llm.answerer import ASK_USER, question_kind
    assert ans(profile, "Your total full-time YOE (exclude internships).", K.NUMBER) == "3"
    assert ans(profile, "Are you serving notice period?", K.CHOICE, ["Yes", "No", "Completed"]) is ASK_USER
    offer = "Share details about any offer you might hold - CTC, location, company type, etc."
    assert question_kind(offer, [], False) == K.TEXT
    assert ans(profile, offer) is ASK_USER
    assert question_kind("This job is based out of Jaipur, 5 days WFO. Comfortable?", [], False) == K.TEXT
    assert question_kind("Mention your current CTC.", [], False) == K.DECIMAL


def test_load_about_strips_template_comments(tmp_path):
    from sortie.config import CONFIG_DIR, load_about
    assert load_about(tmp_path / "missing.md") == ""
    notes = tmp_path / "about.md"
    notes.write_text("<!-- instructions -->\n## Projects\n- Built a Kafka pipeline\n")
    assert load_about(notes) == "## Projects\n- Built a Kafka pipeline"
    assert "Order tracking service" in load_about(CONFIG_DIR / "about.example.md")
    copy = tmp_path / "copy" / "about.md"
    copy.parent.mkdir()
    (copy.parent / "about.example.md").write_text((CONFIG_DIR / "about.example.md").read_text())
    copy.write_text((CONFIG_DIR / "about.example.md").read_text())
    assert load_about(copy) == ""  # an unedited copy of the template is ignored


class RecordingLLM(FakeLLM):
    async def complete_json(self, system, user):
        self.prompt = user
        return await super().complete_json(system, user)


async def test_about_only_sent_when_needed(profile, tmp_path):
    notes = "## Projects\n- Search service on Elasticsearch, 2024"
    book = AnswerBook(tmp_path / "answers.yaml")

    open_q = RecordingLLM({"answer": "I built a search service on Elasticsearch.", "confidence": 0.9})
    await Answerer(profile, book, open_q, None, about=notes).answer(Question("Describe a project you're proud of"), JOB)
    assert notes in open_q.prompt

    skill = RecordingLLM({"answer": "1", "confidence": 0.8})
    await Answerer(profile, book, skill, None, about=notes).answer(
        Question("Years of experience in Elasticsearch", K.NUMBER), JOB)
    assert notes in skill.prompt

    yes_no = RecordingLLM({"answer": "Yes", "confidence": 0.9})
    await Answerer(profile, book, yes_no, None, about=notes).answer(
        Question("Can you attend a virtual interview?", K.CHOICE, ["Yes", "No"]), JOB)
    assert notes not in yes_no.prompt


def test_plain_answer_strips_json_fields():
    from sortie.llm.answerer import plain_answer
    assert plain_answer("3 years 5 months, confidence: 0.9, from_previous_answer: false") == "3 years 5 months"
    assert plain_answer('answer: "Yes", confidence: 0.8') == "Yes"
    assert plain_answer("{answer: 2 years, based_on: Java}") == "2 years"
    assert plain_answer("Bengaluru") == "Bengaluru"


def test_exp_abbreviation_uses_profile(profile):
    from sortie.llm.answerer import profile_answer
    from sortie.models import Job, Question, QuestionKind
    job = Job("indeed", "1", "u", "Dev", "Acme")
    total = profile_answer(profile, Question("total exp", QuestionKind.NUMBER), job)
    assert total.source == "profile"
    java = profile_answer(profile, Question("Exp In java?", QuestionKind.TEXT), job)
    assert java.source == "profile"


def test_date_format_from_placeholder():
    from sortie.llm.answerer import date_format
    assert date_format("DD/MM/YYYY") == "%d/%m/%Y"
    assert date_format("mm-dd-yyyy") == "%m-%d-%Y"
    assert date_format("Search to select an option") is None and date_format("") is None


def test_start_date_question_uses_notice_period(profile):
    from datetime import date, timedelta
    from sortie.llm.answerer import profile_answer
    from sortie.models import Job, Question, QuestionKind
    q = Question("If your application is successful, when would you be able to start employment?",
                 QuestionKind.DATE, date_format="%d/%m/%Y")
    answer = profile_answer(profile, q, Job("indeed", "1", "u", "Dev", "Acme"), today=date(2026, 11, 7))
    assert answer.value == (date(2026, 11, 7) + timedelta(days=profile.notice_period_days)).strftime("%d/%m/%Y")


def test_date_answers_must_be_dates():
    from sortie.llm.answerer import _date_value
    assert _date_value("07/12/2026", "%d/%m/%Y") == "07/12/2026"
    assert _date_value("30 days from offer acceptance", "%d/%m/%Y") is None


async def test_llm_sees_earlier_answers_on_the_form(profile, tmp_path):
    class Capture(FakeLLM):
        async def complete_json(self, system, user):
            self.user = user
            return await super().complete_json(system, user)
    llm = Capture({"answer": "Not applicable", "confidence": 0.9})
    a = Answerer(profile, AnswerBook(tmp_path / "answers.yaml"), llm, None)
    q = Question("If Yes, please provide further details here", K.TEXT,
                 context={"Is any family member employed by a government agency?": "No"})
    assert (await a.answer(q, JOB)).value == "Not applicable"
    assert "EARLIER ANSWERS ON THIS APPLICATION:\n- Is any family member employed by a government agency? → No" in llm.user


async def test_llm_date_answer_must_be_a_date(profile, tmp_path):
    a = Answerer(profile, AnswerBook(tmp_path / "answers.yaml"),
                 FakeLLM({"answer": "after my notice period", "confidence": 0.9}), None)
    q = Question("Date of last appraisal", K.DATE, date_format="%d/%m/%Y")
    assert await a.answer(q, JOB) is None


def test_relocation_with_descriptive_options(profile):
    options = ["Can Relocate All Over India", "Can Only Relocate to Selected Cities", "Not Willing to Relocate"]
    want = options[0] if profile.willing_to_relocate else options[2]
    assert ans(profile, "Are you willing to relocate? (Our offices are in Jaipur, Kolkata)", K.CHOICE, options) == want


def test_notice_period_with_conditional_serving(profile):
    days = profile.notice_period_days
    assert ans(profile, "What is your notice period? please mention your LWD If serving", K.TEXT) == f"{days} days"


def test_serving_notice_from_profile(profile):
    from datetime import date as d
    yn = ["Yes", "No"]
    not_serving = profile.model_copy(update={"serving_notice": False, "last_working_day": None})
    assert ans(not_serving, "Are you currently serving your notice period?", K.CHOICE, yn) == "No"
    assert ans(not_serving, "What is your last working day?") == "Not applicable"
    assert ans(not_serving, "What is your notice period? please mention your LWD If serving") == \
        f"{not_serving.notice_period_days} days"

    serving = profile.model_copy(update={"serving_notice": True, "last_working_day": d(2026, 12, 15)})
    assert ans(serving, "Are you currently serving your notice period?", K.CHOICE, yn) == "Yes"
    assert ans(serving, "What is your notice period? please mention your LWD If serving") == \
        "Serving notice, last working day 15 Dec 2026"
    lwd = Question("Last working day", K.DATE, date_format="%d/%m/%Y")
    assert profile_answer(serving, lwd, JOB, TODAY).value == "15/12/2026"
    start = Question("When can you start?", K.DATE, date_format="%d/%m/%Y")
    assert profile_answer(serving, start, JOB, TODAY).value == "16/12/2026"
    days = (d(2026, 12, 15) - TODAY).days + 1
    assert ans(serving, "Notice period in days", K.NUMBER) == str(days)
