"""web3career API adapter, hashtagweb3 feed adapter, catalog-only sources. Fixtures only;
no live network and no token (a dummy value is set per test)."""

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

import app as app_module
import scrapers
import scrapers.hashtagweb3_feed as feed_mod
import scrapers.web3career_api as api_mod
from scrapers.ats_scraper import GreenhouseScraper
from scrapers.hashtagweb3_feed import HashtagWeb3Scraper
from scrapers.web3career_api import Web3CareerApiScraper
from scrapers.web3career_jobscraper import Web3CareerScraper
from utils import http
from utils.normalize import build_row, stable_id

FIX = Path(__file__).parent / "fixtures"
API_FIXTURE = json.loads((FIX / "web3" / "web3career_api.json").read_text())
FEED_FIXTURE = json.loads((FIX / "web3" / "hashtagweb3.json").read_text())
TOKEN = "dummy-token-for-tests"


class Resp:
    def __init__(self, payload=None, status=200, headers=None):
        self._payload, self.status_code, self.headers = payload, status, headers or {}

    def json(self):
        return self._payload


class Seq(list):
    pass


@pytest.fixture
def net(monkeypatch):
    state = SimpleNamespace(routes={}, calls=[], sleeps=[])

    def get(url, params=None, headers=None, timeout=None):
        state.calls.append({"url": url, "params": params, "headers": headers})
        for key, value in state.routes.items():
            if key in url:
                if isinstance(value, Seq):
                    value = value.pop(0)
                if isinstance(value, Exception):
                    raise value
                return value if isinstance(value, Resp) else Resp(value)
        raise AssertionError(f"unexpected request {url}")

    monkeypatch.setattr(http.requests, "get", get)
    monkeypatch.setattr(http.time, "sleep", state.sleeps.append)
    monkeypatch.setattr(api_mod, "REQUEST_GAP_SECONDS", 0)
    monkeypatch.setattr(feed_mod, "_cache", {})
    monkeypatch.setenv("WEB3CAREER_API_TOKEN", TOKEN)
    return state


# ── web3.career API ──────────────────────────────────────────────────────────


def test_api_mapping_and_apply_url_passthrough(net):
    net.routes["web3.career/api/v1"] = API_FIXTURE
    rows = Web3CareerApiScraper().run(["solidity"])

    assert len(rows) == 3
    row = next(r for r in rows if r["source_job_id"] == "155528")
    raw = API_FIXTURE[2][0]
    assert row["apply_url"] == raw["apply_url"] == "https://web3.career/r/4ITN1UTM__KakDRo"  # byte-identical
    assert row["source"] == "web3career" and row["Job Title"] == raw["title"]
    assert row["canonical_url"] == "https://web3.career/engineering-lead-wallets-nyc-crossmint/155528"
    assert row["posted_at"] == "2026-10-16T08:31:38Z"  # date_epoch 1792139498
    assert row["salary"] == "225000-250000 USD / year" and row["Compensation"] == row["salary"]
    assert row["Job Location"] == "New York, NY, USA" and row["work_mode"] == "unknown"
    assert row["Tags"] == ", ".join(row["tags"]) and row["Notes"] == ""  # score 0 allowed for aggregators


def test_api_one_request_per_keyword_limit_capped_no_paging(net):
    net.routes["web3.career/api/v1"] = API_FIXTURE
    Web3CareerApiScraper(max_jobs_per_keyword=500).run(["solidity", "smart contracts"])

    assert len(net.calls) == 2
    assert [c["params"]["limit"] for c in net.calls] == [100, 100]
    assert [c["params"]["tag"] for c in net.calls] == ["solidity", "smart-contracts"]
    assert all("page" not in c["params"] and "offset" not in c["params"] for c in net.calls)


def test_api_dedupes_across_keywords_and_handles_flat_array(net):
    net.routes["web3.career/api/v1"] = API_FIXTURE[2]  # defensive: root array IS the jobs array
    rows = Web3CareerApiScraper().run(["solidity", "rust"])
    assert sorted(r["source_job_id"] for r in rows) == ["155353", "155358", "155528"]


