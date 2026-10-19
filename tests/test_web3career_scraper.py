"""
Regression tests for Web3CareerScraper. No real HTTP/network is ever touched: FakeSession
stands in for requests.Session, keyed by exact URL, mirroring how FakeDriver stands in for
Selenium in test_fw3_scraper.py. HTML fixtures are trimmed-down but structurally faithful
reproductions of real web3.career markup (job-row-grid listing rows, JobPosting JSON-LD
blocks, the font-body description container, and the "Location:"/"Compensation:" labels)
captured during manual research against the live site.
"""

import requests

import scrapers.web3career_jobscraper as w3mod
from scrapers.web3career_jobscraper import Web3CareerScraper
from utils import filters as filters_mod


def make_scraper(**kwargs):
    return Web3CareerScraper(**kwargs)


# ── Fixtures ─────────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeSession:
    """responses: {url: FakeResponse | Exception}. Missing URL -> RequestException."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls: list = []
        self.closed = False

    def get(self, url, timeout=None):
        self.calls.append(url)
        result = self.responses.get(url)
        if result is None:
            raise requests.RequestException(f"no fake response configured for {url}")
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed = True


def search_page(rows, pagination_hrefs=()):
    """Build a minimal search-results page with one job-row-grid <tr> per (href, ) row."""
    row_html = "".join(
        f"""
        <tr class="job-row-grid table_row" data-jobid={i}>
          <td><a href="{href}">Title {i}</a></td>
          <td><a href="{href}">Company {i}</a></td>
        </tr>
        """
        for i, href in enumerate(rows, start=1)
    )
    pagination_html = "".join(f'<a href="{href}">page</a>' for href in pagination_hrefs)
    return f"""
    <html><body>
    <nav><a href="/python-jobs">Python Jobs</a> <a href="/web3-jobs-sofia">Sofia</a></nav>
    <table>{row_html}</table>
    {pagination_html}
    </body></html>
    """


def detail_page(
    title="Senior Python Engineer",
    company="Acme",
    location_label="Remote",
    compensation_label="$120k - $150k",
    description="<p>We need <b>5 years</b> of Python experience.</p>",
    extra_ld_json_block=None,
):
    location_html = f"<p class=job-cosmica>Location: {location_label}</p>" if location_label else ""
    compensation_html = (
        f"<p class=job-cosmica>Compensation: {compensation_label} <span>estimated</span></p>"
        if compensation_label
        else ""
    )
    description_html = (
        f'<div class="text-dark-grey-text p-2 p-md-0 font-body">{description}</div>'
        if description
        else ""
    )
    extra_block = extra_ld_json_block or ""
    return f"""
    <html><head>
    <script type="application/ld+json">
    {{"@context":"https://schema.org","@type":"JobPosting","title":"{title}",
      "hiringOrganization":{{"@type":"Organization","name":"{company}"}},
      "description":"plain text description — not used"}}
    </script>
    {extra_block}
    </head><body>
    <h1>{company} is hiring a {title}</h1>
    {location_html}
    {compensation_html}
    {description_html}
    </body></html>
    """


def raw_job(url, description, location="Remote", title="Role"):
    return {
        "name": title,
        "url": url,
        "location": location,
        "company_name": "Some Co",
        "compensation": None,
        "description_html": description,
    }


# ── Low-level extraction helpers ──────────────────────────────────────────────


class TestExtractJobHrefs:
    def test_dedupes_repeated_href_within_page(self):
        html = search_page(["/senior-python-engineer-acme/111"])
        hrefs = Web3CareerScraper._extract_job_hrefs(html)
        assert hrefs == ["/senior-python-engineer-acme/111"]

    def test_ignores_nav_links_without_trailing_id(self):
        html = search_page([])
        hrefs = Web3CareerScraper._extract_job_hrefs(html)
        assert hrefs == []

    def test_preserves_first_seen_order_across_multiple_rows(self):
        html = search_page(["/job-a/111", "/job-b/222", "/job-c/333"])
        hrefs = Web3CareerScraper._extract_job_hrefs(html)
        assert hrefs == ["/job-a/111", "/job-b/222", "/job-c/333"]


class TestExtractJobPosting:
    def test_returns_first_jobposting_block(self):
        html = detail_page(
            extra_ld_json_block="""
            <script type="application/ld+json">
            {"@context":"https://schema.org","@type":"JobPosting","title":"Similar Job Ignored",
             "hiringOrganization":{"@type":"Organization","name":"Other Co"}}
            </script>
            """
        )
        posting = Web3CareerScraper._extract_job_posting(html)
        assert posting["title"] == "Senior Python Engineer"
        assert posting["hiringOrganization"]["name"] == "Acme"

    def test_returns_none_when_no_jobposting_present(self):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[]}
        </script>
        </head><body></body></html>
        """
        assert Web3CareerScraper._extract_job_posting(html) is None

    def test_tolerates_malformed_json_ld_block(self):
        html = """
        <html><head>
        <script type="application/ld+json">not valid json{{{</script>
        </head><body></body></html>
        """
        assert Web3CareerScraper._extract_job_posting(html) is None


