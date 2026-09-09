"""
Scraper for web3.career.

robots.txt check (2026-09-08): only `Disallow: /metrics*`; everything else — including
the `/{keyword}-jobs` search pages and individual job pages this scraper visits — is
open to crawlers.

Unlike findweb3.com, web3.career is fully server-rendered (a Rails/Turbo app — no client
-side JS is needed to see job data), so this scraper uses plain HTTP requests instead of
Selenium: a `requests.Session` stands in for Fw3Scraper's Selenium `driver` throughout.

Each job detail page embeds a schema.org `JobPosting` JSON-LD block, which is the most
stable, semantic source available for Job Title and Company Name — far less likely to
break on a front-end redesign than a generated CSS class. It's deliberately NOT used for
Job Location or Compensation: spot-checking multiple listings showed the JSON-LD always
reports `jobLocationType: "TELECOMMUTE"` / `addressLocality: "Anywhere"` and a synthetic
`baseSalary` even for jobs with a specific city and no posted salary — boilerplate for
search-engine visibility, not the real values. The real values are the plain-text
"Location: ..." / "Compensation: ..." labels rendered on the page itself, which is what
this scraper parses instead. The Job Description HTML comes from the page's own
`div.font-body` container (verified to appear exactly once per detail page) — kept as
raw HTML, unsanitized, matching Fw3Scraper's behavior.
"""

import html
import json
import logging
import random
import re
import time
from collections.abc import Callable
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from utils.filters import (
    job_text,
    passes_anti_keywords,
    passes_discarded_urls,
    passes_experience,
    passes_location,
)

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# A job detail link on a search-results page, e.g. "/staff-engineer-python-zscaler/153659".
_JOB_HREF_PATTERN = re.compile(r'href="(/[a-z0-9-]+/\d+)"')


