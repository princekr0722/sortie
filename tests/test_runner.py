import pytest

from sortie import runner as runner_mod
from sortie.db import Database
from sortie.llm.answer_book import AnswerBook
from sortie.llm.client import LLMClient
from sortie.models import ApplyResult, Job, Mode, Question, Status
from sortie.sites.base import SiteAdapter


class FakeSite(SiteAdapter):
    name = "fake"
    login_url = "about:blank"

    def __init__(self, jobs):
        super().__init__(page=None)
        self.jobs = jobs
        self.applied = []

    async def ensure_logged_in(self):
        return True

    async def search(self, search, profile):
        for j in self.jobs:
            yield j

    async def get_details(self, job):
        return job

    async def apply(self, job, answerer, confirm):
        q = Question("What is your notice period?")
        answers = {q.text: await answerer.answer(q, job)}
        if not await confirm(job, answers):
            return ApplyResult(Status.SKIPPED, "not submitted", answers)
        self.applied.append(job.site_job_id)
        return ApplyResult(Status.APPLIED, answers=answers)


def jobs(n, **kw):
    return [Job(site="fake", site_job_id=str(i), url="u", title="Java Developer", company="Acme",
                locations=["Pune"], experience="2-5 Yrs", **kw) for i in range(n)]


@pytest.fixture
def make_runner(tmp_path, monkeypatch, profile, search):
    async def no_pause(*a, **k):
        pass
    monkeypatch.setattr(runner_mod, "human_pause", no_pause)

    def make(mode, limit=None):
        cfg = search.model_copy(deep=True)
        cfg.daily_caps["fake"] = 3
        r = runner_mod.Runner(profile, cfg, Database(tmp_path / "t.db"), LLMClient(model=None),
                              mode, headless=True, limit=limit, book=AnswerBook(tmp_path / "answers.yaml"))
        r.summary["fake"] = __import__("collections").Counter()
        return r
    return make


async def test_auto_applies_up_to_cap_and_dedupes(make_runner):
    r = make_runner(Mode.AUTO)
    site = FakeSite(jobs(5) + [Job(site="fake", site_job_id="x", url="u", title="Java Developer",
                                   company="Initech Pvt Ltd", locations=["Pune"])])
    await r.run_site(site)
    assert site.applied == ["0", "1", "2"]
    assert r.db.applied_count("fake") == 3

    site2 = FakeSite(jobs(5))
    await r.run_site(site2)  # cap already used today
    assert site2.applied == []


async def test_excluded_company_recorded_as_skipped(make_runner):
    r = make_runner(Mode.AUTO)
    await r.run_site(FakeSite([Job(site="fake", site_job_id="x", url="u", title="Java Developer",
                                   company="Initech Pvt Ltd", locations=["Pune"])]))
    assert r.summary["fake"]["skipped"] == 1 and r.db.is_seen("fake", "x")


async def test_dry_run_writes_nothing_and_respects_limit(make_runner, monkeypatch):
    async def quiet(*a, **k):
        pass
    monkeypatch.setattr(runner_mod.terminal, "confirm_preview", quiet)
    r = make_runner(Mode.DRY_RUN, limit=2)
    site = FakeSite(jobs(5))
    await r.run_site(site)
    assert site.applied == []
    assert r.summary["fake"]["skipped"] == 2
    assert not r.db.is_seen("fake", "0")


async def test_block_during_apply_records_job_and_stops_site(make_runner):
    from sortie.sites.base import BlockedError

    class LimitedSite(FakeSite):
        async def apply(self, job, answerer, confirm):
            raise BlockedError("limit reached", ApplyResult(Status.FAILED, "Apply did nothing"))

    r = make_runner(Mode.AUTO)
    with pytest.raises(BlockedError):
        await r.run_site(LimitedSite(jobs(3)))
    assert r.summary["fake"]["failed"] == 1 and r.db.is_seen("fake", "0") is False  # failed once: retried later


async def test_job_lines_show_applied_progress(make_runner, caplog):
    import logging
    r = make_runner(Mode.AUTO, limit=2)
    with caplog.at_level(logging.INFO, logger="sortie"):
        from sortie.log import log
        log.propagate = True
        r.run_target = 2
        await r.apply_jobs(FakeSite(jobs(3)), 2)
    lines = [m for m in caplog.messages if "APPLIED" in m]
    assert lines[0].startswith("[  1 · 1/2 ✅]") and lines[1].startswith("[  2 · 2/2 ✅]")


async def test_external_jobs_are_not_scored(make_runner, monkeypatch):
    scored = []

    async def fake_fit(llm, profile, job, about=""):
        scored.append(job.site_job_id)

    monkeypatch.setattr(runner_mod, "score_fit", fake_fit)
    r = make_runner(Mode.AUTO)
    external = jobs(1)[0]
    external.site_job_id, external.external = "ext", True
    await r.run_site(FakeSite([external] + jobs(1)))
    assert r.summary["fake"]["external"] == 1 and scored == ["0"]


async def test_title_screen_skips_before_opening_and_caches(make_runner, monkeypatch):
    calls = []

    async def fake_screen(llm, profile, roles, job):
        calls.append(job.title)
        return "title screen: SAP role" if "SAP" in job.title else None

    monkeypatch.setattr(runner_mod, "screen_title", fake_screen)
    r = make_runner(Mode.AUTO)
    sap = jobs(2)
    for j in sap:
        j.title = "SAP ABAP Developer"
    sap[0].site_job_id, sap[1].site_job_id = "sap1", "sap2"
    await r.run_site(FakeSite(sap + jobs(1)))
    assert r.summary["fake"]["skipped"] == 2 and r.summary["fake"]["applied"] == 1
    assert calls.count("SAP ABAP Developer") == 1  # cached per title


def test_pace_scales_explicit_bounds():
    import sortie.browser as browser
    browser.set_pace("fast")
    assert browser._pace == (1, 3)
    browser.set_pace("normal")
