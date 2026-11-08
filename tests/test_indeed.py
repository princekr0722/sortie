import json
from pathlib import Path

from sortie.models import QuestionKind
from sortie.sites.forms import Field, to_question
from sortie.sites.indeed import card_to_job, html_to_text, search_targets, search_url

CARDS = json.loads((Path(__file__).parent / "fixtures" / "indeed_cards.json").read_text())


def test_search_url():
    assert search_url("in.indeed.com", "Python Backend Developer", "Bengaluru") == (
        "https://in.indeed.com/jobs?q=Python+Backend+Developer&l=Bengaluru&fromage=7&iafilter=1")
    assert search_url("ae.indeed.com", "Java Developer", "", 10).endswith("q=Java+Developer&l=&fromage=7&iafilter=1&start=10")


def test_search_targets_map_countries_to_their_site():
    assert search_targets(["Bengaluru", "UAE", "Jaipur"]) == [
        ("Bengaluru", "in.indeed.com", "Bengaluru"),
        ("UAE", "ae.indeed.com", ""),
        ("Jaipur", "in.indeed.com", "Jaipur"),
    ]


def test_card_to_job():
    easy, external, _ = (card_to_job(c, "in.indeed.com") for c in CARDS)
    assert (easy.site, easy.site_job_id, easy.company) == ("indeed", "a1b2c3d4e5f60001", "Globex")
    assert easy.url == "https://in.indeed.com/viewjob?jk=a1b2c3d4e5f60001"
    assert easy.locations == ["Chennai, Tamil Nadu"] and not easy.external
    assert easy.description == "- Build backend services & APIs.\n- Design microservices."
    assert external.external and external.locations == ["Kolkata, West Bengal", "Remote"]
    assert external.salary.startswith("₹12,00,000")
    assert card_to_job({"displayTitle": "no key"}, "in.indeed.com") is None


def test_html_to_text():
    assert html_to_text("<p>Hello&nbsp;<b>there</b></p><div>Line  two<br>three</div>") == "Hello there\nLine two\nthree"


def test_form_fields_become_questions():
    years = to_question(Field("f1", "number", "How many years of experience do you have with Java? *", required=True))
    assert (years.text, years.kind) == ("How many years of experience do you have with Java?", QuestionKind.NUMBER)
    choice = to_question(Field("f2", "radio", "Are you willing to relocate?", ["Yes", "No"]))
    assert choice.kind == QuestionKind.CHOICE and choice.options == ["Yes", "No"]
    multi = to_question(Field("f3", "checkbox", "Which of these have you used?", ["Kafka", "Redis"]))
    assert multi.kind == QuestionKind.MULTI_CHOICE
    ctc = to_question(Field("f4", "text", "Expected CTC (in LPA)"))
    assert ctc.kind == QuestionKind.DECIMAL
    assert Field("f5", "text", "Cover letter (optional)").optional
    assert not Field("f6", "text", "Cover letter (optional)", required=True).optional


def test_recaptcha_footer_is_not_a_block():
    from sortie.sites.base import BLOCK_PATTERNS, CAPTCHA_FOOTER
    footer = "Save and close 29% This site is protected by reCAPTCHA and the Google Privacy Policy"
    assert BLOCK_PATTERNS.search(footer) and not BLOCK_PATTERNS.search(CAPTCHA_FOOTER.sub("", footer))
    assert BLOCK_PATTERNS.search(CAPTCHA_FOOTER.sub("", "Please complete the captcha to continue"))


def test_review_page_text_is_not_success():
    from sortie.sites.indeed import NEXT_BUTTON, SUBMIT_BUTTON, SUBMITTED_TEXT
    review = ("Review your application\nYou will not be able to edit your application after you submit.\n"
              "Contact information\nSubmit your application")
    assert not SUBMITTED_TEXT.search(review)
    assert SUBMITTED_TEXT.search("Your application has been submitted!\nYou will get an email confirmation")
    assert all(NEXT_BUTTON.fullmatch(b) for b in ["Continue", "Review your application"])
    assert SUBMIT_BUTTON.fullmatch("Submit your application") and not SUBMIT_BUTTON.fullmatch("Review your application")
    assert not any(NEXT_BUTTON.fullmatch(b) or SUBMIT_BUTTON.fullmatch(b)
                   for b in ["View full job description", "Report an issue", "Save and close"])


def test_unmet_requirements_step():
    from sortie.sites.indeed import UNMET_TEXT, unmet_reason
    body = ("It looks like you don’t meet these employer requirements\nYou may not hear back from the employer.\n"
            "Kafka: 3 years (Required)\nReturn to job search\nApply anyway")
    assert UNMET_TEXT.search(body)
    assert unmet_reason(body) == "Indeed: doesn't meet employer requirements (Kafka: 3 years)"


def test_consent_checkbox():
    from sortie.sites.forms import AGREE
    q = to_question(Field("f7", "consent", "You declare that you have read the privacy notice of Acme. * required",
                          ["Agree"], required=True))
    assert q.text == "You declare that you have read the privacy notice of Acme." and q.options == ["Agree"]
    assert all(AGREE.fullmatch(x) for x in ["Agree", "I agree", "I acknowledge", "Accept"])
    assert not any(AGREE.fullmatch(x) for x in ["Subscribe to job alerts", "Yes, contact me about other roles"])


def test_date_field_becomes_date_question():
    q = to_question(Field("f9", "text", "When would you be able to start employment? *", required=True,
                          placeholder="DD/MM/YYYY"))
    assert q.kind == QuestionKind.DATE and q.date_format == "%d/%m/%Y"
    assert to_question(Field("f10", "date", "Available from")).date_format == "%Y-%m-%d"