def test_api_missing_token_raises_without_any_request(net, monkeypatch):
    monkeypatch.delenv("WEB3CAREER_API_TOKEN")
    with pytest.raises(RuntimeError, match="WEB3CAREER_API_TOKEN not set"):
        Web3CareerApiScraper().run(["solidity"])
    assert net.calls == []


def test_api_429_retry_after_then_success(net):
    net.routes["web3.career/api/v1"] = Seq([Resp(status=429, headers={"Retry-After": "3"}), Resp(API_FIXTURE)])
    assert len(Web3CareerApiScraper().run(["solidity"])) == 3
    assert [s for s in net.sleeps if s] == [3.0]


def test_api_persistent_failure_recorded_and_token_never_leaks(net, caplog):
    net.routes["web3.career/api/v1"] = Seq([requests.ConnectionError(f"boom ?token={TOKEN}")] * 3)
    scraper = Web3CareerApiScraper()
    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as err:
        scraper.run(["solidity"])

    assert TOKEN not in str(err.value) and TOKEN not in json.dumps(scraper.errors) and TOKEN not in caplog.text
    assert scraper.errors[0]["source"] == "web3career"


def test_api_one_failing_keyword_does_not_lose_the_others(net):
    net.routes["web3.career/api/v1"] = Seq([Resp(status=500)] * 3 + [Resp(API_FIXTURE)])
    scraper = Web3CareerApiScraper()
    rows = scraper.run(["bad", "solidity"])
    assert len(rows) == 3 and len(scraper.errors) == 1


def test_fixture_contains_no_token_material():
    text = (FIX / "web3" / "web3career_api.json").read_text()
    assert "token" not in text.lower() or "token=" not in text


# ── Registry / cut-over / 422 ────────────────────────────────────────────────


def test_default_path_keeps_legacy_scraper_until_cutover(monkeypatch):
    monkeypatch.setenv("WEB3CAREER_API_TOKEN", TOKEN)
    monkeypatch.delenv("WEB3CAREER_LEGACY_SCRAPER", raising=False)
    assert scrapers.adapter_for("web3career", explicit=False) is Web3CareerScraper
    assert scrapers.adapter_for("web3career", explicit=True) is Web3CareerApiScraper

    monkeypatch.setenv("WEB3CAREER_LEGACY_SCRAPER", "0")
    assert scrapers.adapter_for("web3career", explicit=False) is Web3CareerApiScraper

    monkeypatch.delenv("WEB3CAREER_API_TOKEN")  # flag off but no token: stay on legacy
    assert scrapers.adapter_for("web3career", explicit=False) is Web3CareerScraper


def test_explicit_web3career_without_token_is_422(monkeypatch):
    monkeypatch.delenv("WEB3CAREER_API_TOKEN", raising=False)
    client = TestClient(app_module.app)
    body = {"keywords": ["a"], "max_jobs_searched_per_keyword": 1, "max_jobs_saved": 1, "sources": ["web3career"]}
    response = client.post("/jobs", json=body)
    assert response.status_code == 422 and "WEB3CAREER_API_TOKEN not set" in response.json()["detail"]


def test_omitted_sources_still_accepted_without_token(monkeypatch):
    monkeypatch.delenv("WEB3CAREER_API_TOKEN", raising=False)
    monkeypatch.setattr(app_module, "_build_scrapers", lambda request, on_progress=None: [])
    body = {"keywords": ["a"], "max_jobs_searched_per_keyword": 1, "max_jobs_saved": 1}
    assert TestClient(app_module.app).post("/jobs", json=body).status_code == 202


# ── hashtagweb3 ──────────────────────────────────────────────────────────────


def test_feed_single_fetch_for_many_keywords(net):
    net.routes["hashtagweb3.com"] = FEED_FIXTURE
    HashtagWeb3Scraper().run(["manager", "engineer", "analyst"])
    assert len(net.calls) == 1


