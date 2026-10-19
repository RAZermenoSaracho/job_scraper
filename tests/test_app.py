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
from scrapers import SOURCES


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
        ["keywords", "max_jobs_searched_per_keyword", "max_jobs_saved"],
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
        monkeypatch.setitem(SOURCES["findweb3"], "adapter", self._spy_scraper_class(captured_kwargs))
        monkeypatch.setitem(SOURCES["web3career"], "adapter", self._spy_scraper_class({}))

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
        assert "findweb3" in names
        assert captured_kwargs["max_jobs_per_keyword"] == 15
        assert captured_kwargs["top_n_results"] == 7
        assert captured_kwargs["acceptable_locations"] == ["remote"]
        assert captured_kwargs["anti_keywords"] == ["clearance"]
        assert captured_kwargs["discarded_urls"] == ["https://findweb3.com/job/x"]
        assert captured_kwargs["max_experience_years"] == 6
        assert captured_kwargs["on_progress"] is progress_cb

    def test_forwards_request_params_to_web3career_scraper(self, monkeypatch):
        captured_kwargs = {}
        monkeypatch.setitem(SOURCES["findweb3"], "adapter", self._spy_scraper_class({}))
        monkeypatch.setitem(SOURCES["web3career"], "adapter", self._spy_scraper_class(captured_kwargs))

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
        assert "web3career" in names
        assert captured_kwargs["max_jobs_per_keyword"] == 15
        assert captured_kwargs["top_n_results"] == 7
        assert captured_kwargs["acceptable_locations"] == ["remote"]
        assert captured_kwargs["anti_keywords"] == ["clearance"]
        assert captured_kwargs["discarded_urls"] == ["https://web3.career/job/x"]
        assert captured_kwargs["max_experience_years"] == 6
        assert captured_kwargs["on_progress"] is progress_cb

    def test_returns_both_scrapers(self, monkeypatch):
        monkeypatch.setitem(SOURCES["findweb3"], "adapter", self._spy_scraper_class({}))
        monkeypatch.setitem(SOURCES["web3career"], "adapter", self._spy_scraper_class({}))

        request = app_module.JobsRequest(**valid_body())

        scrapers = app_module._build_scrapers(request)

        names = {name for name, _instance in scrapers}
        assert names == {"findweb3", "web3career"}


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
        assert state["result"]["errors"] == []

    def test_one_source_failing_keeps_other_sources_and_records_error(self, monkeypatch):
        job_a = sample_job("https://findweb3.com/job/a")

        monkeypatch.setattr(
            app_module,
            "_build_scrapers",
            lambda request, on_progress=None: [
                ("web3career", FakeScraper(exc=RuntimeError("boom\nTraceback..."))),
                ("findweb3", FakeScraper(jobs=[job_a])),
            ],
        )

        self._seed_job("job1")
        app_module._run_scrape_job("job1", app_module.JobsRequest(**valid_body()))

        state = app_module.jobs_status["job1"]
        assert state["status"] == "done"
        assert state["result"]["jobs"] == [job_a]
        assert state["result"]["errors"] == [{"source": "web3career", "error": "boom Traceback..."}]

    def test_all_sources_failing_sets_error_status(self, monkeypatch):
        monkeypatch.setattr(
            app_module,
            "_build_scrapers",
            lambda request, on_progress=None: [
                ("findweb3", FakeScraper(exc=RuntimeError("boom"))),
                ("web3career", FakeScraper(exc=RuntimeError("bang"))),
            ],
        )

        self._seed_job("job1")
        app_module._run_scrape_job("job1", app_module.JobsRequest(**valid_body()))

        state = app_module.jobs_status["job1"]
        assert state["status"] == "error"
        assert "findweb3" in state["error"] and "boom" in state["error"] and "bang" in state["error"]
        assert state["result"] is None

    def test_result_envelope_on_success(self, monkeypatch):
        monkeypatch.setattr(
            app_module, "_build_scrapers",
            lambda request, on_progress=None: [("findweb3", FakeScraper(jobs=[]))],
        )
        self._seed_job("job1")
        request = app_module.JobsRequest(**valid_body())

        app_module._run_scrape_job("job1", request)

        result = app_module.jobs_status["job1"]["result"]
        assert result == {"schema_version": "1", "keywords": request.keywords, "jobs": [], "errors": []}

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


