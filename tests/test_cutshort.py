import json
from pathlib import Path

from sortie.sites.cutshort import data_to_job, listing_url, role_slugs

SKILLS = ["Python", "Django", "Vue", "Microservices"]
ITEMS = json.loads((Path(__file__).parent / "fixtures" / "cutshort_jobs.json").read_text())


def test_role_slugs():
    assert role_slugs("Python Backend Developer", SKILLS) == ["python-backend-developer", "backend-developer", "python"]
    assert role_slugs("Software Engineer Python Django", SKILLS) == [
        "software-engineer-python-django", "python", "django"]
    assert role_slugs("SWE Backend", SKILLS) == ["swe-backend", "backend-developer"]
    assert "stack-developer" not in role_slugs("Full Stack Developer Python Vue", SKILLS)
    assert role_slugs("Software Engineer", SKILLS) == []  # too generic for a listing


def test_listing_url():
    assert listing_url("java", "Bengaluru") == "https://cutshort.io/jobs/java-jobs-in-bangalore-bengaluru"
    assert listing_url("spring-boot", "Jaipur") == "https://cutshort.io/jobs/spring-boot-jobs-in-jaipur"


def test_data_to_job():
    job, remote = (data_to_job(i) for i in ITEMS)
    assert (job.site, job.site_job_id, job.company) == ("cutshort", "a1b2c3d4e5f6a1b2c3d4e5f6", "Globex")
    assert job.url == "https://cutshort.io/job/Backend-Engineer-Bengaluru-Globex-abc123"
    assert job.experience == "2-4 Yrs" and job.salary == "₹10L - ₹14L / yr"
    assert job.description.startswith("Our Story\nWe build backend systems & APIs.") and "Key skills: Java, Kafka" in job.description
    assert remote.locations == ["Jaipur", "Kolkata", "Remote"]
    assert data_to_job({"headline": "no id"}) is None


def test_cutshort_is_a_regular_site():
    from sortie.sites import ADAPTERS, DISABLED, EXPERIMENTAL
    assert "cutshort" in ADAPTERS and "cutshort" not in EXPERIMENTAL and "cutshort" not in DISABLED