class TestExtractLabeledText:
    def test_extracts_location_label(self):
        html = detail_page(location_label="Buenos Aires, Argentina")
        assert Web3CareerScraper._extract_labeled_text(html, "Location") == "Buenos Aires, Argentina"

    def test_extracts_compensation_stopping_before_nested_span(self):
        html = detail_page(compensation_label="$84k - $108k")
        # The nested "estimated" <span> must not leak into the extracted value.
        assert Web3CareerScraper._extract_labeled_text(html, "Compensation") == "$84k - $108k"

    def test_returns_none_when_label_absent(self):
        html = detail_page(compensation_label=None)
        assert Web3CareerScraper._extract_labeled_text(html, "Compensation") is None


class TestExtractDescriptionHtml:
    def test_extracts_raw_inner_html(self):
        html = detail_page(description="<p>Loves <b>DeFi</b></p>")
        result = Web3CareerScraper._extract_description_html(html)
        assert "<p>Loves <b>DeFi</b></p>" in result

    def test_returns_none_when_container_absent(self):
        html = detail_page(description=None)
        assert Web3CareerScraper._extract_description_html(html) is None


# ── search_by_keyword ─────────────────────────────────────────────────────────


class TestSearchByKeyword:
    def test_discarded_url_does_not_consume_a_slot(self):
        page1 = search_page(["/job-a/1", "/job-b/2", "/job-c/3", "/job-d/4"])
        session = FakeSession({"https://web3.career/python-jobs?page=1": FakeResponse(page1)})
        scraper = make_scraper(
            max_jobs_per_keyword=3,
            discarded_urls=["https://web3.career/job-a/1"],
        )

        urls, discarded_count = scraper.search_by_keyword(session, "python")

        assert urls == [
            "https://web3.career/job-b/2",
            "https://web3.career/job-c/3",
            "https://web3.career/job-d/4",
        ]
        assert discarded_count == 1

    def test_paginates_when_first_page_is_not_enough(self):
        page1 = search_page(["/job-a/1", "/job-b/2"])
        page2 = search_page(["/job-c/3"])
        session = FakeSession({
            "https://web3.career/python-jobs?page=1": FakeResponse(page1),
            "https://web3.career/python-jobs?page=2": FakeResponse(page2),
        })
        scraper = make_scraper(max_jobs_per_keyword=3)

        urls, discarded_count = scraper.search_by_keyword(session, "python")

        assert urls == [
            "https://web3.career/job-a/1",
            "https://web3.career/job-b/2",
            "https://web3.career/job-c/3",
        ]
        assert session.calls == [
            "https://web3.career/python-jobs?page=1",
            "https://web3.career/python-jobs?page=2",
        ]

    def test_stops_once_max_jobs_per_keyword_reached_without_fetching_next_page(self):
        page1 = search_page(["/job-a/1", "/job-b/2", "/job-c/3"])
        session = FakeSession({"https://web3.career/python-jobs?page=1": FakeResponse(page1)})
        scraper = make_scraper(max_jobs_per_keyword=2)

        urls, _ = scraper.search_by_keyword(session, "python")

        assert urls == ["https://web3.career/job-a/1", "https://web3.career/job-b/2"]
        assert session.calls == ["https://web3.career/python-jobs?page=1"]

    def test_no_results_returns_empty(self):
        empty_page = search_page([])
        session = FakeSession({"https://web3.career/nonexistent-jobs?page=1": FakeResponse(empty_page)})
        scraper = make_scraper()

        urls, discarded_count = scraper.search_by_keyword(session, "nonexistent")

        assert urls == []
        assert discarded_count == 0

    def test_multi_word_keyword_is_hyphenated_in_url(self):
        page1 = search_page(["/job-a/1"])
        session = FakeSession({"https://web3.career/smart-contract-jobs?page=1": FakeResponse(page1)})
        scraper = make_scraper()

        urls, _ = scraper.search_by_keyword(session, "smart contract")

        assert urls == ["https://web3.career/job-a/1"]

    def test_request_failure_returns_whatever_was_collected_so_far(self):
        session = FakeSession({})  # no responses configured -> every get() raises
        scraper = make_scraper()

        urls, discarded_count = scraper.search_by_keyword(session, "python")

        assert urls == []
        assert discarded_count == 0


# ── extract_job_detail ────────────────────────────────────────────────────────