# ── Optional X-API-Key ──────────────────────────────────────────────────────


class TestApiKey:
    def test_disabled_when_env_unset(self, client, monkeypatch):
        monkeypatch.delenv("JOB_SCRAPER_API_KEY", raising=False)
        monkeypatch.setattr(app_module, "_build_scrapers", lambda request, on_progress=None: [])

        assert client.post("/jobs", json=valid_body()).status_code == 202
        assert client.get("/jobs/nope/status").status_code == 404  # served, not 401

    @pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
    def test_enabled_rejects_missing_or_wrong_key_on_all_routes(self, client, monkeypatch, headers):
        monkeypatch.setenv("JOB_SCRAPER_API_KEY", "s3cret")

        assert client.post("/jobs", json=valid_body(), headers=headers).status_code == 401
        assert client.get("/jobs/x/status", headers=headers).status_code == 401
        assert client.get("/jobs/x/result", headers=headers).status_code == 401

    def test_enabled_accepts_right_key(self, client, monkeypatch):
        monkeypatch.setenv("JOB_SCRAPER_API_KEY", "s3cret")
        monkeypatch.setattr(app_module, "_build_scrapers", lambda request, on_progress=None: [])

        response = client.post("/jobs", json=valid_body(), headers={"X-API-Key": "s3cret"})

        assert response.status_code == 202


# ── Frontend compatibility (job_scraper_frontend/src/utils/jobRequest.js) ───


class TestFrontendCompatibility:
    LEGACY_ROW_KEYS = {"Job Title", "Company Name", "Job Description", "Job Location",
                       "Compensation", "Job Url", "Tags", "Notes"}

    def test_exact_frontend_body_flows_through_with_legacy_shapes(self, client, monkeypatch):
        from utils.normalize import build_row

        row = build_row(
            {"Job Title": "Engineer", "Company Name": "Acme", "Job Description": "<p>x</p>",
             "Job Location": "Remote", "Compensation": None, "Job Url": "https://findweb3.com/job/1"},
            "findweb3", tags=["python"],
        )
        monkeypatch.setattr(
            app_module, "_build_scrapers",
            lambda request, on_progress=None: [("findweb3", FakeScraper(jobs=[row]))],
        )
        body = {  # the 7 fields the frontend sends, nothing else
            "keywords": ["python"], "acceptable_locations": ["remote"], "anti_keywords": [],
            "discarded_urls": [], "max_jobs_searched_per_keyword": 20, "max_jobs_saved": 5,
            "max_experience_years": 4,
        }

        created = client.post("/jobs", json=body)
        assert created.status_code == 202 and set(created.json()) == {"job_id"}
        job_id = created.json()["job_id"]

        status = client.get(f"/jobs/{job_id}/status").json()
        assert set(status) == {"status", "stage", "current", "total"}
        assert status["status"] == "done"

        result = client.get(f"/jobs/{job_id}/result").json()
        assert {"keywords", "jobs"} <= set(result)
        assert self.LEGACY_ROW_KEYS <= set(result["jobs"][0])
        assert result["jobs"][0]["Job Url"] == "https://findweb3.com/job/1"

    def test_error_body_uses_detail_and_status_error(self, client, monkeypatch):
        monkeypatch.setattr(
            app_module, "_build_scrapers",
            lambda request, on_progress=None: [("findweb3", FakeScraper(exc=RuntimeError("boom")))],
        )
        job_id = client.post("/jobs", json=valid_body()).json()["job_id"]

        status = client.get(f"/jobs/{job_id}/status").json()
        assert status["status"] == "error" and "boom" in status["error"]
        result = client.get(f"/jobs/{job_id}/result")
        assert result.status_code == 500 and "boom" in result.json()["detail"]


