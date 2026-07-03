"""
Scraper for findweb3.com.

robots.txt check (2026-07-03): only a Sitemap directive, no Disallow rules.
All paths are open to crawlers.
"""

import logging
import random
import re
import time
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

log = logging.getLogger(__name__)


class Fw3Scraper:
    """Scraper for findweb3.com job listings."""

    def __init__(self, max_jobs_per_keyword: int = 20, top_n_results: int = 10) -> None:
        self.max_jobs_per_keyword = max_jobs_per_keyword
        self.top_n_results = top_n_results

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self, keywords: list) -> list:
        """
        Full pipeline: scrape all keyword pages → deduplicate → fetch details
        → score → sort → top-N.

        Accepts keywords as input; returns a clean list of job dicts (no _ fields).
        Does not read or write any files.
        """
        driver = self._build_driver()
        ordered_urls: dict = {}  # dict used as an ordered set

        try:
            for kw in keywords:
                for url in self.search_by_keyword(driver, kw):
                    ordered_urls[url] = None

            log.info("Unique job URLs to scrape: %d", len(ordered_urls))
            raw_jobs = []

            for url in ordered_urls:
                detail = self.extract_job_detail(driver, url)
                if detail:
                    raw_jobs.append(detail)
                time.sleep(random.uniform(1, 2))

        finally:
            driver.quit()

        for job in raw_jobs:
            job["_score"] = self.compute_match_score(job.get("_page_text", ""), keywords)

        raw_jobs.sort(key=lambda j: j["_score"], reverse=True)

        return [
            {k: v for k, v in job.items() if not k.startswith("_")}
            for job in raw_jobs[: self.top_n_results]
        ]

    def search_by_keyword(self, driver: webdriver.Chrome, keyword: str) -> list:
        """
        Load the findweb3.com listing page for a keyword and return up to
        self.max_jobs_per_keyword job-detail URLs (/job/<id> pattern).

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
            return []

        job_urls = []
        seen = set()
        for a in driver.find_elements(By.TAG_NAME, "a"):
            href = (a.get_attribute("href") or "").strip()
            if re.search(r"findweb3\.com/job/[^/]+$", href) and href not in seen:
                seen.add(href)
                job_urls.append(href)
                if len(job_urls) >= self.max_jobs_per_keyword:
                    break

        log.info("  → %d job link(s) found", len(job_urls))
        return job_urls

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

        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text
        except NoSuchElementException:
            page_text = name

        location = self._find_field(driver, [
            "//*[contains(@class,'location')]",
            "//*[contains(@data-testid,'location')]",
            "//span[contains(text(),'Remote') or contains(text(),'remote')]",
            "//li[contains(text(),'Remote') or contains(text(),'Location')]",
        ])

        job_type = self._find_field(driver, [
            "//*[contains(@class,'type') or contains(@class,'job-type')]",
            "//*[contains(@data-testid,'type')]",
            "//span[contains(text(),'Full Time') or contains(text(),'Part Time') "
            "     or contains(text(),'Contract') or contains(text(),'Internship')]",
            "//li[contains(text(),'Full Time') or contains(text(),'Part Time') "
            "    or contains(text(),'Contract') or contains(text(),'Internship')]",
        ])

        compensation = self._find_field(driver, [
            "//*[contains(@class,'salary') or contains(@class,'compensation')]",
            "//*[contains(@data-testid,'salary') or contains(@data-testid,'compensation')]",
            "//span[contains(text(),'$') or contains(text(),'USD') or contains(text(),'salary')]",
            "//li[contains(text(),'$') or contains(text(),'USD')]",
        ])

        application_url = self._find_apply_url(driver, job_url)

        return {
            "name": name,
            "url": job_url,
            "location": location,
            "type": job_type,
            "compensation": compensation,
            "application_url": application_url,
            "_page_text": page_text,
        }

    @staticmethod
    def compute_match_score(job_text: str, keywords: list) -> int:
        """Count unique keywords present in job_text (case-insensitive, presence only)."""
        text = job_text.lower()
        return sum(1 for kw in keywords if kw.lower() in text)

    # ── Private helpers ────────────────────────────────────────────────────────

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

    def _find_field(self, driver: webdriver.Chrome, xpaths: list) -> str | None:
        """Try each XPath; return the first non-empty text match, or None."""
        for xpath in xpaths:
            try:
                text = driver.find_element(By.XPATH, xpath).text.strip()
                if text:
                    return text
            except NoSuchElementException:
                continue
        return None

    def _find_apply_url(self, driver: webdriver.Chrome, fallback: str) -> str:
        """
        Return the external application URL from the Apply button/link.
        Primary: div.JobPage_jobApplyCard__tFzYs a.mediumButton (findweb3-specific).
        Fallback: any external <a> not pointing to findweb3.com.
        """
        try:
            el = driver.find_element(
                By.CSS_SELECTOR, "div.JobPage_jobApplyCard__tFzYs a.mediumButton"
            )
            href = el.get_attribute("href") or ""
            if href:
                return href
        except NoSuchElementException:
            pass

        for xpath in [
            "//a[contains(translate(normalize-space(text()),'APPLY','apply'),'apply') "
            "    and contains(@href,'http') and not(contains(@href,'findweb3'))]",
            "//a[contains(@href,'http') and not(contains(@href,'findweb3.com'))]",
        ]:
            try:
                href = driver.find_element(By.XPATH, xpath).get_attribute("href") or ""
                if href:
                    return href
            except NoSuchElementException:
                continue

        return fallback
