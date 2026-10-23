"""ATS adapters against recorded fixtures (tests/fixtures/ats). No network: utils.http's
requests.get is replaced by a fake that serves fixture payloads per URL."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import scrapers.ats_scraper as ats_mod
from scrapers.ats_scraper import AshbyScraper, GreenhouseScraper, LeverScraper
from utils import http

FIX = Path(__file__).parent / "fixtures" / "ats"
load = lambda name: json.loads((FIX / f"{name}.json").read_text())  # noqa: E731


class Seq(list):
    """Responses consumed one per request (a plain list is a JSON payload)."""


class Resp:
    def __init__(self, payload=None, status=200, headers=None):
        self._payload, self.status_code, self.headers = payload, status, headers or {}

    def json(self):
        return self._payload


@pytest.fixture
def net(monkeypatch):
    """routes: {url-substring: payload | Resp | Exception | Seq of those (consumed in order)}."""
    state = SimpleNamespace(routes={}, calls=[], sleeps=[])

    def get(url, params=None, headers=None, timeout=None):
        state.calls.append(url)
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
    monkeypatch.setattr(ats_mod, "BOARD_GAP_SECONDS", 0)
    return state


# ── Mapping per vendor ───────────────────────────────────────────────────────


def test_greenhouse_mapping(net):
    net.routes["boards/stripe"] = load("greenhouse")
    rows = GreenhouseScraper(boards=["stripe"]).run(["solidity", "sales"])

    row = next(r for r in rows if r["source_job_id"] == "4012345")
    assert (row["source"], row["ats_type"], row["Job Title"]) == ("greenhouse", "greenhouse", "Backend Engineer")
    assert row["canonical_url"] == row["Job Url"] == "https://boards.greenhouse.io/stripe/jobs/4012345"
    assert row["apply_url"] == "https://job-boards.greenhouse.io/stripe/jobs/4012345?gh_src=abc"
    assert row["posted_at"] == "2026-09-30T14:00:00Z"
    assert row["work_mode"] == "remote" and row["company"] == "Stripe"
    assert row["description_html"].startswith("<p>Build &amp; ship")
    assert row["description_text"].startswith("Build & ship Solidity services")
    assert row["Tags"] == "solidity" and row["Notes"] == ""


def test_lever_mapping(net):
    net.routes["postings/palantir"] = load("lever")
    row = LeverScraper(boards=["palantir"]).run(["administrative"])[0]

    assert (row["source"], row["ats_type"]) == ("lever", "lever")
    assert row["source_job_id"] == "6ed76ce8-4156-4b60-b120-403538bd66cd"
    assert row["canonical_url"] == "https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd"
    assert row["apply_url"].endswith("/apply")
    assert row["posted_at"] == "2026-08-11T17:38:11Z"  # createdAt epoch ms
    assert row["work_mode"] == "hybrid" and row["company"] == "Palantir"
    assert row["Job Location"] == "Singapore, Singapore"


def test_ashby_mapping_and_remote_flag(net):
    payload = load("ashby")
    net.routes["job-board/ramp"] = payload
    row = AshbyScraper(boards=["ramp"]).run(["security"])[0]
    assert (row["source"], row["ats_type"]) == ("ashby", "ashby")
    assert row["canonical_url"] == f"https://jobs.ashbyhq.com/ramp/{row['source_job_id']}"
    assert row["posted_at"] == "2026-04-07T17:12:35Z" and row["title"] == "Security Engineer, Cloud"
    assert row["work_mode"] == "hybrid"  # workplaceType wins over isRemote
    assert row["salary"].startswith("$211.4K")

    remote = copy.deepcopy(payload)
    remote["jobs"][0].pop("workplaceType")
    remote["jobs"][0]["isRemote"] = True
    net.routes["job-board/ramp"] = remote
    assert AshbyScraper(boards=["ramp"]).run(["security"])[0]["work_mode"] == "remote"


def test_ashby_unlisted_postings_skipped(net):
    payload = load("ashby")
    payload["jobs"][0]["isListed"] = False
    net.routes["job-board/ramp"] = payload
    assert all("Security Engineer, Cloud" != r["title"] for r in AshbyScraper(boards=["ramp"]).run(["engineer"]))


# ── Keyword + filter application ─────────────────────────────────────────────


def test_non_matching_posting_not_returned(net):
    net.routes["boards/stripe"] = load("greenhouse")
    assert GreenhouseScraper(boards=["stripe"]).run(["haskell"]) == []


def test_shared_match_rule_word_mode(net):
    net.routes["boards/stripe"] = load("greenhouse")
    net.routes["boards/stripe"]["jobs"][0]["content"] = "&lt;p&gt;We use mongodb and python&lt;/p&gt;"
    kept = GreenhouseScraper(boards=["stripe"], anti_keywords=["go"], match_mode="word").run(["python"])
    assert [r["source_job_id"] for r in kept] == ["4012345"]  # mongodb != go in word mode
    net.routes["boards/stripe"]["jobs"][0]["content"] = "&lt;p&gt;We use mongodb and python&lt;/p&gt;"
    assert GreenhouseScraper(boards=["stripe"], anti_keywords=["go"]).run(["python"]) == []  # substring default


def test_location_experience_and_ranking(net):
    net.routes["boards/stripe"] = load("greenhouse")
    scraper = GreenhouseScraper(boards=["stripe"], max_experience_years=4, acceptable_locations=["remote"])
    assert [r["source_job_id"] for r in scraper.run(["payments", "solidity", "sell"])] == ["4012345"]


@pytest.mark.parametrize("discarded", [
    "https://job-boards.greenhouse.io/stripe/jobs/4012345?gh_src=abc",  # vendor listing/apply URL
    "https://boards.greenhouse.io/stripe/jobs/4012345",  # canonical
])
def test_discarded_url_not_returned(net, discarded):
    net.routes["boards/stripe"] = load("greenhouse")
    rows = GreenhouseScraper(boards=["stripe"], discarded_urls=[discarded]).run(["solidity"])
    assert rows == []


def test_known_key_not_returned(net):
    net.routes["boards/stripe"] = load("greenhouse")
    assert GreenhouseScraper(boards=["stripe"], known_keys=["greenhouse:4012345"]).run(["solidity"]) == []


# ── Politeness and isolation ─────────────────────────────────────────────────


def test_429_retry_after_then_success(net):
    net.routes["boards/stripe"] = Seq([Resp(status=429, headers={"Retry-After": "2"}), Resp(load("greenhouse"))])
    rows = GreenhouseScraper(boards=["stripe"]).run(["solidity"])
    assert len(rows) == 1 and [s for s in net.sleeps if s] == [2.0]


def test_unknown_board_404_no_retry_and_recorded(net):
    net.routes["boards/stripe"] = Resp(status=404)
    net.routes["boards/gitlab"] = load("greenhouse")
    scraper = GreenhouseScraper(boards=["stripe", "gitlab"])
    scraper.run(["solidity"])
    assert net.calls.count("https://boards-api.greenhouse.io/v1/boards/stripe/jobs") == 1
    assert scraper.errors == [{"source": "greenhouse:stripe", "error": "HTTP 404 (not found)"}]


def test_one_board_timeout_does_not_stop_others(net):
    net.routes["postings/netflix"] = Seq([requests.Timeout("slow")] * 3)
    net.routes["postings/palantir"] = load("lever")
    scraper = LeverScraper(boards=["netflix", "palantir"])
    rows = scraper.run(["administrative"])
    assert rows and [e["source"] for e in scraper.errors] == ["lever:netflix"]
    assert len([s for s in net.sleeps if s]) == 2  # backoff between the 3 tries of the failing board only


def test_all_boards_failing_raises_with_per_board_errors(net):
    net.routes["postings/"] = Resp(status=404)
    scraper = LeverScraper(boards=["a", "b"])
    with pytest.raises(RuntimeError):
        scraper.run(["x"])
    assert [e["source"] for e in scraper.errors] == ["lever:a", "lever:b"]


def test_plain_http_only_no_browser():
    assert not hasattr(ats_mod, "webdriver") and "selenium" not in Path(ats_mod.__file__).read_text()


def test_board_deadline_stops_before_fetching(net):
    scraper = GreenhouseScraper(boards=["stripe"], deadline=0.0)
    assert scraper.run(["solidity"]) == [] and scraper.deadline_hit and net.calls == []
