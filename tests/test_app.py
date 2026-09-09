"""
Tests for app.py's HTTP contract and background-job orchestration. Scraper classes are
mocked at the _build_scrapers/Fw3Scraper boundary — no Selenium, no network, and
background tasks are run synchronously via FastAPI's TestClient (starlette executes
BackgroundTasks before returning the response in the test client context... actually
TestClient runs them after sending the response but before the `with` block context
manager used per-request returns — see note on `client` fixture below).
"""

import time

import pytest
from fastapi.testclient import TestClient

import app as app_module


@pytest.fixture(autouse=True)
def clear_jobs_status():
    """jobs_status is a module-level dict — reset it around every test for isolation."""
    app_module.jobs_status.clear()
    yield
    app_module.jobs_status.clear()


@pytest.fixture
def client():
    # TestClient runs BackgroundTasks synchronously before __exit__/response delivery
    # completes when used outside `with`, but using the context manager form triggers
    # startup/shutdown events cleanly; background tasks still run within the request.
    return TestClient(app_module.app)


def valid_body(**overrides):
    body = {
        "keywords": ["python", "solidity"],
        "acceptable_locations": ["remote"],
        "anti_keywords": [],
        "discarded_urls": [],
        "max_jobs_searched_per_keyword": 10,
        "max_jobs_saved": 5,
        "max_experience_years": 4,
    }
    body.update(overrides)
    return body


class FakeScraper:
    """Stands in for a real scraper in _build_scrapers — run() just returns canned jobs."""

    def __init__(self, jobs=None, exc=None):
        self._jobs = jobs or []
        self._exc = exc

    def run(self, keywords):
        if self._exc:
            raise self._exc
        return self._jobs


def sample_job(url="https://findweb3.com/job/1"):
    return {
        "Job Title": "Engineer",
        "Company Name": "Acme",
        "Job Description": "<p>desc</p>",
        "Job Location": "Remote",
        "Compensation": None,
        "Job Url": url,
        "Tags": "python",
        "Notes": "",
    }


# ── POST /jobs ──────────────────────────────────────────────────────────────


class TestCreateJob:
    def test_valid_request_returns_202_with_job_id(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "_build_scrapers", lambda request, on_progress=None: [])

        response = client.post("/jobs", json=valid_body())

        assert response.status_code == 202
        body = response.json()
        assert "job_id" in body
        assert isinstance(body["job_id"], str)

    @pytest.mark.parametrize(
        "missing_field",
        ["keywords", "acceptable_locations", "max_jobs_searched_per_keyword", "max_jobs_saved", "max_experience_years"],
    )
    def test_missing_required_field_returns_422(self, client, missing_field):
        body = valid_body()
        del body[missing_field]

        response = client.post("/jobs", json=body)

        assert response.status_code == 422

    def test_wrong_type_returns_422(self, client):
        body = valid_body(keywords="not-a-list")

        response = client.post("/jobs", json=body)

        assert response.status_code == 422

    def test_anti_keywords_and_discarded_urls_default_to_empty_list(self, client, monkeypatch):
        captured = {}

        def fake_build_scrapers(request, on_progress=None):
            captured["anti_keywords"] = request.anti_keywords
            captured["discarded_urls"] = request.discarded_urls
            return []

        monkeypatch.setattr(app_module, "_build_scrapers", fake_build_scrapers)

        body = valid_body()
        del body["anti_keywords"]
        del body["discarded_urls"]

        response = client.post("/jobs", json=body)

        assert response.status_code == 202
        assert captured["anti_keywords"] == []
        assert captured["discarded_urls"] == []


# ── GET /jobs/{job_id}/status ───────────────────────────────────────────────


class TestJobStatus:
    def test_unknown_job_id_returns_404(self, client):
        response = client.get("/jobs/does-not-exist/status")
        assert response.status_code == 404

    def test_running_job_status(self, client):
        app_module.jobs_status["job1"] = {
            "status": "running",
            "stage": "scraping",
            "current": 3,
            "total": 10,
            "result": None,
            "error": None,
            "created_at": time.time(),
        }

        response = client.get("/jobs/job1/status")

        assert response.status_code == 200
        assert response.json() == {"status": "running", "stage": "scraping", "current": 3, "total": 10}

    def test_done_job_status(self, client):
        app_module.jobs_status["job1"] = {
            "status": "done",
            "stage": "done",
            "current": 10,
            "total": 10,
            "result": {"keywords": [], "jobs": []},
            "error": None,
            "created_at": time.time(),
        }

        response = client.get("/jobs/job1/status")

        assert response.status_code == 200
        assert response.json()["status"] == "done"

    def test_error_job_status_includes_error_message(self, client):
        app_module.jobs_status["job1"] = {
            "status": "error",
            "stage": "error",
            "current": 0,
            "total": 0,
            "result": None,
            "error": "Scraper 'findweb3.com' failed: boom",
            "created_at": time.time(),
        }

        response = client.get("/jobs/job1/status")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "error"
        assert body["error"] == "Scraper 'findweb3.com' failed: boom"