def test_feed_mapping_and_client_side_filters(net):
    net.routes["hashtagweb3.com"] = FEED_FIXTURE
    rows = HashtagWeb3Scraper(top_n_results=50).run(["manager"])

    assert rows and all("manager" in r["Job Title"].lower() for r in rows)
    first = next(r for r in rows if r["source_job_id"] == "48a43493-1c69-4e53-804c-5a2afa8964b7")
    assert first["source"] == "hashtagweb3" and first["work_mode"] == "hybrid"
    assert first["canonical_url"] == "https://jobs.lever.co/coins/48a43493-1c69-4e53-804c-5a2afa8964b7"
    assert first["apply_url"] == FEED_FIXTURE[0]["link"] and first["description_html"] is None
    assert first["posted_at"] == "2026-10-19T08:17:22Z" and first["ats_type"] == "lever"

    remote_only = HashtagWeb3Scraper(top_n_results=50, work_modes=["remote"]).run(["manager"])
    assert all(r["work_mode"] in ("remote", "unknown") for r in remote_only)
    since = HashtagWeb3Scraper(top_n_results=50, since="2026-10-19T00:00:00Z").run(["manager"])
    assert {r["posted_at"] >= "2026-10-19" for r in since} <= {True}


def test_feed_skips_inactive(net):
    inactive = [dict(FEED_FIXTURE[0], active=False)]
    net.routes["hashtagweb3.com"] = inactive
    assert HashtagWeb3Scraper().run(["manager"]) == []


def test_feed_conditional_request_and_304_reuses_cache(net):
    net.routes["hashtagweb3.com"] = Seq([Resp(FEED_FIXTURE, headers={"ETag": '"v1"'}), Resp(status=304)])
    first = HashtagWeb3Scraper(top_n_results=50).run(["manager"])
    second = HashtagWeb3Scraper(top_n_results=50).run(["manager"])

    assert "If-None-Match" not in net.calls[0]["headers"]
    assert net.calls[1]["headers"]["If-None-Match"] == '"v1"'
    assert [r["source_job_id"] for r in second] == [r["source_job_id"] for r in first] and first


def test_feed_429_retry_after(net):
    net.routes["hashtagweb3.com"] = Seq([Resp(status=429, headers={"Retry-After": "4"}), Resp(FEED_FIXTURE)])
    assert HashtagWeb3Scraper().run(["manager"]) is not None
    assert [s for s in net.sleeps if s] == [4.0]


def test_feed_failure_raises_for_app_isolation(net):
    net.routes["hashtagweb3.com"] = Seq([Resp(status=503)] * 3)
    with pytest.raises(http.FetchError):
        HashtagWeb3Scraper().run(["manager"])


def test_feed_id_fallback_is_sha1_of_canonical_url():
    row = HashtagWeb3Scraper._map({"title": "T", "company": "C", "link": "https://jobs.lever.co/x/abc-1?lever-source=z"})
    assert row["source_job_id"] == stable_id("https://jobs.lever.co/x/abc-1")
    assert row["source_job_id"] == HashtagWeb3Scraper._map(
        {"title": "T", "company": "C", "link": "https://jobs.lever.co/x/abc-1"})["source_job_id"]


def test_feed_row_dedupes_against_greenhouse_adapter_row(net):
    """Feed link to a Greenhouse posting and the greenhouse adapter converge on one canonical_url."""
    link = "https://job-boards.eu.greenhouse.io/stripe/jobs/4012345?gh_src=x"
    net.routes["hashtagweb3.com"] = [{"id": "4012345", "title": "Backend Engineer solidity", "company": "Stripe",
                                      "link": link, "date": "2026-10-12T09:15:03-04:00", "active": True}]
    net.routes["boards/stripe"] = json.loads((FIX / "ats" / "greenhouse.json").read_text())
    feed_row = HashtagWeb3Scraper().run(["solidity"])[0]
    ats_row = GreenhouseScraper(boards=["stripe"]).run(["solidity"])[0]
    assert feed_row["canonical_url"] == ats_row["canonical_url"] == "https://boards.greenhouse.io/stripe/jobs/4012345"

    request = app_module.JobsRequest(keywords=["solidity"], max_jobs_searched_per_keyword=1, max_jobs_saved=10)
    merged = app_module._merge_and_cut([feed_row, ats_row], request.max_jobs_saved)
    assert len(merged) == 1