# ── improve-discovery-filters: request fields, catalog, ranking, deadline ───

from utils.normalize import build_row  # noqa: E402

SOURCE_ENTRY_KEYS = {"id", "name", "category", "availability", "executable", "requires_config",
                     "filters", "hosts", "ingestion"}


def make_row(url, title="Dev", company="Acme", location="Remote", tags=(), source="findweb3"):
    return build_row(
        {"Job Title": title, "Company Name": company, "Job Description": "<p>d</p>",
         "Job Location": location, "Compensation": None, "Job Url": url},
        source, tags=list(tags), source_job_id=url.rsplit("/", 1)[-1],
    )


class TestSourceCatalog:
    def test_shape_and_default_sources(self, client):
        body = client.get("/sources").json()

        assert body["schema_version"] == "1"
        by_id = {s["id"]: s for s in body["sources"]}
        assert SOURCE_ENTRY_KEYS <= set(by_id["findweb3"])
        assert by_id["findweb3"]["executable"] is True and by_id["web3career"]["executable"] is True

    def test_executable_is_computed_from_adapter_and_config(self, client, monkeypatch):
        base = {"label": "X", "category": "job_board", "availability": "available",
                "needs_browser": False, "filters": [], "hosts": [], "ingestion": None}
        monkeypatch.setitem(SOURCES, "tok", {**base, "adapter": object, "requires_config": "token:TEST_TOK"})
        monkeypatch.setitem(SOURCES, "manual", {**base, "adapter": None, "availability": "manual",
                                                "requires_config": None, "ingestion": "paste URL"})
        monkeypatch.delenv("TEST_TOK", raising=False)

        by_id = {s["id"]: s for s in client.get("/sources").json()["sources"]}
        assert by_id["tok"]["executable"] is False and by_id["tok"]["availability"] == "planned"
        assert by_id["manual"]["executable"] is False and by_id["manual"]["ingestion"] == "paste URL"

        monkeypatch.setenv("TEST_TOK", "x")
        by_id = {s["id"]: s for s in client.get("/sources").json()["sources"]}
        assert by_id["tok"]["executable"] is True and by_id["tok"]["availability"] == "available"

    def test_no_network_needed(self, client, monkeypatch):
        import socket

        def boom(*a, **k):
            raise AssertionError("network access")

        monkeypatch.setattr(socket, "create_connection", boom)
        monkeypatch.setattr(socket.socket, "connect", boom)
        assert client.get("/sources").status_code == 200


