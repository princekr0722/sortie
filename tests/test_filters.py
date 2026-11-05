import pytest

from sortie.filters import parse_range, skip_reason
from sortie.models import Job


@pytest.fixture(scope="module")
def cfg(search, profile):
    return search, profile


def job(**kw):
    base = dict(site="naukri", site_job_id="1", url="u", title="Python Backend Developer",
                company="Acme Pvt Ltd", locations=["Calcutta"], experience="2-5 Yrs", salary="")
    return Job(**{**base, **kw})


def test_parse_range():
    assert parse_range("2-5 Yrs") == (2, 5)
    assert parse_range("12-18 Lacs PA") == (12, 18)
    assert parse_range("3+ years") == (3, 3)
    assert parse_range("Not disclosed") is None


def test_passes_good_job(cfg):
    assert skip_reason(job(), *cfg) is None


@pytest.mark.parametrize("kw, reason", [
    (dict(company="Initech India Pvt Ltd"), "excluded company"),
    (dict(title="Tech Lead - Java"), "excluded keyword"),
    (dict(title="Senior .NET Developer"), "excluded keyword"),
    (dict(experience="7-12 Yrs"), "experience"),
    (dict(experience="0-1 Yrs"), "experience"),
    (dict(locations=["Chennai"]), "location"),
    (dict(salary="6-10 Lacs PA"), "salary"),
])
def test_skips(cfg, kw, reason):
    assert reason in skip_reason(job(**kw), *cfg)


def test_location_aliases_and_remote(cfg):
    assert skip_reason(job(locations=["Gurgaon"]), *cfg) is None
    assert skip_reason(job(locations=["Dubai, UAE"]), *cfg) is None
    assert skip_reason(job(locations=["Remote"]), *cfg) is None


def test_lead_does_not_match_inside_words(cfg):
    assert skip_reason(job(title="Leading Java Developer"), *cfg) is None


def test_location_aliases():
    from sortie.filters import location_aliases
    assert "bangalore" in location_aliases("Bengaluru")
    assert "gurgaon" in location_aliases("Gurugram")
    assert "dubai" in location_aliases("UAE")
    assert location_aliases("Jaipur") == ["jaipur"]
