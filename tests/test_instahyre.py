import json
from pathlib import Path

from sortie.sites.instahyre import api_to_job, search_url

FIXTURE = Path(__file__).parent / "fixtures" / "instahyre_search.json"


def test_search_url():
    assert search_url("Java Backend Developer", 3, 35) == (
        "https://www.instahyre.com/api/v1/job_search"
        "?skills=java+backend+developer&years=3&company_size=0&job_type=0&offset=35")


def test_api_to_job():
    job = api_to_job({"id": 7, "candidate_title": "Java Developer", "employer": {"company_name": "Acme"},
                      "locations": "Bangalore,Hyderabad", "keywords": ["Java", "Spring Boot"],
                      "public_url": "https://www.instahyre.com/job-7-java-developer-at-acme-bangalore/"})
    assert (job.site, job.site_job_id, job.company) == ("instahyre", "7", "Acme")
    assert job.locations == ["Bangalore", "Hyderabad"]
    assert job.description == "Key skills: Java, Spring Boot"
    assert api_to_job({"id": 8, "title": "Dev"}).url == "https://www.instahyre.com/job-8/"


def test_parses_saved_api_response():
    jobs = [api_to_job(item) for item in json.loads(FIXTURE.read_text())["objects"]]
    assert len(jobs) == 5
    assert all(j.site_job_id and j.title and j.company and j.url.startswith("https://www.instahyre.com/job-") for j in jobs)


def test_opportunity_to_job():
    from sortie.sites.instahyre import opportunity_to_job
    opps = json.loads((Path(__file__).parent / "fixtures" / "instahyre_opportunities.json").read_text())["objects"]
    jobs = [opportunity_to_job(o) for o in opps]
    assert len(jobs) == 3 and all(j.site == "instahyre" and j.site_job_id.isdigit() and j.company for j in jobs)
    assert all(j.url.startswith("https://www.instahyre.com/job-") for j in jobs)
    job = opportunity_to_job({"job": {"id": 5, "title": "Dev", "opportunity_url": "/job-5-dev-at-acme-pune/",
                                      "keywords": "['Java', 'SQL']", "locations": "Pune"},
                              "employer": {"company_name": "Acme"}})
    assert (job.site_job_id, job.company, job.locations) == ("5", "Acme", ["Pune"])
    assert job.url == "https://www.instahyre.com/job-5-dev-at-acme-pune/"
    assert job.description == "Key skills: Java, SQL"