# ── GET /jobs/{job_id}/result ────────────────────────────────────────────────


class TestJobResult:
    def test_unknown_job_id_returns_404(self, client):
        response = client.get("/jobs/does-not-exist/result")
        assert response.status_code == 404

    def test_running_job_returns_409(self, client):
        app_module.jobs_status["job1"] = {
            "status": "running",
            "stage": "scraping",
            "current": 1,
            "total": 5,
            "result": None,
            "error": None,
            "created_at": time.time(),
        }

        response = client.get("/jobs/job1/result")

        assert response.status_code == 409

    def test_error_job_returns_500_with_message(self, client):
        app_module.jobs_status["job1"] = {
            "status": "error",
            "stage": "error",
            "current": 0,
            "total": 0,
            "result": None,
            "error": "something broke",
            "created_at": time.time(),
        }

        response = client.get("/jobs/job1/result")

        assert response.status_code == 500
        assert response.json()["detail"] == "something broke"

    def test_done_job_returns_200_with_result(self, client):
        expected_result = {"keywords": ["python"], "jobs": [sample_job()]}
        app_module.jobs_status["job1"] = {
            "status": "done",
            "stage": "done",
            "current": 1,
            "total": 1,
            "result": expected_result,
            "error": None,
            "created_at": time.time(),
        }

        response = client.get("/jobs/job1/result")

        assert response.status_code == 200
        assert response.json() == expected_result


# ── _purge_old_jobs ──────────────────────────────────────────────────────────


class TestPurgeOldJobs:
    def test_purges_entries_older_than_ttl(self):
        now = time.time()
        app_module.jobs_status["old"] = {
            "status": "done", "stage": "done", "current": 0, "total": 0,
            "result": None, "error": None,
            "created_at": now - app_module.JOB_STATUS_TTL_SECONDS - 1,
        }
        app_module.jobs_status["fresh"] = {
            "status": "done", "stage": "done", "current": 0, "total": 0,
            "result": None, "error": None,
            "created_at": now,
        }

        app_module._purge_old_jobs()

        assert "old" not in app_module.jobs_status
        assert "fresh" in app_module.jobs_status


# ── _build_scrapers ──────────────────────────────────────────────────────────


class TestBuildScrapers:
    def _spy_scraper_class(self, captured_kwargs):
        class SpyScraper:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

            def run(self, keywords):
                return []

        return SpyScraper

    def test_forwards_request_params_to_fw3_scraper(self, monkeypatch):
        captured_kwargs = {}
        monkeypatch.setattr(app_module, "Fw3Scraper", self._spy_scraper_class(captured_kwargs))
        monkeypatch.setattr(app_module, "Web3CareerScraper", self._spy_scraper_class({}))

        request = app_module.JobsRequest(**valid_body(
            keywords=["python"],
            acceptable_locations=["remote"],
            anti_keywords=["clearance"],
            discarded_urls=["https://findweb3.com/job/x"],
            max_jobs_searched_per_keyword=15,
            max_jobs_saved=7,
            max_experience_years=6,
        ))

        def progress_cb(stage, current, total):
            pass

        scrapers = app_module._build_scrapers(request, progress_cb)

        names = [name for name, _instance in scrapers]
        assert "findweb3.com" in names
        assert captured_kwargs["max_jobs_per_keyword"] == 15
        assert captured_kwargs["top_n_results"] == 7
        assert captured_kwargs["acceptable_locations"] == ["remote"]
        assert captured_kwargs["anti_keywords"] == ["clearance"]
        assert captured_kwargs["discarded_urls"] == ["https://findweb3.com/job/x"]
        assert captured_kwargs["max_experience_years"] == 6
        assert captured_kwargs["on_progress"] is progress_cb

    def test_forwards_request_params_to_web3career_scraper(self, monkeypatch):
        captured_kwargs = {}
        monkeypatch.setattr(app_module, "Fw3Scraper", self._spy_scraper_class({}))
        monkeypatch.setattr(app_module, "Web3CareerScraper", self._spy_scraper_class(captured_kwargs))

        request = app_module.JobsRequest(**valid_body(
            keywords=["python"],
            acceptable_locations=["remote"],
            anti_keywords=["clearance"],
            discarded_urls=["https://web3.career/job/x"],
            max_jobs_searched_per_keyword=15,
            max_jobs_saved=7,
            max_experience_years=6,
        ))

        def progress_cb(stage, current, total):
            pass

        scrapers = app_module._build_scrapers(request, progress_cb)

        names = [name for name, _instance in scrapers]
        assert "web3.career" in names
        assert captured_kwargs["max_jobs_per_keyword"] == 15
        assert captured_kwargs["top_n_results"] == 7
        assert captured_kwargs["acceptable_locations"] == ["remote"]
        assert captured_kwargs["anti_keywords"] == ["clearance"]
        assert captured_kwargs["discarded_urls"] == ["https://web3.career/job/x"]
        assert captured_kwargs["max_experience_years"] == 6
        assert captured_kwargs["on_progress"] is progress_cb

    def test_returns_both_scrapers(self, monkeypatch):
        monkeypatch.setattr(app_module, "Fw3Scraper", self._spy_scraper_class({}))
        monkeypatch.setattr(app_module, "Web3CareerScraper", self._spy_scraper_class({}))

        request = app_module.JobsRequest(**valid_body())

        scrapers = app_module._build_scrapers(request)

        names = {name for name, _instance in scrapers}
        assert names == {"findweb3.com", "web3.career"}