class TestExtractJobDetail:
    def test_extracts_all_fields_happy_path(self):
        html = detail_page(
            title="Senior Python Engineer &amp; Blockchain",
            company="Acme &amp; Co",
            location_label="Remote",
            compensation_label="$120k - $150k",
            description="<p>Great role</p>",
        )
        session = FakeSession({"https://web3.career/job/1": FakeResponse(html)})

        result = make_scraper().extract_job_detail(session, "https://web3.career/job/1")

        assert result["name"] == "Senior Python Engineer & Blockchain"  # HTML entity decoded
        assert result["company_name"] == "Acme & Co"
        assert result["location"] == "Remote"
        assert result["compensation"] == "$120k - $150k"
        assert result["description_html"] == "<p>Great role</p>"
        assert result["url"] == "https://web3.career/job/1"

    def test_extracts_date_posted_from_json_ld(self):
        html = detail_page().replace(
            '"description":"plain', '"datePosted":"2026-10-12T08:30:00+00:00","description":"plain'
        )
        session = FakeSession({"https://web3.career/job/1": FakeResponse(html)})

        result = make_scraper().extract_job_detail(session, "https://web3.career/job/1")

        assert result["posted_at"] == "2026-10-12T08:30:00+00:00"

    def test_missing_optional_fields_default_to_none(self):
        html = detail_page(location_label=None, compensation_label=None, description=None)
        session = FakeSession({"https://web3.career/job/1": FakeResponse(html)})

        result = make_scraper().extract_job_detail(session, "https://web3.career/job/1")

        assert result["location"] is None
        assert result["compensation"] is None
        assert result["description_html"] is None
        assert result["name"] == "Senior Python Engineer"  # title/company still present

    def test_no_jobposting_data_returns_none(self):
        html = "<html><body>no structured data here</body></html>"
        session = FakeSession({"https://web3.career/job/1": FakeResponse(html)})

        result = make_scraper().extract_job_detail(session, "https://web3.career/job/1")

        assert result is None

    def test_request_failure_returns_none(self):
        session = FakeSession({"https://web3.career/job/1": requests.ConnectionError("boom")})

        result = make_scraper().extract_job_detail(session, "https://web3.career/job/1")

        assert result is None

    def test_http_error_status_returns_none(self):
        session = FakeSession({"https://web3.career/job/1": FakeResponse("not found", status_code=500)})

        result = make_scraper().extract_job_detail(session, "https://web3.career/job/1")

        assert result is None


# ── run() orchestration ────────────────────────────────────────────────────────


