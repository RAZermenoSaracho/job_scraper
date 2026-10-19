"""
Scraper for findweb3.com.

robots.txt check (2026-07-03): only a Sitemap directive, no Disallow rules.
All paths are open to crawlers.
"""

import logging
import random
import re
import time
from collections.abc import Callable
from urllib.parse import quote

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from utils.filters import (
    job_text,
    passes_anti_keywords,
    passes_discarded_urls,
    passes_experience,
    passes_location,
)
from utils.normalize import build_row, identify_url, set_tags

log = logging.getLogger(__name__)

# Matches a compensation value or range, e.g. "$125K – $273K", "€85,176K", "£90K-£120K".
# Currency symbol may appear once (start only) or on both sides of the range.
COMPENSATION_PATTERN = re.compile(
    r"[€$£¥]\s?\d+(?:[.,]\d+)?K(?:\s*[-–—]\s*[€$£¥]?\s?\d+(?:[.,]\d+)?K)?"
)


class Fw3Scraper:
    """Scraper for findweb3.com job listings."""

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
        Full pipeline: scrape all keyword pages → deduplicate → visit every candidate,
        filtering each one inline as soon as its fields are extracted. discarded_urls is
        filtered during search itself (see search_by_keyword) so a discarded job never
        occupies one of the max_jobs_per_keyword slots and never costs a page load; every
        URL that reaches this stage has already passed that check. Every candidate that
        survives the remaining filters is scored by keyword-match count; once all
        candidates have been visited, the surviving pool is ranked by score and the
        top_n_results best are returned — not just the first ones found to pass.

        Accepts keywords as input; returns a clean list of job dicts matching the
        output schema (Job Title, Company Name, Job Description, Job Location,
        Compensation, Job Url, Tags, Notes). Does not read or write any files.

        If on_progress was passed to __init__, it's called as on_progress(stage, current,
        total) at the same points this method logs progress — callers (e.g. an API layer
        tracking a background job) can use it to report progress without this class knowing
        anything about how that's surfaced.
        """
        driver = self._build_driver()
        ordered_urls: dict = {}  # dict used as an ordered set

        candidates: list = []  # (score, job) for every candidate that passed all filters
        drop_counts = {"discarded_urls": 0, "location": 0, "anti_keywords": 0, "experience": 0}

        try:
            for i, kw in enumerate(keywords, start=1):
                self._report("searching_keywords", i, len(keywords))
                urls, discarded_count = self.search_by_keyword(driver, kw)
                drop_counts["discarded_urls"] += discarded_count
                for url in urls:
                    ordered_urls[url] = None

            log.info("Unique job URLs to scrape: %d", len(ordered_urls))

            for n, url in enumerate(ordered_urls, start=1):
                log.info(f"Scrapped: {n} / {len(ordered_urls)}")
                self._report("scraping", n, len(ordered_urls))
                raw = self.extract_job_detail(driver, url)
                time.sleep(random.uniform(1, 2))
                if not raw:
                    continue

                job = build_row(
                    {
                        "Job Title": raw["name"],
                        "Company Name": raw["company_name"],
                        "Job Description": raw["description_html"],
                        "Job Location": raw["location"],
                        "Compensation": raw["compensation"],
                        "Job Url": raw["url"],
                    },
                    "findweb3",
                    source_job_id=identify_url(raw["url"])["source_job_id"],
                    id_fallback=True,
                )

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
                set_tags(job, tags)

                candidates.append((len(tags), job))

        finally:
            driver.quit()

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

    def search_by_keyword(self, driver: webdriver.Chrome, keyword: str) -> tuple[list, int]:
        """
        Load the findweb3.com listing page for a keyword and return up to
        self.max_jobs_per_keyword job-detail URLs (/job/<id> pattern), as
        (job_urls, discarded_count).

        Discarded URLs are skipped as they're encountered, before they can consume one
        of the max_jobs_per_keyword slots — scanning continues over further links on
        the page until that many valid URLs are collected, or the page's links run out.
        This is a single already-loaded page's DOM, so "keep looking" costs nothing
        extra over the network; it just means not stopping the scan early.

        Multi-word keywords (e.g. "smart contracts") are hyphenated for the URL.
        """
        slug = quote(keyword.replace(" ", "-"), safe="-")
        url = f"https://findweb3.com/jobs/{slug}"
        log.info("Searching keyword '%s' → %s", keyword, url)

        try:
            driver.get(url)
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "a"))
            )
            time.sleep(random.uniform(1.5, 2.5))
        except TimeoutException:
            log.warning("Timeout loading '%s'", url)
            return [], 0

        job_urls = []
        seen = set()
        discarded_count = 0
        for a in driver.find_elements(By.TAG_NAME, "a"):
            href = (a.get_attribute("href") or "").strip()
            if not re.search(r"findweb3\.com/job/[^/]+$", href) or href in seen:
                continue
            seen.add(href)

            if not passes_discarded_urls(href, self.discarded_urls):
                discarded_count += 1
                continue

            job_urls.append(href)
            if len(job_urls) >= self.max_jobs_per_keyword:
                break

        log.info("  → %d job link(s) found (%d discarded)", len(job_urls), discarded_count)
        return job_urls, discarded_count

    def extract_job_detail(self, driver: webdriver.Chrome, job_url: str) -> dict | None:
        """
        Navigate to a findweb3.com job detail page and extract structured fields.
        Returns None on unrecoverable failure so the caller can skip and continue.
        """
        try:
            driver.get(job_url)
            WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.TAG_NAME, "h1"))
            )
            time.sleep(random.uniform(1, 2))
        except (TimeoutException, WebDriverException) as exc:
            log.warning("Skipping %s — failed to load: %s", job_url, exc)
            return None

        try:
            name = driver.find_element(By.TAG_NAME, "h1").text.strip()
        except NoSuchElementException:
            log.warning("Skipping %s — no <h1> found", job_url)
            return None

        company_name = self._wait_and_get_text(driver, "h2.JobPage_companyName__G6Nta")
        location = self._wait_and_get_text(driver, "div.JobPage_companyLocation__jz0CV")
        compensation = self._find_compensation(driver)

        description_html = self._find_html_field(driver, [
            "//*[contains(translate(@class,'DESCRIPTION','description'),'description')]",
            "//*[contains(translate(@data-testid,'DESCRIPTION','description'),'description')]",
        ])

        return {
            "name": name,
            "url": job_url,
            "location": location,
            "company_name": company_name,
            "compensation": compensation,
            "description_html": description_html,
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

    def _build_driver(self) -> webdriver.Chrome:
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--window-size=1280,900")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_argument(
            "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=opts)

    def _wait_and_get_text(self, driver: webdriver.Chrome, css_selector: str, timeout: int = 5) -> str | None:
        """Wait for a CSS selector to render, then return its stripped text, or None."""
        try:
            el = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
            )
            text = el.text.strip()
            return text or None
        except (TimeoutException, NoSuchElementException):
            return None

    def _find_compensation(self, driver: webdriver.Chrome) -> str | None:
        """
        Return the first compensation match found across all div.JobPage_keyDetail__Dddhf
        elements, or None if none contain a recognizable compensation value/range.
        """
        for el in driver.find_elements(By.CSS_SELECTOR, "div.JobPage_keyDetail__Dddhf"):
            match = COMPENSATION_PATTERN.search(el.text)
            if match:
                return match.group(0)
        return None

    def _find_html_field(self, driver: webdriver.Chrome, xpaths: list) -> str | None:
        """Try each XPath; return the first match's raw innerHTML (unsanitized), or None."""
        for xpath in xpaths:
            try:
                html = driver.find_element(By.XPATH, xpath).get_attribute("innerHTML")
                if html and html.strip():
                    return html
            except NoSuchElementException:
                continue
        return None