# ── _run_scrape_job / multi-scraper orchestration ───────────────────────────


class TestRunScrapeJob:
    def _seed_job(self, job_id="job1"):
        app_module.jobs_status[job_id] = {
            "status": "running",
            "stage": "starting",
            "current": 0,
            "total": 0,
            "result": None,
            "error": None,
            "created_at": time.time(),
        }

    def test_concatenates_results_from_multiple_scrapers(self, monkeypatch):
        job_a = sample_job("https://findweb3.com/job/a")
        job_b = sample_job("https://web3.career/job/b")

        monkeypatch.setattr(
            app_module,
            "_build_scrapers",
            lambda request, on_progress=None: [
                ("findweb3.com", FakeScraper(jobs=[job_a])),
                ("web3.career", FakeScraper(jobs=[job_b])),
            ],
        )

        self._seed_job("job1")
        request = app_module.JobsRequest(**valid_body())

        app_module._run_scrape_job("job1", request)

        state = app_module.jobs_status["job1"]
        assert state["status"] == "done"
        assert state["result"]["jobs"] == [job_a, job_b]
        assert state["result"]["keywords"] == request.keywords

    def test_scraper_failure_sets_error_status_and_stops(self, monkeypatch):
        job_a = sample_job("https://findweb3.com/job/a")

        monkeypatch.setattr(
            app_module,
            "_build_scrapers",
            lambda request, on_progress=None: [
                ("findweb3.com", FakeScraper(exc=RuntimeError("boom"))),
                ("web3.career", FakeScraper(jobs=[job_a])),
            ],
        )

        self._seed_job("job1")
        request = app_module.JobsRequest(**valid_body())

        app_module._run_scrape_job("job1", request)

        state = app_module.jobs_status["job1"]
        assert state["status"] == "error"
        assert "findweb3.com" in state["error"]
        assert "boom" in state["error"]
        # Second scraper must never run once an earlier one has failed.
        assert state["result"] is None

    def test_build_scrapers_failure_sets_error_status(self, monkeypatch):
        def raise_build(request, on_progress=None):
            raise ValueError("bad config")

        monkeypatch.setattr(app_module, "_build_scrapers", raise_build)

        self._seed_job("job1")
        request = app_module.JobsRequest(**valid_body())

        app_module._run_scrape_job("job1", request)

        state = app_module.jobs_status["job1"]
        assert state["status"] == "error"
        assert "bad config" in state["error"]

    def test_progress_callback_updates_jobs_status(self, monkeypatch):
        class ProgressReportingScraper:
            def __init__(self, on_progress):
                self._on_progress = on_progress

            def run(self, keywords):
                self._on_progress("scraping", 1, 2)
                self._on_progress("scraping", 2, 2)
                return []

        monkeypatch.setattr(
            app_module,
            "_build_scrapers",
            lambda request, on_progress=None: [
                ("findweb3.com", ProgressReportingScraper(on_progress)),
            ],
        )

        self._seed_job("job1")
        request = app_module.JobsRequest(**valid_body())

        app_module._run_scrape_job("job1", request)

        state = app_module.jobs_status["job1"]
        # current/total persist from the last on_progress call — only status/stage/result
        # get overwritten by the final "done" update.
        assert state["current"] == 2
        assert state["total"] == 2
        assert state["status"] == "done"
        assert state["stage"] == "done"


# ── End-to-end through the HTTP layer (scraper still mocked) ────────────────


class TestEndToEndJobLifecycle:
    def test_full_lifecycle_via_http(self, client, monkeypatch):
        result_job = sample_job()
        monkeypatch.setattr(
            app_module,
            "_build_scrapers",
            lambda request, on_progress=None: [("findweb3.com", FakeScraper(jobs=[result_job]))],
        )

        create_response = client.post("/jobs", json=valid_body())
        assert create_response.status_code == 202
        job_id = create_response.json()["job_id"]

        status_response = client.get(f"/jobs/{job_id}/status")
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "done"

        result_response = client.get(f"/jobs/{job_id}/result")
        assert result_response.status_code == 200
        assert result_response.json()["jobs"] == [result_job]
