from sortie.sites.wellfound import _names, data_to_job, search_url


def test_search_url():
    assert search_url("Python Backend Developer", "Bengaluru") == "https://wellfound.com/role/l/python-backend-developer/bangalore"
    assert search_url("Backend Engineer", "Jaipur", 2) == "https://wellfound.com/role/l/backend-engineer/jaipur?page=2"
    assert search_url("Software Engineer", None) == "https://wellfound.com/role/r/software-engineer"


def test_names():
    assert _names("['Dubai', 'Bengaluru']") == ["Dubai", "Bengaluru"]
    assert _names(["Jaipur"]) == ["Jaipur"] and _names("Remote") == ["Remote"] and _names(None) == []


def test_data_to_job():
    item = {"id": "123", "title": "Backend Engineer", "slug": "backend-engineer", "company": "Acme",
            "description": "# About\\nWe build **APIs**.", "locations": "['Bengaluru', 'Bengaluru']", "remote": "True",
            "compensation": "₹15L – ₹25L", "jobType": "full-time", "expMin": "2", "expMax": "5", "ats": None}
    job = data_to_job(item)
    assert (job.site, job.site_job_id, job.company) == ("wellfound", "123", "Acme")
    assert job.url == "https://wellfound.com/jobs/123-backend-engineer"
    assert job.locations == ["Bengaluru", "Remote"] and job.experience == "2-5 Yrs" and job.salary == "₹15L – ₹25L"
    assert data_to_job({**item, "expMax": "None"}).experience == "2+ Yrs"
    assert data_to_job({"title": "no id"}) is None


def test_wellfound_is_a_regular_site():
    from sortie.sites import ADAPTERS, EXPERIMENTAL
    assert "wellfound" in ADAPTERS and "wellfound" not in EXPERIMENTAL


def test_unknown_role_redirect_detected():
    from sortie.sites.wellfound import is_role_page
    assert is_role_page("https://wellfound.com/role/l/backend-engineer/bangalore?page=2")
    assert not is_role_page("https://wellfound.com/location/bangalore")


async def test_radios_outside_a_fieldset_are_read_as_one_question():
    from playwright.async_api import async_playwright
    from sortie.sites.forms import read_fields
    # Wellfound's markup: a <label> (for= points nowhere) wraps the question and the Yes/No radios
    html = """<div role="dialog" aria-modal="true"><div class="mb-4"><label for="form-input--undefined">
      <div><div>Are you authorized to work in the United States?<span>*</span></div></div>
      <div data-test="RadioGroup-q1">
        <div><input id="q1-true" type="radio" name="q1" value="true"><label for="q1-true">Yes</label></div>
        <div><input id="q1-false" type="radio" name="q1" value="false"><label for="q1-false">No</label></div>
      </div></label></div>
      <label for="form-input--undefined"><div>Phone<span>*</span></div><div><input type="text" name="phone"></div></label></div>"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        fields = await read_fields(page.locator("[role=dialog]"))
        await browser.close()
    radio = [f for f in fields if f.kind == "radio"]
    assert len(radio) == 1 and radio[0].options == ["Yes", "No"] and radio[0].required and not radio[0].filled
    assert radio[0].label.startswith("Are you authorized to work in the United States?")
    assert [f.label for f in fields if f.kind == "text"] == ["Phone*"]


def test_remote_jobs_closed_to_your_country_are_dropped():
    from sortie.sites.wellfound import open_to
    yours = ["India", "Bengaluru", "Jaipur"]
    assert open_to({"remoteOnlyIn": [], "locations": ["Illinois"]}, yours)  # no restriction listed
    assert open_to({"remoteOnlyIn": ["India", "Egypt"], "locations": ["New Delhi"]}, yours)
    assert open_to({"remoteOnlyIn": ["Worldwide"]}, yours)
    assert not open_to({"remoteOnlyIn": ["United States"], "locations": ["Illinois"]}, yours)
    assert not open_to({"remoteOnlyIn": ["Romania", "Budapest, Hungary"], "locations": ["Romania"]}, yours)
    # remote only in the US, but also an office in one of your cities: left to the apply-time check
    assert open_to({"remoteOnlyIn": ["United States"], "locations": ["Bangalore Urban", "San Francisco"]}, yours)
    assert not open_to({"remoteOnlyIn": "['United States']", "locations": "['Indiana']"}, yours)  # no false "india" hit
