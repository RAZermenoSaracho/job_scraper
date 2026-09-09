"""
Regression tests for Fw3Scraper. No real Selenium/browser/network is ever touched:
- search_by_keyword / extract_job_detail are tested directly against FakeDriver
  (see conftest.py), which stands in for webdriver.Chrome.
- run() is tested with search_by_keyword/extract_job_detail/_build_driver monkeypatched
  on the instance, isolating the orchestration logic (dedup, filter order, ranking,
  tags, schema, progress) from the extraction details already covered above.
"""

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By

import scrapers.fw3_jobscraper as fw3mod
from scrapers.fw3_jobscraper import Fw3Scraper
from tests.conftest import FakeDriver, FakeElement


def make_scraper(**kwargs):
    return Fw3Scraper(**kwargs)


def raw_job(url, description, location="Remote", title="Role"):
    """A raw extracted-detail dict, as extract_job_detail would return it."""
    return {
        "name": title,
        "url": url,
        "location": location,
        "company_name": "Some Co",
        "compensation": None,
        "description_html": description,
    }


# ── search_by_keyword ─────────────────────────────────────────────────────────


class TestSearchByKeyword:
    def _driver_with_links(self, hrefs):
        driver = FakeDriver()
        anchors = [FakeElement(attrs={"href": href}) for href in hrefs]
        driver.find_element_map[(By.TAG_NAME, "a")] = anchors[0] if anchors else FakeElement()
        driver.find_elements_map[(By.TAG_NAME, "a")] = anchors
        return driver

    def test_discarded_url_does_not_consume_a_slot(self):
        hrefs = [
            "https://findweb3.com/job/job1",
            "https://findweb3.com/job/job2",
            "https://findweb3.com/job/job3",
            "https://findweb3.com/job/job4",
        ]
        driver = self._driver_with_links(hrefs)
        scraper = make_scraper(
            max_jobs_per_keyword=3,
            discarded_urls=["https://findweb3.com/job/job1"],
        )

        urls, discarded_count = scraper.search_by_keyword(driver, "python")

        assert urls == [
            "https://findweb3.com/job/job2",
            "https://findweb3.com/job/job3",
            "https://findweb3.com/job/job4",
        ]
        assert discarded_count == 1

    def test_stops_once_max_jobs_per_keyword_reached(self):
        hrefs = [f"https://findweb3.com/job/job{i}" for i in range(1, 10)]
        driver = self._driver_with_links(hrefs)
        scraper = make_scraper(max_jobs_per_keyword=2)

        urls, discarded_count = scraper.search_by_keyword(driver, "python")

        assert urls == hrefs[:2]
        assert discarded_count == 0

    def test_ignores_links_not_matching_job_detail_pattern(self):
        hrefs = [
            "https://findweb3.com/jobs/python",  # listing page, not a job detail page
            "https://findweb3.com/job/job1",
            "https://other-site.com/job/job2",
        ]
        driver = self._driver_with_links(hrefs)
        scraper = make_scraper(max_jobs_per_keyword=10)

        urls, _ = scraper.search_by_keyword(driver, "python")

        assert urls == ["https://findweb3.com/job/job1"]

    def test_deduplicates_repeated_href_on_same_page(self):
        hrefs = [
            "https://findweb3.com/job/job1",
            "https://findweb3.com/job/job1",
            "https://findweb3.com/job/job2",
        ]
        driver = self._driver_with_links(hrefs)
        scraper = make_scraper(max_jobs_per_keyword=10)

        urls, _ = scraper.search_by_keyword(driver, "python")

        assert urls == ["https://findweb3.com/job/job1", "https://findweb3.com/job/job2"]

    def test_timeout_loading_listing_page_returns_empty(self):
        class FailingDriver(FakeDriver):
            def get(self, url):
                raise TimeoutException("page load timeout")

        scraper = make_scraper()
        urls, discarded_count = scraper.search_by_keyword(FailingDriver(), "python")

        assert urls == []
        assert discarded_count == 0


# ── extract_job_detail ────────────────────────────────────────────────────────

DESCRIPTION_CLASS_XPATH = "//*[contains(translate(@class,'DESCRIPTION','description'),'description')]"
DESCRIPTION_TESTID_XPATH = (
    "//*[contains(translate(@data-testid,'DESCRIPTION','description'),'description')]"
)