class TestSourcesValidation:
    def _catalog_only(self, monkeypatch):
        monkeypatch.setitem(SOURCES, "linkedin", {
            "adapter": None, "label": "LinkedIn", "category": "job_board", "availability": "manual",
            "needs_browser": False, "requires_config": None, "filters": [], "hosts": [],
            "ingestion": "paste URL into Odoo"})

    def test_unknown_source_422_names_it_and_no_job_starts(self, client):
        response = client.post("/jobs", json=valid_body(sources=["foo"]))
        assert response.status_code == 422 and "foo" in response.json()["detail"]
        assert app_module.jobs_status == {}

    def test_non_executable_source_422(self, client, monkeypatch):
        self._catalog_only(monkeypatch)
        response = client.post("/jobs", json=valid_body(sources=["findweb3", "linkedin"]))
        assert response.status_code == 422 and "linkedin" in response.json()["detail"]
        assert app_module.jobs_status == {}

    def test_source_selection_builds_only_requested(self, monkeypatch):
        built = []

        class Spy:
            def __init__(self, **kw): built.append(self)

        monkeypatch.setitem(SOURCES, "findweb3", {**SOURCES["findweb3"], "adapter": Spy})
        monkeypatch.setitem(SOURCES, "web3career", {**SOURCES["web3career"], "adapter": Spy})

        request = app_module.JobsRequest(**valid_body(sources=["web3career"]))
        assert [n for n, _ in app_module._build_scrapers(request)] == ["web3career"]

        built.clear()
        request = app_module.JobsRequest(**valid_body())  # sources omitted -> legacy default
        assert [n for n, _ in app_module._build_scrapers(request)] == ["findweb3", "web3career"]

    def test_all_new_fields_forwarded_to_scrapers(self, monkeypatch):
        captured = {}

        class Spy:
            def __init__(self, **kw): captured.update(kw)

        monkeypatch.setitem(SOURCES, "findweb3", {**SOURCES["findweb3"], "adapter": Spy})
        request = app_module.JobsRequest(**valid_body(
            sources=["findweb3"], known_keys=["a"], since="2026-10-01T00:00:00Z", work_modes=["remote"],
            seniority_exclude=["senior"], excluded_companies=["x"], match_mode="word"))
        app_module._build_scrapers(request)

        assert captured["known_keys"] == ["a"] and captured["work_modes"] == ["remote"]
        assert captured["seniority_exclude"] == ["senior"] and captured["excluded_companies"] == ["x"]
        assert captured["match_mode"] == "word" and captured["since"].year == 2026


class TestOptionalRequestFields:
    def test_legacy_body_gets_neutral_defaults(self):
        request = app_module.JobsRequest(**valid_body())
        assert (request.sources, request.known_keys, request.since, request.work_modes) == (None, [], None, [])
        assert (request.seniority_exclude, request.excluded_companies) == ([], [])
        assert (request.deadline_seconds, request.match_mode) == (None, "substring")

    def test_acceptable_locations_and_experience_now_optional(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "_build_scrapers", lambda request, on_progress=None: [])
        body = valid_body()
        del body["acceptable_locations"], body["max_experience_years"]
        response = client.post("/jobs", json=body)
        assert response.status_code == 202
        request = app_module.JobsRequest(**body)
        assert request.acceptable_locations == [] and request.max_experience_years is None

    @pytest.mark.parametrize("override", [
        {"match_mode": "fuzzy"}, {"work_modes": ["mars"]}, {"since": "not-a-date"}, {"deadline_seconds": 0},
    ])
    def test_invalid_new_values_422(self, client, override):
        assert client.post("/jobs", json=valid_body(**override)).status_code == 422

    def test_unknown_fields_still_ignored(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "_build_scrapers", lambda request, on_progress=None: [])
        assert client.post("/jobs", json=valid_body(whatever=1)).status_code == 202