class Web3CareerScraper:
    """Scraper for web3.career job listings."""

    _MAX_PAGES = 20  # safety cap on pagination per keyword, in case the site never signals "done"

    def __init__(
        self,
        max_jobs_per_keyword: int = 20,
        top_n_results: int = 10,
        acceptable_locations: list | None = None,
        anti_keywords: list | None = None,
        discarded_urls: list | None = None,
        max_experience_years: int | None = None,
        on_progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        self.max_jobs_per_keyword = max_jobs_per_keyword
        self.top_n_results = top_n_results
        self.acceptable_locations = acceptable_locations or []
        self.anti_keywords = anti_keywords or []
        self.discarded_urls = set(discarded_urls or [])
        self.max_experience_years = max_experience_years
        self.on_progress = on_progress

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self, keywords: list) -> list:
        """
        Same pipeline as Fw3Scraper.run(): scrape all keyword pages → deduplicate →
        visit every candidate, filtering each inline as soon as its fields are
        extracted (passes_location → passes_anti_keywords → passes_experience), score
        survivors by keyword-match count, then rank and cut to top_n_results only after
        every candidate has been visited. discarded_urls is filtered during search
        itself (see search_by_keyword) so a discarded job never occupies one of the
        max_jobs_per_keyword slots.
        """
        session = self._build_session()
        ordered_urls: dict = {}

        candidates: list = []
        drop_counts = {"discarded_urls": 0, "location": 0, "anti_keywords": 0, "experience": 0}

        try:
            for i, kw in enumerate(keywords, start=1):
                self._report("searching_keywords", i, len(keywords))
                urls, discarded_count = self.search_by_keyword(session, kw)
                drop_counts["discarded_urls"] += discarded_count
                for url in urls:
                    ordered_urls[url] = None

            log.info("Unique job URLs to scrape: %d", len(ordered_urls))

            for n, url in enumerate(ordered_urls, start=1):
                log.info(f"Scrapped: {n} / {len(ordered_urls)}")
                self._report("scraping", n, len(ordered_urls))
                raw = self.extract_job_detail(session, url)
                time.sleep(random.uniform(0.4, 0.9))
                if not raw:
                    continue

                job = {
                    "Job Title": raw["name"],
                    "Company Name": raw["company_name"],
                    "Job Description": raw["description_html"],
                    "Job Location": raw["location"],
                    "Compensation": raw["compensation"],
                    "Job Url": raw["url"],
                }

                if not passes_location(job, self.acceptable_locations):
                    drop_counts["location"] += 1
                    continue
                if not passes_anti_keywords(job, self.anti_keywords):
                    drop_counts["anti_keywords"] += 1
                    continue
                if not passes_experience(job, self.max_experience_years):
                    drop_counts["experience"] += 1
                    continue

                tags = self.match_keywords(job_text(job), keywords)
                job["Tags"] = ", ".join(tags)
                job["Notes"] = ""

                candidates.append((len(tags), job))

        finally:
            session.close()

        candidates.sort(key=lambda pair: pair[0], reverse=True)
        selected = candidates[: self.top_n_results]

        total_seen = len(ordered_urls) + drop_counts["discarded_urls"]
        log.info(
            "Filtered %d candidate(s) found: %d passed "
            "(dropped — discarded_urls: %d, location: %d, anti_keywords: %d, experience: %d)",
            total_seen,
            len(candidates),
            drop_counts["discarded_urls"],
            drop_counts["location"],
            drop_counts["anti_keywords"],
            drop_counts["experience"],
        )
        self._report("filtering", len(candidates), len(ordered_urls))
        if selected:
            scores = [score for score, _ in selected]
            log.info(
                "Selected top %d of %d passing candidate(s) — score min: %d, max: %d",
                len(selected), len(candidates), min(scores), max(scores),
            )

        return [job for _, job in selected]

    def search_by_keyword(self, session: requests.Session, keyword: str) -> tuple[list, int]:
        """
        Page through https://web3.career/{keyword}-jobs?page=N and return up to
        self.max_jobs_per_keyword job-detail URLs, as (job_urls, discarded_count).

        Discarded URLs are skipped as they're encountered — before they can consume one
        of the max_jobs_per_keyword slots — and scanning continues onto further pages
        until that many valid URLs are collected or results run out, mirroring
        Fw3Scraper's within-page behavior but across web3.career's paginated listing.

        Multi-word keywords (e.g. "smart contracts") are hyphenated for the URL slug;
        a slug the site doesn't recognize renders its custom 404 page with zero job
        rows, which this method treats the same as "no results" for that keyword.
        """
        slug = quote(keyword.strip().lower().replace(" ", "-"), safe="-")
        job_urls: list = []
        seen: set = set()
        discarded_count = 0
        page = 1

        while len(job_urls) < self.max_jobs_per_keyword and page <= self._MAX_PAGES:
            url = f"https://web3.career/{slug}-jobs?page={page}"
            log.info("Searching keyword '%s' page %d → %s", keyword, page, url)
            try:
                response = session.get(url, timeout=15)
                response.raise_for_status()
            except requests.RequestException as exc:
                log.warning("Failed to load '%s': %s", url, exc)
                break

            hrefs = self._extract_job_hrefs(response.text)
            if not hrefs:
                break  # no results for this keyword, or pagination exhausted

            found_new = False
            for href in hrefs:
                full_url = f"https://web3.career{href}"
                if full_url in seen:
                    continue
                seen.add(full_url)
                found_new = True

                if not passes_discarded_urls(full_url, self.discarded_urls):
                    discarded_count += 1
                    continue

                job_urls.append(full_url)
                if len(job_urls) >= self.max_jobs_per_keyword:
                    break

            if not found_new:
                break  # this page repeated only already-seen links — site clamped to last page

            page += 1
            if len(job_urls) < self.max_jobs_per_keyword and page <= self._MAX_PAGES:
                time.sleep(random.uniform(0.4, 0.9))

        log.info("  → %d job link(s) found (%d discarded)", len(job_urls), discarded_count)
        return job_urls, discarded_count

    def extract_job_detail(self, session: requests.Session, job_url: str) -> dict | None:
        """
        Fetch a web3.career job detail page and extract structured fields. Returns None
        on unrecoverable failure so the caller can skip and continue.
        """
        try:
            response = session.get(job_url, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Skipping %s — failed to load: %s", job_url, exc)
            return None

        posting = self._extract_job_posting(response.text)
        if not posting or not posting.get("title"):
            log.warning("Skipping %s — no JobPosting data found", job_url)
            return None

        name = html.unescape(posting["title"]).strip()
        company_name = html.unescape(
            (posting.get("hiringOrganization") or {}).get("name") or ""
        ).strip() or None

        return {
            "name": name,
            "url": job_url,
            "location": self._extract_labeled_text(response.text, "Location"),
            "company_name": company_name,
            "compensation": self._extract_labeled_text(response.text, "Compensation"),
            "description_html": self._extract_description_html(response.text),
        }

    @staticmethod
    def match_keywords(text: str, keywords: list) -> list:
        """Return the subset of keywords (original casing) present in text (case-insensitive)."""
        lowered = text.lower()
        return [kw for kw in keywords if kw.lower() in lowered]

    # ── Private helpers ────────────────────────────────────────────────────────

    def _report(self, stage: str, current: int, total: int) -> None:
        """Invoke the optional progress callback, if one was provided."""
        if self.on_progress:
            self.on_progress(stage, current, total)

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        return session

    @staticmethod
    def _extract_job_hrefs(page_html: str) -> list:
        """
        Return job-detail hrefs in first-seen order, deduplicated — each job row's
        title and company both link to the same href, so it appears twice per posting.
        """
        seen: set = set()
        ordered: list = []
        for href in _JOB_HREF_PATTERN.findall(page_html):
            if href not in seen:
                seen.add(href)
                ordered.append(href)
        return ordered

    @staticmethod
    def _extract_job_posting(page_html: str) -> dict | None:
        """
        Return the first schema.org JobPosting JSON-LD block on the page — empirically
        always the page's own job (further blocks, if any, describe "similar jobs"
        cards rendered further down the same page).
        """
        soup = BeautifulSoup(page_html, "html.parser")
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text()
            if not raw or not raw.strip():
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("@type") == "JobPosting":
                return data
        return None

    @staticmethod
    def _extract_labeled_text(page_html: str, label: str) -> str | None:
        """
        Return the plain text immediately following "{label}: " up to the next tag, or
        None if that label isn't present on the page (e.g. no salary was posted).
        """
        match = re.search(rf"{label}:\s*([^<]+)", page_html)
        if not match:
            return None
        text = match.group(1).strip()
        return text or None

    @staticmethod
    def _extract_description_html(page_html: str) -> str | None:
        """Return the raw (unsanitized) inner HTML of the job's description container."""
        soup = BeautifulSoup(page_html, "html.parser")
        container = soup.find("div", class_="font-body")
        if not container:
            return None
        inner = container.decode_contents().strip()
        return inner or None