class TestExtractJobDetail:
    def _base_driver(self, h1_text="Solidity Engineer"):
        driver = FakeDriver()
        driver.find_element_map[(By.TAG_NAME, "h1")] = FakeElement(text=h1_text)
        return driver

    def test_extracts_all_fields_happy_path(self):
        driver = self._base_driver()
        driver.find_element_map[(By.CSS_SELECTOR, "h2.JobPage_companyName__G6Nta")] = FakeElement(
            text="Maple Finance"
        )
        driver.find_element_map[(By.CSS_SELECTOR, "div.JobPage_companyLocation__jz0CV")] = FakeElement(
            text="Remote"
        )
        driver.find_elements_map[(By.CSS_SELECTOR, "div.JobPage_keyDetail__Dddhf")] = [
            FakeElement(text="Full-time"),
            FakeElement(text="$125K – $273K"),
        ]
        driver.find_element_map[(By.XPATH, DESCRIPTION_CLASS_XPATH)] = FakeElement(
            attrs={"innerHTML": "<div>Great role</div>"}
        )

        result = make_scraper().extract_job_detail(driver, "https://findweb3.com/job/job1")

        assert result == {
            "name": "Solidity Engineer",
            "url": "https://findweb3.com/job/job1",
            "location": "Remote",
            "company_name": "Maple Finance",
            "compensation": "$125K – $273K",
            "description_html": "<div>Great role</div>",
        }

    def test_falls_back_to_second_description_xpath(self):
        driver = self._base_driver()
        driver.find_element_map[(By.XPATH, DESCRIPTION_TESTID_XPATH)] = FakeElement(
            attrs={"innerHTML": "<p>desc</p>"}
        )

        result = make_scraper().extract_job_detail(driver, "https://findweb3.com/job/job1")

        assert result["description_html"] == "<p>desc</p>"

    def test_missing_h1_returns_none(self):
        driver = FakeDriver()  # nothing configured — no h1 anywhere on the page

        result = make_scraper().extract_job_detail(driver, "https://findweb3.com/job/job1")

        assert result is None

    def test_missing_optional_fields_default_to_none(self):
        driver = self._base_driver()  # only h1 configured

        result = make_scraper().extract_job_detail(driver, "https://findweb3.com/job/job1")

        assert result["company_name"] is None
        assert result["location"] is None
        assert result["compensation"] is None
        assert result["description_html"] is None

    def test_page_load_failure_returns_none(self):
        class FailingDriver(FakeDriver):
            def get(self, url):
                raise TimeoutException("page load timeout")

        result = make_scraper().extract_job_detail(FailingDriver(), "https://findweb3.com/job/job1")

        assert result is None


# ── run() orchestration ────────────────────────────────────────────────────────