class TestRun:
    def _scraper_with_mocks(self, monkeypatch, search_results, extract_results, **kwargs):
        scraper = Web3CareerScraper(**kwargs)
        session = FakeSession()
        monkeypatch.setattr(scraper, "_build_session", lambda: session)
        monkeypatch.setattr(
            scraper, "search_by_keyword", lambda s, kw: search_results.get(kw, ([], 0))
        )

        extract_calls = []

        def fake_extract(s, url):
            extract_calls.append(url)
            return extract_results.get(url)

        monkeypatch.setattr(scraper, "extract_job_detail", fake_extract)
        return scraper, session, extract_calls

    def test_deduplicates_urls_found_via_multiple_keywords(self, monkeypatch):
        scraper, _session, extract_calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={
                "kw1": (["https://web3.career/job/a", "https://web3.career/job/b"], 0),
                "kw2": (["https://web3.career/job/b", "https://web3.career/job/c"], 0),
            },
            extract_results={
                "https://web3.career/job/a": raw_job("https://web3.career/job/a", "kw1"),
                "https://web3.career/job/b": raw_job("https://web3.career/job/b", "kw1 kw2"),
                "https://web3.career/job/c": raw_job("https://web3.career/job/c", "kw2"),
            },
        )

        result = scraper.run(["kw1", "kw2"])

        assert len(extract_calls) == 3
        assert {job["Job Url"] for job in result} == {
            "https://web3.career/job/a",
            "https://web3.career/job/b",
            "https://web3.career/job/c",
        }

    def test_filters_applied_in_order_location_then_anti_keywords_then_experience(self, monkeypatch):
        call_order = []
        real_location, real_anti, real_experience = (
            w3mod.passes_location,
            w3mod.passes_anti_keywords,
            w3mod.passes_experience,
        )
        monkeypatch.setattr(
            w3mod, "passes_location",
            lambda job, locs: call_order.append("location") or real_location(job, locs),
        )
        monkeypatch.setattr(
            w3mod, "passes_anti_keywords",
            lambda job, kw: call_order.append("anti_keywords") or real_anti(job, kw),
        )
        monkeypatch.setattr(
            w3mod, "passes_experience",
            lambda job, yrs: call_order.append("experience") or real_experience(job, yrs),
        )

        scraper, _session, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"kw1": (["https://web3.career/job/1"], 0)},
            extract_results={
                "https://web3.career/job/1": raw_job(
                    "https://web3.career/job/1", "kw1 mentioned, 2 years experience"
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
            w3mod.passes_location,
            w3mod.passes_anti_keywords,
            w3mod.passes_experience,
        )
        monkeypatch.setattr(
            w3mod, "passes_location",
            lambda job, locs: call_order.append("location") or real_location(job, locs),
        )
        monkeypatch.setattr(
            w3mod, "passes_anti_keywords",
            lambda job, kw: call_order.append("anti_keywords") or real_anti(job, kw),
        )
        monkeypatch.setattr(
            w3mod, "passes_experience",
            lambda job, yrs: call_order.append("experience") or real_experience(job, yrs),
        )

        scraper, _session, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"kw1": (["https://web3.career/job/1"], 0)},
            extract_results={
                "https://web3.career/job/1": raw_job(
                    "https://web3.career/job/1", "kw1", location="Berlin"
                )
            },
            acceptable_locations=["remote"],
            anti_keywords=["kw1"],
            max_experience_years=0,
        )

        result = scraper.run(["kw1"])

        assert call_order == ["location"]
        assert result == []

    def test_ranks_by_score_and_returns_top_n_after_evaluating_all_candidates(self, monkeypatch):
        keywords = ["kw1", "kw2", "kw3", "kw4", "kw5"]
        scraper, _session, _calls = self._scraper_with_mocks(
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

        assert [job["Job Url"] for job in result] == ["url_high", "url_mid"]

    def test_tags_contains_only_matched_keywords(self, monkeypatch):
        keywords = ["python", "solidity", "rust"]
        scraper, _session, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"python": (["url1"], 0)},
            extract_results={"url1": raw_job("url1", "We need a python and solidity expert")},
        )

        result = scraper.run(keywords)

        assert result[0]["Tags"] == "python, solidity"

    def test_output_schema_exact_keys_no_internal_fields_leak(self, monkeypatch):
        scraper, _session, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"kw1": (["url1"], 0)},
            extract_results={"url1": raw_job("url1", "kw1 description")},
        )

        result = scraper.run(["kw1"])

        assert len(result) == 1
        legacy = {"Job Title", "Company Name", "Job Description", "Job Location",
                  "Compensation", "Job Url", "Tags", "Notes"}
        normalized = {"source", "source_job_id", "canonical_url", "apply_url", "company", "title",
                      "location", "work_mode", "posted_at", "description_html", "description_text",
                      "salary", "ats_type", "tags", "dedupe_key"}
        assert set(result[0].keys()) == legacy | normalized
        # Legacy values keep their meaning next to the additive normalized fields.
        assert result[0]["Job Url"] == "url1"
        assert result[0]["Job Description"] == "kw1 description"
        assert result[0]["Tags"] == "kw1" and result[0]["tags"] == ["kw1"]
        assert result[0]["source"] == "web3career"
        assert result[0]["Notes"] == ""

    def test_individual_job_extraction_failure_is_skipped_not_fatal(self, monkeypatch):
        scraper, _session, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"kw1": (["url1", "url2"], 0)},
            extract_results={
                "url1": None,
                "url2": raw_job("url2", "kw1 description"),
            },
        )

        result = scraper.run(["kw1"])

        assert len(result) == 1
        assert result[0]["Job Url"] == "url2"

    def test_session_closed_even_when_a_candidate_fails(self, monkeypatch):
        scraper, session, _calls = self._scraper_with_mocks(
            monkeypatch,
            search_results={"kw1": (["url1"], 0)},
            extract_results={"url1": None},
        )

        scraper.run(["kw1"])

        assert session.closed is True

    def test_reports_progress_at_each_stage(self, monkeypatch):
        events = []
        scraper, _session, _calls = self._scraper_with_mocks(
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
        assert len([e for e in events if e[0] == "filtering"]) == 1


class TestNormalizedRow:
    def test_stable_id_and_canonical_url_from_posting_url(self, monkeypatch):
        url = "https://web3.career/staff-engineer-acme/153659"
        raw = dict(raw_job(url, "kw1"), posted_at="2026-10-12T08:30:00+02:00")
        results = []
        for _ in range(2):  # same posting, two runs
            scraper = Web3CareerScraper()
            monkeypatch.setattr(scraper, "_build_session", lambda: FakeSession())
            monkeypatch.setattr(scraper, "search_by_keyword", lambda s, kw: ([url], 0))
            monkeypatch.setattr(scraper, "extract_job_detail", lambda s, u: raw)
            results.append(scraper.run(["kw1"])[0])

        assert results[0]["source_job_id"] == results[1]["source_job_id"] == "153659"
        assert results[0]["canonical_url"] == url
        assert results[0]["posted_at"] == "2026-10-12T06:30:00Z"