def test_partial_failure_hashtagweb3_down_findweb3_ok(monkeypatch):
    class Ok:
        def run(self, keywords):
            return [build_row({"Job Title": "A", "Company Name": "B", "Job Description": None,
                               "Job Location": None, "Compensation": None, "Job Url": "https://findweb3.com/job/1"},
                              "findweb3", id_fallback=True)]

    class Down:
        def run(self, keywords):
            raise http.FetchError("HTTP 503 after 3 attempts")

    monkeypatch.setattr(app_module, "_build_scrapers", lambda r, on_progress=None: [("findweb3", Ok()), ("hashtagweb3", Down())])
    app_module.jobs_status["j"] = {"status": "running", "stage": "s", "current": 0, "total": 0,
                                   "result": None, "error": None, "created_at": 9e12}
    app_module._run_scrape_job("j", app_module.JobsRequest(keywords=["a"], max_jobs_searched_per_keyword=1, max_jobs_saved=5))
    state = app_module.jobs_status["j"]
    app_module.jobs_status.clear()
    assert state["status"] == "done" and len(state["result"]["jobs"]) == 1
    assert state["result"]["errors"] == [{"source": "hashtagweb3", "error": "HTTP 503 after 3 attempts"}]


# ── Stable id fallback ───────────────────────────────────────────────────────


def test_fallback_source_job_id_is_sha1_of_canonical_and_stable():
    legacy = {"Job Title": "T", "Company Name": "C", "Job Description": None, "Job Location": None,
              "Compensation": None, "Job Url": "https://findweb3.com/jobs/odd-shape?utm_source=a"}
    a = build_row(legacy, "findweb3", id_fallback=True)
    b = build_row(dict(legacy, **{"Job Url": "https://findweb3.com/jobs/odd-shape"}), "findweb3", id_fallback=True)
    assert a["source_job_id"] == b["source_job_id"] == stable_id("https://findweb3.com/jobs/odd-shape")


# ── Catalog-only manual and defunct sources ──────────────────────────────────


def test_catalog_lists_manual_and_defunct_sources_as_non_executable():
    client = TestClient(app_module.app)
    by_id = {s["id"]: s for s in client.get("/sources").json()["sources"]}

    for sid in ("linkedin", "wellfound", "bitcoinerjobs", "laborx"):
        assert by_id[sid]["availability"] == "manual" and by_id[sid]["executable"] is False
        assert by_id[sid]["ingestion"]
    assert by_id["cyfrin"]["availability"] == "defunct" and by_id["cyfrin"]["executable"] is False
    assert "paste" in by_id["linkedin"]["ingestion"].lower() and "Odoo" in by_id["linkedin"]["ingestion"]
    assert all(scrapers.SOURCES[s]["adapter"] is None for s in ("linkedin", "wellfound", "bitcoinerjobs", "laborx", "cyfrin"))
    assert by_id["hashtagweb3"]["executable"] is True


@pytest.mark.parametrize("sid", ["linkedin", "wellfound", "bitcoinerjobs", "laborx", "cyfrin"])
def test_requesting_catalog_only_source_is_422(sid):
    client = TestClient(app_module.app)
    body = {"keywords": ["a"], "max_jobs_searched_per_keyword": 1, "max_jobs_saved": 1, "sources": [sid]}
    response = client.post("/jobs", json=body)
    assert response.status_code == 422 and sid in response.json()["detail"]
    assert app_module.jobs_status == {}
