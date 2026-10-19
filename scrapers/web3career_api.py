"""
web3.career official API adapter: GET https://web3.career/api/v1 with the owner's token
from env WEB3CAREER_API_TOKEN (query param, redacted from every log line and error).

Verified 2026-10-19 against docs.bondex.app and one live call (fixture:
tests/fixtures/web3/web3career_api.json): the response is a top-level JSON array of mixed
types, `[str, str, [job, ...]]`; there is NO paging (only `limit` <= 100, one `tag` per
request), so this adapter makes one request per keyword. Invalid tags return an empty list
(HTTP 200). Job fields seen live: id, title, company, location, description (HTML), tags,
apply_url, date, date_epoch, is_remote, country, city, salary_min_value, salary_max_value,
salary_currency, salary_unit. There is no `url` field; the posting URL is derived as
https://web3.career/<slug(title company)>/<id>.

Terms: `apply_url` is passed through byte-for-byte (no tracking params, no rewriting).
"""

import logging
import os
import re
import time
from email.utils import parsedate_to_datetime

from scrapers.base import BaseScraper
from utils import http
from utils.normalize import build_row, norm_text

log = logging.getLogger(__name__)

API_URL = "https://web3.career/api/v1"
TOKEN_ENV = "WEB3CAREER_API_TOKEN"
REQUEST_GAP_SECONDS = 1.0


def _slug(text: str) -> str:
    return re.sub(r"\s+", "-", norm_text(text))


def _jobs_from(data) -> list:
    """Find the jobs array in the mixed-type root array (defensive, per the docs)."""
    if not isinstance(data, list):
        raise ValueError("unexpected response format")
    jobs = next((x for x in data if isinstance(x, list)), None)
    if jobs is None and data and isinstance(data[0], dict):
        jobs = data
    return [j for j in (jobs or []) if isinstance(j, dict)]


def _salary(job: dict) -> str | None:
    lo, hi = job.get("salary_min_value"), job.get("salary_max_value")
    if not (lo or hi):
        return None
    fmt = lambda v: f"{float(v):g}"  # noqa: E731
    amount = f"{fmt(lo)}-{fmt(hi)}" if lo and hi else fmt(lo or hi)
    return " ".join(p for p in (amount, job.get("salary_currency"), "/ " + job["salary_unit"].lower()
                                if job.get("salary_unit") else None) if p)


def _posted_at(job: dict):
    if job.get("date_epoch"):
        return job["date_epoch"]
    try:
        return parsedate_to_datetime(job["date"])
    except (KeyError, TypeError, ValueError):
        return None


class Web3CareerApiScraper(BaseScraper):
    def run(self, keywords: list) -> list:
        token = os.environ.get(TOKEN_ENV)
        if not token:
            raise RuntimeError(f"{TOKEN_ENV} not set")
        limit = min(max(self.max_jobs_per_keyword, 1), 100)
        candidates: list = []
        drops: dict = {"discarded_urls": 0}
        seen: set = set()

        for i, kw in enumerate(keywords, start=1):
            if self.expired():
                break
            self._report("searching_keywords", i, len(keywords))
            params = {"token": token, "tag": _slug(kw), "limit": limit, "show_description": "true"}
            try:
                jobs = _jobs_from(http.get(API_URL, params=params, redact=[token]).json())
            except Exception as exc:  # isolate per keyword; message already redacted by http.get
                message = str(exc).replace(token, "REDACTED")[:200]
                log.warning("web3.career API keyword '%s' failed: %s", kw, message)
                self.errors.append({"source": "web3career", "error": f"{kw}: {message}"})
                continue
            for raw in jobs:
                row = self._map(raw)
                if row is None or row["source_job_id"] in seen:
                    continue
                seen.add(row["source_job_id"])
                if self.skip_url(row["canonical_url"]):
                    drops["discarded_urls"] += 1
                    continue
                score = self.evaluate(row, keywords, drops)
                if score is not None:
                    candidates.append((score, row))
            if i < len(keywords):
                time.sleep(REQUEST_GAP_SECONDS)

        if keywords and len(self.errors) == len(keywords):
            raise RuntimeError("; ".join(e["error"] for e in self.errors)[:200])
        return self.select(candidates, drops, len(seen))

    @staticmethod
    def _map(raw: dict) -> dict | None:
        if raw.get("id") in (None, ""):
            return None
        jid = str(raw["id"])
        title, company = (raw.get("title") or "").strip(), (raw.get("company") or "").strip()
        url = f"https://web3.career/{_slug(f'{title} {company}') or 'job'}/{jid}"
        return build_row(
            {"Job Title": title or None, "Company Name": company or None,
             "Job Description": raw.get("description"), "Job Location": (raw.get("location") or "").strip() or None,
             "Compensation": _salary(raw), "Job Url": url},
            "web3career", source_job_id=jid, canonical=url, apply_url=raw.get("apply_url"),
            posted_at=_posted_at(raw), work_mode="remote" if raw.get("is_remote") is True else None,
            id_fallback=True,
        )
