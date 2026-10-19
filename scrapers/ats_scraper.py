"""
Greenhouse, Lever and Ashby public job-board APIs (documented, read-only GETs; no
browser, no employer credentials, no application submission). One GET returns a whole
board, so there is no per-job detail fetch. Boards are not keyword-searchable: postings
are kept only if at least one keyword hits (shared `term_hits` rule via BaseScraper).

A failing board is recorded in `self.errors` as {source: "<ats>:<token>", error} and never
stops the other boards; the scraper only raises when every board failed.
"""

import html
import logging
import time

from scrapers.base import BaseScraper
from utils import http
from utils.normalize import build_row

log = logging.getLogger(__name__)

BOARD_GAP_SECONDS = 1.0


def _pretty(token: str) -> str:
    return token.replace("-", " ").replace("_", " ").title()


def _mode(value) -> str | None:
    return {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite"}.get(
        str(value or "").replace(" ", "").lower()
    )


class AtsScraper(BaseScraper):
    ATS = ""  # greenhouse | lever | ashby

    def __init__(self, boards: list | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.boards = list(boards or [])

    # ── Vendor specifics (overridden) ──────────────────────────────────────────

    def _request(self, token: str) -> tuple[str, dict | None]:
        raise NotImplementedError

    def _postings(self, payload) -> list:
        raise NotImplementedError

    def _map(self, raw: dict, token: str) -> tuple[dict, str | None] | None:
        raise NotImplementedError

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self, keywords: list) -> list:
        candidates: list = []
        drops: dict = {"discarded_urls": 0}
        seen = 0
        for i, token in enumerate(self.boards, start=1):
            if self.expired():
                break
            self._report("scraping", i, len(self.boards))
            try:
                url, params = self._request(token)
                payload = http.get(url, params=params).json()
            except Exception as exc:  # per-board isolation
                log.warning("%s:%s failed: %s", self.ATS, token, exc)
                self.errors.append({"source": f"{self.ATS}:{token}", "error": str(exc)[:200]})
                continue
            for raw in self._postings(payload):
                mapped = self._map(raw, token) if isinstance(raw, dict) else None
                if not mapped:
                    continue
                row, listing_url = mapped
                seen += 1
                if self.skip_url(row["canonical_url"]) or any(
                    u in self.discarded_urls for u in (listing_url, row["apply_url"]) if u
                ):
                    drops["discarded_urls"] += 1
                    continue
                score = self.evaluate(row, keywords, drops, require_keyword=True)
                if score is not None:
                    candidates.append((score, row))
            if i < len(self.boards):
                time.sleep(BOARD_GAP_SECONDS)

        if self.boards and len(self.errors) == len(self.boards):
            raise RuntimeError(f"all {self.ATS} boards failed")
        return self.select(candidates, drops, seen)

    def _row(self, token, *, id, title, company, location, description, listing_url,
             canonical, apply_url, posted_at, salary, work_mode) -> tuple[dict, str | None]:
        row = build_row(
            {"Job Title": title, "Company Name": company or _pretty(token),
             "Job Description": description, "Job Location": location,
             "Compensation": salary, "Job Url": canonical},
            self.ATS, source_job_id=id, canonical=canonical, apply_url=apply_url or listing_url,
            posted_at=posted_at, work_mode=work_mode, ats_type=self.ATS,
        )
        return row, listing_url


class GreenhouseScraper(AtsScraper):
    ATS = "greenhouse"

    def _request(self, token):
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", {"content": "true"}

    def _postings(self, payload):
        return payload.get("jobs") or []

    def _map(self, raw, token):
        if raw.get("id") is None:
            return None
        jid = str(raw["id"])
        return self._row(
            token, id=jid, title=raw.get("title"), company=raw.get("company_name"),
            location=(raw.get("location") or {}).get("name"),
            description=html.unescape(raw["content"]) if raw.get("content") else None,
            listing_url=raw.get("absolute_url"),
            canonical=f"https://boards.greenhouse.io/{token}/jobs/{jid}",
            apply_url=raw.get("absolute_url"),
            posted_at=raw.get("first_published") or raw.get("updated_at"), salary=None, work_mode=None,
        )


class LeverScraper(AtsScraper):
    ATS = "lever"

    def _request(self, token):
        return f"https://api.lever.co/v0/postings/{token}", {"mode": "json"}

    def _postings(self, payload):
        return payload if isinstance(payload, list) else []

    def _map(self, raw, token):
        if not raw.get("id"):
            return None
        cats = raw.get("categories") or {}
        body = (raw.get("description") or "") + "".join(
            f"<h3>{lst.get('text', '')}</h3><ul>{lst.get('content', '')}</ul>"
            for lst in raw.get("lists") or [] if isinstance(lst, dict)
        ) + (raw.get("additional") or "")
        sr = raw.get("salaryRange") or {}
        salary = (f"{sr.get('min')}-{sr.get('max')} {sr.get('currency', '')} {sr.get('interval', '')}".strip()
                  if sr.get("min") or sr.get("max") else raw.get("salaryDescriptionPlain"))
        return self._row(
            token, id=raw["id"], title=raw.get("text"), company=None, location=cats.get("location"),
            description=body or None, listing_url=raw.get("hostedUrl"),
            canonical=f"https://jobs.lever.co/{token}/{raw['id']}", apply_url=raw.get("applyUrl"),
            posted_at=raw.get("createdAt"), salary=salary, work_mode=_mode(raw.get("workplaceType")),
        )


class AshbyScraper(AtsScraper):
    ATS = "ashby"

    def _request(self, token):
        return f"https://api.ashbyhq.com/posting-api/job-board/{token}", {"includeCompensation": "true"}

    def _postings(self, payload):
        return payload.get("jobs") or []

    def _map(self, raw, token):
        if not raw.get("id") or raw.get("isListed") is False:
            return None
        comp = raw.get("compensation") or {}
        mode = _mode(raw.get("workplaceType")) or ("remote" if raw.get("isRemote") else None)
        return self._row(
            token, id=raw["id"], title=(raw.get("title") or "").strip(), company=None,
            location=raw.get("location"), description=raw.get("descriptionHtml"),
            listing_url=raw.get("jobUrl"), canonical=f"https://jobs.ashbyhq.com/{token}/{raw['id']}",
            apply_url=raw.get("applyUrl"), posted_at=raw.get("publishedAt"),
            salary=comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary"),
            work_mode=mode,
        )
