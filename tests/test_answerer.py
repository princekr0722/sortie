from datetime import date

import pytest

from sortie.llm.answer_book import AnswerBook
from sortie.llm.answerer import UNKNOWN_SKILL, Answerer, find_skill, pick_option, profile_answer
from sortie.models import Job, Question, QuestionKind as K

TODAY = date(2026, 11, 5)
JOB = Job(site="naukri", site_job_id="1", url="u", title="Java Developer", company="Acme", locations=["Bengaluru"])
PH_JOB = Job(site="linkedin", site_job_id="2", url="u", title="Java Developer", company="Acme", locations=["Makati, Philippines"])


def ans(profile, text, kind=K.TEXT, options=(), job=JOB):
    a = profile_answer(profile, Question(text, kind, list(options)), job, TODAY)
    return a.value if hasattr(a, "value") else a


def test_pick_option():
    opts = ["0-1 years", "1-3 years", "3-5 years", "5+ years"]
    assert pick_option("3.1", opts) == "3-5 years"
    assert pick_option("1", opts) == "1-3 years"
    assert pick_option("7", opts) == "5+ years"
    assert pick_option("yes", ["Yes", "No"]) == "Yes"
    assert pick_option("Bangalore", ["Bangalore/Bengaluru", "Pune"]) == "Bangalore/Bengaluru"
    assert pick_option("Chennai", ["Pune"]) is None


def test_find_skill(profile):
    assert find_skill(profile, "years of experience in spring boot?") == "Spring Boot"
    assert find_skill(profile, "experience with Google Cloud") == "GCP"
    assert find_skill(profile, "javascript experience") == "JavaScript"
    assert find_skill(profile, "java experience") == "Java"
    assert find_skill(profile, "node.js") == "Node.js"
    assert find_skill(profile, "rust experience") is None


def test_experience(profile):
    assert ans(profile, "How many years of experience do you have in Java?", K.NUMBER) == "3"
    assert ans(profile, "Years of experience in Spring Boot", K.DECIMAL) == "3.1"
    assert ans(profile, "Experience with Golang?", K.NUMBER) == "1"
    assert ans(profile, "Experience in Python", K.NUMBER) == "0"
    assert ans(profile, "Total years of IT experience?", K.NUMBER) == "3"
    assert ans(profile, "Experience in Kafka", K.CHOICE, ["0-1", "1-2", "2-3", "3+"]) == "3+"
    assert ans(profile, "Experience in Java") == "3 years 2 months"
    assert ans(profile, "Do you have experience with Docker?", K.CHOICE, ["Yes", "No"]) == "Yes"
    assert ans(profile, "Do you have experience with Python?", K.CHOICE, ["Yes", "No"]) == "No"
    assert ans(profile, "Years of experience in Rust", K.NUMBER) is UNKNOWN_SKILL
    assert ans(profile, "Do you have experience integrating payment gateways?", K.CHOICE, ["Yes", "No"]) is None


def test_money_and_notice(profile):
    assert ans(profile, "What is your current CTC (in lakhs)?", K.DECIMAL) == "11.2"
    assert ans(profile, "Current CTC in INR", K.NUMBER) == "1120000"
    assert ans(profile, "Expected CTC (LPA)", K.NUMBER) == "18"
    assert ans(profile, "What is your expected salary?") == "16-20 LPA"
    assert ans(profile, "Expected CTC", K.CHOICE, ["10-15 LPA", "15-20 LPA", "20+ LPA"]) == "15-20 LPA"
    assert ans(profile, "What is your notice period?", K.CHOICE, ["Immediate", "15 days", "1 month", "2 months"]) == "1 month"
    assert ans(profile, "Notice period in days", K.NUMBER) == "30"


def test_location_relocation_authorization(profile):
    assert ans(profile, "What is your current location?") == "Bengaluru"
    assert ans(profile, "Are you willing to relocate?", K.CHOICE, ["Yes", "No"]) == "Yes"
    assert ans(profile, "Will you require visa sponsorship?", K.CHOICE, ["Yes", "No"]) == "No"
    assert ans(profile, "Will you require visa sponsorship?", K.CHOICE, ["Yes", "No"], job=PH_JOB) == "Yes"
    assert ans(profile, "Are you legally authorized to work in the Philippines?", K.CHOICE, ["Yes", "No"]) == "No"


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
    assert ans(profile, "Highest qualification?", K.CHOICE, ["B.Tech", "BCA", "MCA"]) == "BCA"


def test_questionnaire_style_questions(profile):
    from sortie.llm.answerer import ASK_USER, question_kind
    assert ans(profile, "Your total full-time YOE (exclude internships).", K.NUMBER) == "3"
    assert ans(profile, "Are you serving notice period?", K.CHOICE, ["Yes", "No", "Completed"]) is ASK_USER
    offer = "Share details about any offer you might hold - CTC, location, company type, etc."
    assert question_kind(offer, [], False) == K.TEXT
    assert ans(profile, offer) is ASK_USER
    assert question_kind("This job is based out of Pune, 5 days WFO. Comfortable?", [], False) == K.TEXT
    assert question_kind("Mention your current CTC.", [], False) == K.DECIMAL