class TestGlobalRanking:
    def _run(self, monkeypatch, sources, **overrides):
        monkeypatch.setattr(app_module, "_build_scrapers", lambda request, on_progress=None: sources)
        app_module.jobs_status["j"] = {"status": "running", "stage": "starting", "current": 0,
                                       "total": 0, "result": None, "error": None, "created_at": time.time()}
        app_module._run_scrape_job("j", app_module.JobsRequest(**valid_body(**overrides)))
        return app_module.jobs_status["j"]

    def test_cross_source_best_first_and_global_cut(self, monkeypatch):
        s1 = [make_row("https://x.com/a/1", "A", tags="abc"), make_row("https://x.com/a/2", "B", tags="a")]
        s2 = [make_row("https://x.com/b/1", "C", tags="abcde"), make_row("https://x.com/b/2", "D", tags="ab")]
        state = self._run(monkeypatch, [("s1", FakeScraper(jobs=s1)), ("s2", FakeScraper(jobs=s2))],
                          max_jobs_saved=3)

        assert [j["title"] for j in state["result"]["jobs"]] == ["C", "A", "D"]

    def test_never_exceeds_max_jobs_saved_discard_queue_style(self, monkeypatch):
        rows = [make_row(f"https://x.com/{i}", f"T{i}") for i in range(4)]
        state = self._run(monkeypatch, [("s1", FakeScraper(jobs=rows)), ("s2", FakeScraper(jobs=rows[:2]))],
                          max_jobs_saved=1)
        assert len(state["result"]["jobs"]) == 1

    def test_duplicate_across_sources_keeps_higher_score(self, monkeypatch):
        low = make_row("https://x.com/1", "Same", tags="a")
        high = make_row("https://y.com/9", "Same", tags="abc", source="lever")  # same dedupe_key, other URL
        state = self._run(monkeypatch, [("s1", FakeScraper(jobs=[low])), ("s2", FakeScraper(jobs=[high]))])
        assert state["result"]["jobs"] == [high]

    def test_same_canonical_url_deduped(self, monkeypatch):
        a = make_row("https://x.com/1", "One", company="A")
        b = make_row("https://x.com/1?utm_source=z", "Other", company="B", tags="q")
        state = self._run(monkeypatch, [("s1", FakeScraper(jobs=[a])), ("s2", FakeScraper(jobs=[b]))])
        assert len(state["result"]["jobs"]) == 1

    def test_single_source_equals_input_order_for_equal_scores(self, monkeypatch):
        rows = [make_row(f"https://x.com/{i}", f"T{i}", company=f"C{i}") for i in range(3)]
        state = self._run(monkeypatch, [("s1", FakeScraper(jobs=rows))])
        assert state["result"]["jobs"] == rows


class TestDeadline:
    def test_interrupted_source_noted_and_status_done(self, monkeypatch):
        class Interrupted:
            deadline_hit = True
            errors = []

            def run(self, keywords):
                return [make_row("https://x.com/1")]

        monkeypatch.setattr(
            app_module, "_build_scrapers",
            lambda request, on_progress=None: [("first", FakeScraper(jobs=[make_row("https://x.com/0", "Z")])),
                                              ("second", Interrupted())],
        )
        app_module.jobs_status["j"] = {"status": "running", "stage": "s", "current": 0, "total": 0,
                                       "result": None, "error": None, "created_at": time.time()}
        app_module._run_scrape_job("j", app_module.JobsRequest(**valid_body(deadline_seconds=60)))

        state = app_module.jobs_status["j"]
        assert state["status"] == "done" and len(state["result"]["jobs"]) == 2
        assert state["result"]["errors"] == [{"source": "second", "error": "deadline exceeded"}]

    def test_sources_not_started_after_deadline_are_reported(self, monkeypatch):
        clock = iter([0.0, 0.0, 100.0, 100.0, 100.0])
        monkeypatch.setattr(app_module.time, "monotonic", lambda: next(clock))
        monkeypatch.setattr(
            app_module, "_build_scrapers",
            lambda request, on_progress=None: [("first", FakeScraper(jobs=[make_row("https://x.com/0")])),
                                              ("second", FakeScraper(jobs=[make_row("https://x.com/1", "Q")]))],
        )
        app_module.jobs_status["j"] = {"status": "running", "stage": "s", "current": 0, "total": 0,
                                       "result": None, "error": None, "created_at": time.time()}
        app_module._run_scrape_job("j", app_module.JobsRequest(**valid_body(deadline_seconds=5)))

        state = app_module.jobs_status["j"]
        assert state["status"] == "done" and len(state["result"]["jobs"]) == 1
        assert state["result"]["errors"] == [{"source": "second", "error": "deadline exceeded"}]

    def test_no_deadline_by_default(self, monkeypatch):
        scraper = FakeScraper(jobs=[])
        monkeypatch.setattr(app_module, "_build_scrapers", lambda request, on_progress=None: [("s", scraper)])
        app_module.jobs_status["j"] = {"status": "running", "stage": "s", "current": 0, "total": 0,
                                       "result": None, "error": None, "created_at": time.time()}
        app_module._run_scrape_job("j", app_module.JobsRequest(**valid_body()))
        assert not hasattr(scraper, "deadline")


