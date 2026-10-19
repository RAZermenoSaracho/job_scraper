"""
Hashtag Web3 JSON feed adapter: ONE GET of https://hashtagweb3.com/data/jobs-runtime.json
per run (never per keyword), for personal non-commercial use only. Cached in memory by
ETag/Last-Modified (conditional request; a 304 reuses the cached body). Retry-After and
exponential backoff with jitter are handled by utils.http. The feed has no descriptions, so
keyword matching sees the title only (keywords are required to hit, as for ATS boards).

Feed fields (verified 2026-10-19): id, title, company, link, date (ISO, mixed offsets),
location, workplaceType, isRemote, active, salary, department, slug. `link` is usually a
Greenhouse/Lever/Ashby URL, so canonical_url converges with the ATS adapters' rows.
"""

import logging

from scrapers.base import BaseScraper
from utils import http
from utils.normalize import build_row

log = logging.getLogger(__name__)

FEED_URL = "https://hashtagweb3.com/data/jobs-runtime.json"
_cache: dict = {}  # {"etag", "last_modified", "body"}; module-level so it survives runs


def _mode(value) -> str | None:
    return {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}.get(str(value or "").replace("-", "").lower())


def fetch_feed() -> list:
    headers = {}
    if _cache.get("etag"):
        headers["If-None-Match"] = _cache["etag"]
    if _cache.get("last_modified"):
        headers["If-Modified-Since"] = _cache["last_modified"]
    response = http.get(FEED_URL, headers=headers, ok=(200, 304))
    if response.status_code == 304 and "body" in _cache:
        return _cache["body"]
    body = response.json()
    if not isinstance(body, list):
        raise ValueError("unexpected feed format")
    if response.headers.get("ETag") or response.headers.get("Last-Modified"):
        _cache.update(etag=response.headers.get("ETag"), last_modified=response.headers.get("Last-Modified"),
                      body=body)
    return body


class HashtagWeb3Scraper(BaseScraper):
    def run(self, keywords: list) -> list:
        if self.expired():
            return []
        self._report("scraping", 1, 1)
        feed = fetch_feed()
        candidates: list = []
        drops: dict = {"discarded_urls": 0}
        seen: set = set()
        for raw in feed:
            row = self._map(raw) if isinstance(raw, dict) and raw.get("active", True) else None
            if row is None or row["source_job_id"] in seen:
                continue
            seen.add(row["source_job_id"])
            if self.skip_url(row["canonical_url"]) or raw.get("link") in self.discarded_urls:
                drops["discarded_urls"] += 1
                continue
            score = self.evaluate(row, keywords, drops, require_keyword=True)
            if score is not None:
                candidates.append((score, row))
        return self.select(candidates, drops, len(seen))

    @staticmethod
    def _map(raw: dict) -> dict | None:
        link = raw.get("link")
        if not link:
            return None
        return build_row(
            {"Job Title": raw.get("title"), "Company Name": raw.get("company"), "Job Description": None,
             "Job Location": raw.get("location"), "Compensation": raw.get("salary"), "Job Url": link},
            "hashtagweb3", source_job_id=None if raw.get("id") in (None, "") else str(raw["id"]), apply_url=link, posted_at=raw.get("date"),
            work_mode=_mode(raw.get("workplaceType")) or ("remote" if raw.get("isRemote") is True else None),
            id_fallback=True,
        )
