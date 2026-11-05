from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from sortie.models import QuestionKind
from sortie.sites.naukri import CARD_JS, SELECTORS, card_to_job, question_kind, search_url, split_options

FIXTURE = Path(__file__).parent / "fixtures" / "naukri_search.html"


def test_search_url():
    assert search_url("Python Backend Developer", "bangalore", 3) == (
        "https://www.naukri.com/python-backend-developer-jobs-in-bangalore"
        "?k=python%20backend%20developer&l=bangalore&experience=3&jobAge=7")
    assert "-jobs-in-jaipur-2?" in search_url("SWE Backend", "jaipur", 3, page=2)


def test_card_to_job():
    job = card_to_job({"id": "1", "url": "u", "title": "Java Dev", "company": "Acme", "exp": "3-8 Yrs",
                       "salary": "Not disclosed", "location": "Hybrid - Kolkata, Jaipur, Bengaluru"})
    assert job.locations == ["Hybrid - Kolkata", "Jaipur", "Bengaluru"]
    assert job.salary == ""
    assert card_to_job({"id": "", "url": "u"}) is None


def test_split_options_from_one_container():
    texts = ["Bengaluru / Bangalore\nKolkata / Secunderabad\nChennai\nJaipur\nLoad more"]
    assert split_options(texts) == ["Bengaluru / Bangalore", "Kolkata / Secunderabad", "Chennai", "Jaipur"]
    assert split_options(["Yes", "No", "Yes"]) == ["Yes", "No"]


def test_question_kind():
    assert question_kind("What is your current CTC in lakhs?", [], False) == QuestionKind.DECIMAL
    assert question_kind("How many years of experience in Java?", [], False) == QuestionKind.NUMBER
    assert question_kind("Your LinkedIn URL", [], False) == QuestionKind.TEXT
    assert question_kind("Notice period", ["15 days", "1 month"], False) == QuestionKind.CHOICE
    assert question_kind("Skills", ["Java", "Go"], True) == QuestionKind.MULTI_CHOICE


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture missing")
async def test_parses_saved_search_page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=True)
        # the saved page's own scripts would fetch from naukri.com and re-render it
        page = await (await browser.new_context(java_script_enabled=False)).new_page()
        await page.set_content(FIXTURE.read_text(), wait_until="domcontentloaded")
        cards = await page.locator(SELECTORS["card"]).evaluate_all(CARD_JS)
        await browser.close()
    jobs = [card_to_job(c) for c in cards]
    assert len(jobs) == 20 and all(jobs)
    first = jobs[0]
    assert first.site_job_id == "120826501027"
    assert first.title == "Junior Python Backend Developer"
    assert first.company == "Wisdomleaf Technologies"
    assert first.experience == "3-8 Yrs" and first.locations == ["Bengaluru"]
    assert first.url.startswith("https://www.naukri.com/job-listings-")