# ── add-ats-sources: boards field and wiring ────────────────────────────────


class TestBoards:
    def test_valid_boards_accepted_and_deduped_case_insensitively(self):
        request = app_module.JobsRequest(**valid_body(
            boards=["greenhouse:stripe", "lever:netflix", "ashby:ramp", "Greenhouse:Stripe"]))
        assert request.boards == ["greenhouse:stripe", "lever:netflix", "ashby:ramp"]

    @pytest.mark.parametrize("bad", ["workday:acme", "stripe", "lever:", "lever:a b", "lever:a/b"])
    def test_malformed_entry_422_and_no_job(self, client, bad):
        response = client.post("/jobs", json=valid_body(boards=[bad]))
        assert response.status_code == 422 and app_module.jobs_status == {}

    def test_omitted_boards_builds_no_ats_scraper(self):
        names = [n for n, _ in app_module._build_scrapers(app_module.JobsRequest(**valid_body()))]
        assert names == ["findweb3", "web3career"]

    def test_boards_build_one_scraper_per_vendor_with_tokens(self, monkeypatch):
        seen = {}

        class Spy:
            def __init__(self, boards=None, **kw): seen.setdefault(self.__class__.__name__, boards)

        for vendor in ("greenhouse", "lever", "ashby"):
            monkeypatch.setitem(SOURCES, vendor, {**SOURCES[vendor], "adapter": type(vendor, (Spy,), {})})
        request = app_module.JobsRequest(**valid_body(
            boards=["greenhouse:stripe", "greenhouse:gitlab", "lever:netflix"]))

        names = [n for n, _ in app_module._build_scrapers(request)]

        assert names == ["findweb3", "web3career", "greenhouse", "lever"]
        assert seen == {"greenhouse": ["stripe", "gitlab"], "lever": ["netflix"]}

    def test_explicit_sources_limit_which_vendors_run(self, monkeypatch):
        request = app_module.JobsRequest(**valid_body(
            sources=["lever"], boards=["greenhouse:stripe", "lever:netflix"]))
        assert [n for n, _ in app_module._build_scrapers(request)] == ["lever"]

    def test_ats_ids_listed_in_catalog_as_executable(self, client):
        by_id = {s["id"]: s for s in client.get("/sources").json()["sources"]}
        for vendor in ("greenhouse", "lever", "ashby"):
            assert by_id[vendor]["category"] == "ats" and by_id[vendor]["executable"] is True
            assert by_id[vendor]["requires_config"] == "boards"

    def test_per_board_failures_reach_errors_and_do_not_fail_the_run(self, monkeypatch):
        class BoardScraper:
            errors = [{"source": "lever:netflix", "error": "HTTP 404 (not found)"}]

            def run(self, keywords):
                return [make_row("https://jobs.lever.co/x/1", source="lever")]

        class AllBoardsFailed:
            errors = [{"source": "greenhouse:a", "error": "HTTP 404"}, {"source": "greenhouse:b", "error": "HTTP 404"}]

            def run(self, keywords):
                raise RuntimeError("all greenhouse boards failed")

        monkeypatch.setattr(app_module, "_build_scrapers", lambda request, on_progress=None: [
            ("lever", BoardScraper()), ("greenhouse", AllBoardsFailed())])
        app_module.jobs_status["j"] = {"status": "running", "stage": "s", "current": 0, "total": 0,
                                       "result": None, "error": None, "created_at": time.time()}
        app_module._run_scrape_job("j", app_module.JobsRequest(**valid_body()))

        state = app_module.jobs_status["j"]
        assert state["status"] == "done" and len(state["result"]["jobs"]) == 1
        assert [e["source"] for e in state["result"]["errors"]] == [
            "lever:netflix", "greenhouse:a", "greenhouse:b"]