class TestRun:
    def _scraper_with_mocks(self, monkeypatch, search_results, extract_results, **kwargs):
        """
        search_results: {keyword: (urls, discarded_count)} — stands in for search_by_keyword.
        extract_results: {url: raw_dict_or_None} — stands in for extract_job_detail.
        Returns (scraper, driver, extract_calls) so tests can assert on call order/counts.
        """
        scraper = Fw3Scraper(**kwargs)
        driver = FakeDriver()
        monkeypatch.setattr(scraper, "_build_driver", lambda: driver)
        monkeypatch.setattr(
            scraper, "search_by_keyword", lambda d, kw: search_results.get(kw, ([], 0))
        )

        extract_calls = []

        def fake_extract(d, url):
            extract_calls.append(url)
            return extract_results.get(url)

        monkeypatch.setattr(scraper, "extract_job_detail", fake_extract)
        return scraper, driver, extract_calls

    def test_deduplicates_urls_found_via_multiple_keywords(self, monkeypatch):
        scraper, _driver, extract_calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={
                "kw1": (["https://findweb3.com/job/a", "https://findweb3.com/job/b"], 0),
                "kw2": (["https://findweb3.com/job/b", "https://findweb3.com/job/c"], 0),
            },
            extract_results={
                "https://findweb3.com/job/a": raw_job("https://findweb3.com/job/a", "kw1"),
                "https://findweb3.com/job/b": raw_job("https://findweb3.com/job/b", "kw1 kw2"),
                "https://findweb3.com/job/c": raw_job("https://findweb3.com/job/c", "kw2"),
            },
        )

        result = scraper.run(["kw1", "kw2"])

        # job/b was found via both keywords but must only be visited/scored once.
        assert sorted(extract_calls) == [
            "https://findweb3.com/job/a",
            "https://findweb3.com/job/b",
            "https://findweb3.com/job/c",
        ]
        assert len(extract_calls) == 3
        assert {job["Job Url"] for job in result} == {
            "https://findweb3.com/job/a",
            "https://findweb3.com/job/b",
            "https://findweb3.com/job/c",
        }

    def test_filters_applied_in_order_location_then_anti_keywords_then_experience(self, monkeypatch):
        call_order = []
        real_location, real_anti, real_experience = (
            fw3mod.passes_location,
            fw3mod.passes_anti_keywords,
            fw3mod.passes_experience,
        )

        def spy_location(job, locations):
            call_order.append("location")
            return real_location(job, locations)

        def spy_anti(job, anti_kw):
            call_order.append("anti_keywords")
            return real_anti(job, anti_kw)

        def spy_experience(job, max_years):
            call_order.append("experience")
            return real_experience(job, max_years)

        monkeypatch.setattr(fw3mod, "passes_location", spy_location)
        monkeypatch.setattr(fw3mod, "passes_anti_keywords", spy_anti)
        monkeypatch.setattr(fw3mod, "passes_experience", spy_experience)

        scraper, _driver, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"kw1": (["https://findweb3.com/job/1"], 0)},
            extract_results={
                "https://findweb3.com/job/1": raw_job(
                    "https://findweb3.com/job/1", "kw1 mentioned, 2 years experience"
                )
            },
            acceptable_locations=["remote"],
            anti_keywords=["blacklisted"],
            max_experience_years=5,
        )

        scraper.run(["kw1"])

        assert call_order == ["location", "anti_keywords", "experience"]

    def test_location_failure_short_circuits_remaining_filters(self, monkeypatch):
        call_order = []
        real_location, real_anti, real_experience = (
            fw3mod.passes_location,
            fw3mod.passes_anti_keywords,
            fw3mod.passes_experience,
        )
        monkeypatch.setattr(
            fw3mod, "passes_location",
            lambda job, locs: call_order.append("location") or real_location(job, locs),
        )
        monkeypatch.setattr(
            fw3mod, "passes_anti_keywords",
            lambda job, kw: call_order.append("anti_keywords") or real_anti(job, kw),
        )
        monkeypatch.setattr(
            fw3mod, "passes_experience",
            lambda job, yrs: call_order.append("experience") or real_experience(job, yrs),
        )

        scraper, _driver, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"kw1": (["https://findweb3.com/job/1"], 0)},
            extract_results={
                "https://findweb3.com/job/1": raw_job(
                    "https://findweb3.com/job/1", "kw1", location="Berlin"
                )
            },
            acceptable_locations=["remote"],  # Berlin doesn't match -> fails at location
            anti_keywords=["kw1"],  # would also fail here if reached
            max_experience_years=0,  # would also fail here if reached
        )

        result = scraper.run(["kw1"])

        assert call_order == ["location"]
        assert result == []

    def test_ranks_by_score_and_returns_top_n_after_evaluating_all_candidates(self, monkeypatch):
        keywords = ["kw1", "kw2", "kw3", "kw4", "kw5"]
        scraper, _driver, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"kw1": (["url_low", "url_mid", "url_high"], 0)},
            extract_results={
                "url_low": raw_job("url_low", "kw1"),
                "url_mid": raw_job("url_mid", "kw1 kw2"),
                "url_high": raw_job("url_high", "kw1 kw2 kw3 kw4 kw5"),
            },
            top_n_results=2,
        )

        result = scraper.run(keywords)

        # Discovery order was low, mid, high (scores 1, 2, 5) — evaluating the full
        # pool before cutting to top_n=2 must keep the two highest scores, not the
        # first two discovered.
        assert [job["Job Url"] for job in result] == ["url_high", "url_mid"]

    def test_tags_contains_only_matched_keywords(self, monkeypatch):
        keywords = ["python", "solidity", "rust"]
        scraper, _driver, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"python": (["url1"], 0)},
            extract_results={"url1": raw_job("url1", "We need a python and solidity expert")},
        )

        result = scraper.run(keywords)

        assert result[0]["Tags"] == "python, solidity"

    def test_output_schema_exact_keys_no_internal_fields_leak(self, monkeypatch):
        scraper, _driver, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"kw1": (["url1"], 0)},
            extract_results={"url1": raw_job("url1", "kw1 description")},
        )

        result = scraper.run(["kw1"])

        assert len(result) == 1
        assert set(result[0].keys()) == {
            "Job Title",
            "Company Name",
            "Job Description",
            "Job Location",
            "Compensation",
            "Job Url",
            "Tags",
            "Notes",
        }
        assert result[0]["Notes"] == ""

    def test_candidate_extraction_failure_is_skipped_not_fatal(self, monkeypatch):
        scraper, _driver, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"kw1": (["url1", "url2"], 0)},
            extract_results={
                "url1": None,  # extraction failed for this one
                "url2": raw_job("url2", "kw1 description"),
            },
        )

        result = scraper.run(["kw1"])

        assert len(result) == 1
        assert result[0]["Job Url"] == "url2"

    def test_driver_quit_called_even_when_a_candidate_fails(self, monkeypatch):
        scraper, driver, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"kw1": (["url1"], 0)},
            extract_results={"url1": None},
        )

        scraper.run(["kw1"])

        assert driver.quit_called is True

    def test_reports_progress_at_each_stage(self, monkeypatch):
        events = []
        scraper, _driver, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"kw1": (["url1"], 0), "kw2": ([], 0)},
            extract_results={"url1": raw_job("url1", "kw1 text")},
            on_progress=lambda stage, current, total: events.append((stage, current, total)),
        )

        scraper.run(["kw1", "kw2"])

        assert [e for e in events if e[0] == "searching_keywords"] == [
            ("searching_keywords", 1, 2),
            ("searching_keywords", 2, 2),
        ]
        assert [e for e in events if e[0] == "scraping"] == [("scraping", 1, 1)]
        filtering_events = [e for e in events if e[0] == "filtering"]
        assert len(filtering_events) == 1

    def test_no_progress_callback_does_not_raise(self, monkeypatch):
        scraper, _driver, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"kw1": (["url1"], 0)},
            extract_results={"url1": raw_job("url1", "kw1 text")},
        )

        # on_progress defaults to None — run() must not blow up calling it.
        result = scraper.run(["kw1"])

        assert len(result) == 1
