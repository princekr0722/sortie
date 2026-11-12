import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from sortie import dashboard
from sortie.db import Database
from sortie.models import Job, Status


def job(i, site="naukri", title="Java Developer"):
    return Job(site=site, site_job_id=str(i), url=f"https://example.com/{i}", title=title, company=f"Acme {i}",
               locations=["Jaipur"], description="<script>alert(1)</script> Build APIs")


@pytest.fixture
def db_path(tmp_path):
    db = Database(tmp_path / "t.db")
    db.record(job(1), Status.APPLIED, fit_score=80, answers={"Notice period?": "30 days"})
    db.record(job(2), Status.SKIPPED, "experience 6-11 Yrs outside 2-5")
    db.record(job(3), Status.SKIPPED, "fit 40: Core stack mismatch", fit_score=40)
    db.record(job(4, "wellfound"), Status.FAILED, "TimeoutError: Locator.click: Timeout 30000ms")
    db.record(job(4, "wellfound"), Status.APPLIED, fit_score=70)  # retried: the latest attempt counts
    db.close()
    return tmp_path / "t.db"


def test_reason_groups():
    assert dashboard.reason_group("experience 6-11 Yrs outside 2-5") == "experience outside your range"
    assert dashboard.reason_group("fit 40: Core stack mismatch", "skipped") == "low fit score"
    assert dashboard.reason_group("fit 75: already applied", "skipped") == "already applied"
    assert dashboard.reason_group("fit 75: apply form doesn't mention this job; not filled", "failed") \
        == "apply form doesn't mention this job; not filled"
    assert dashboard.reason_group("TimeoutError: Locator.click: Timeout 30000ms", "failed") == "TimeoutError"


def test_overview_and_applications(db_path):
    conn = dashboard.connect(db_path)
    o = dashboard.overview(conn, {"naukri": 50, "wellfound": 20})
    sites = {s["site"]: s for s in o["sites"]}
    assert sites["naukri"]["today"] == {"applied": 1, "skipped": 2}
    assert sites["wellfound"]["today"] == {"failed": 1, "applied": 1}  # today counts every attempt
    assert sites["wellfound"]["all"] == {"applied": 1}  # all time: one row per job, its latest attempt
    assert o["totals"] == {"applied": 2, "skipped": 2}
    assert dict(o["reasons"]["skipped"]) == {"experience outside your range": 1, "low fit score": 1}
    assert o["fit"] == {"avg": 75.0, "n": 2}

    page = dashboard.applications(conn, status="skipped")
    assert page["total"] == 2 and {r["company"] for r in page["rows"]} == {"Acme 2", "Acme 3"}
    assert dashboard.applications(conn, q="acme 4")["total"] == 1
    assert dashboard.applications(conn, site="wellfound")["rows"][0]["status"] == "applied"

    first = dashboard.applications(conn, site="naukri", status="applied")["rows"][0]
    detail = dashboard.application(conn, first["id"])
    assert detail["answers"] == {"Notice period?": "30 days"} and detail["company"] == "Acme 1"
    retried = dashboard.application(conn, dashboard.applications(conn, site="wellfound")["rows"][0]["id"])
    assert [a["status"] for a in retried["attempts"]] == ["failed", "applied"]
    conn.close()


def test_database_is_opened_read_only(db_path):
    conn = dashboard.connect(db_path)
    with pytest.raises(Exception):
        conn.execute("DELETE FROM applications")
    conn.close()


def test_run_logs_only_by_bare_name(tmp_path):
    (tmp_path / "run-1.log").write_text("hello")
    assert [r["name"] for r in dashboard.runs(tmp_path)] == ["run-1.log"]
    assert dashboard.run_log("run-1.log", tmp_path) == "hello"
    assert dashboard.run_log("../t.db", tmp_path) is None


def test_server_refuses_other_hosts(db_path):
    dashboard.Handler.db_path, dashboard.Handler.caps = db_path, {"naukri": 50}
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        def get(path, host=f"127.0.0.1:{port}"):
            c = http.client.HTTPConnection("127.0.0.1", port)
            c.request("GET", path, headers={"Host": host})
            r = c.getresponse()
            return r.status, r.read()
        status, body = get("/api/overview")
        assert status == 200 and json.loads(body)["totals"]["applied"] == 2
        assert get("/api/overview", host="evil.example")[0] == 403  # DNS rebinding
        assert get("/screenshots/..%2Ft.db")[0] == 404
        assert get("/")[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_outcomes_funnel_and_clear(db_path):
    conn = dashboard.connect(db_path)
    applied = {r["company"]: r["id"] for r in dashboard.applications(conn, status="applied")["rows"]}
    conn.close()
    dashboard.set_outcome(db_path, applied["Acme 1"], "interview", "call on Monday")
    dashboard.set_outcome(db_path, applied["Acme 1"], "rejected")
    dashboard.set_outcome(db_path, applied["Acme 4"], "heard_back")
    with pytest.raises(ValueError):
        dashboard.set_outcome(db_path, applied["Acme 4"], "hired!")

    conn = dashboard.connect(db_path)
    o = dashboard.outcomes_summary(conn)
    # an interview that ended in a rejection still counts as an interview
    assert o["funnel"] == {"applied": 2, "replied": 2, "interviewed": 1, "offers": 0, "rejected": 1}
    assert {r["key"]: r["reply_rate"] for r in o["by_site"]} == {"naukri": 100.0, "wellfound": 100.0}
    assert dashboard.applications(conn, stage="rejected")["rows"][0]["company"] == "Acme 1"
    detail = dashboard.application(conn, applied["Acme 1"])
    assert detail["stage"] == "rejected" and detail["outcomes"][0]["note"] == "call on Monday"
    conn.close()

    dashboard.set_outcome(db_path, applied["Acme 4"], "none")  # undo
    conn = dashboard.connect(db_path)
    assert dashboard.applications(conn, stage="none")["rows"][0]["company"] == "Acme 4"
    conn.close()


def test_outcome_writes_refuse_other_sites(db_path):
    dashboard.Handler.db_path, dashboard.Handler.caps = db_path, {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        def post(body, kind="application/json", origin=None):
            c = http.client.HTTPConnection("127.0.0.1", port)
            headers = {"Content-Type": kind, "Host": f"127.0.0.1:{port}"} | ({"Origin": origin} if origin else {})
            c.request("POST", "/api/application/1/outcome", body=json.dumps(body), headers=headers)
            return c.getresponse().status
        assert post({"stage": "interview"}, kind="text/plain") == 403  # a cross-site form post
        assert post({"stage": "interview"}, origin="https://evil.example") == 403
        assert post({"stage": "bogus"}) == 400
        assert post({"stage": "interview"}, origin=f"http://127.0.0.1:{port}") == 200
    finally:
        server.shutdown()
        server.server_close()
