"""
Shared scraper plumbing: common constructor kwargs, the single post-fetch filter chain,
scoring, ranking, and progress/deadline helpers. Site-specific modules only fetch and
map; they must not re-implement matching or filtering.
"""

import logging
import time
from collections.abc import Callable

from utils.filters import (
    expand_known_keys,
    job_text,
    passes_anti_keywords,
    passes_discarded_urls,
    passes_excluded_companies,
    passes_experience,
    passes_location,
    passes_seniority,
    passes_since,
    passes_work_mode,
    row_is_known,
    term_hits,
    url_is_known,
)
from utils.normalize import set_tags

log = logging.getLogger(__name__)


class BaseScraper:
    def __init__(
        self,
        max_jobs_per_keyword: int = 20,
        top_n_results: int = 10,
        acceptable_locations: list | None = None,
        anti_keywords: list | None = None,
        discarded_urls: list | None = None,
        max_experience_years: int | None = None,
        on_progress: Callable[[str, int, int], None] | None = None,
        known_keys: list | None = None,
        since=None,
        work_modes: list | None = None,
        seniority_exclude: list | None = None,
        excluded_companies: list | None = None,
        match_mode: str = "substring",
        deadline: float | None = None,
    ) -> None:
        self.max_jobs_per_keyword = max_jobs_per_keyword
        self.top_n_results = top_n_results
        self.acceptable_locations = acceptable_locations or []
        self.anti_keywords = anti_keywords or []
        self.discarded_urls = set(discarded_urls or [])
        self.max_experience_years = max_experience_years
        self.on_progress = on_progress
        self.known_keys = expand_known_keys(known_keys)
        self.since = since
        self.work_modes = work_modes or []
        self.seniority_exclude = seniority_exclude or []
        self.excluded_companies = excluded_companies or []
        self.match_mode = match_mode
        self.deadline = deadline  # time.monotonic() value; the app may set it after build
        self.deadline_hit = False
        self.errors: list = []  # non-fatal {source, error} entries collected by the app

    # ── Shared helpers ─────────────────────────────────────────────────────────

    def _report(self, stage: str, current: int, total: int) -> None:
        """Invoke the optional progress callback, if one was provided."""
        if self.on_progress:
            self.on_progress(stage, current, total)

    def expired(self) -> bool:
        """True (and remembered) once the overall run deadline has passed."""
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self.deadline_hit = True
        return self.deadline_hit

    def skip_url(self, url: str) -> bool:
        """Search-time skip: URL is discarded or its key is already known."""
        return not passes_discarded_urls(url, self.discarded_urls) or url_is_known(url, self.known_keys)

    def match_keywords(self, text: str, keywords: list) -> list:
        """Matched keywords in `text` under the request's match_mode."""
        return term_hits({"Job Title": text}, keywords, self.match_mode)

    def evaluate(self, row: dict, keywords: list, drops: dict, require_keyword: bool = False):
        """
        Run the post-fetch filter chain on a normalized row: location -> anti_keywords ->
        experience -> since -> work_mode -> seniority -> excluded_companies -> known
        dedupe_key. Returns the score (and records the matched keywords on the row) or
        None after counting the drop in `drops`.
        """
        checks = (
            ("location", lambda: passes_location(row, self.acceptable_locations)),
            ("anti_keywords", lambda: passes_anti_keywords(row, self.anti_keywords, self.match_mode)),
            ("experience", lambda: passes_experience(row, self.max_experience_years)),
            ("since", lambda: passes_since(row, self.since)),
            ("work_mode", lambda: passes_work_mode(row, self.work_modes)),
            ("seniority", lambda: passes_seniority(row, self.seniority_exclude)),
            ("excluded_companies", lambda: passes_excluded_companies(row, self.excluded_companies)),
            ("known", lambda: not row_is_known(row, self.known_keys)),
        )
        for name, check in checks:
            if not check():
                drops[name] = drops.get(name, 0) + 1
                return None
        tags = term_hits(row, keywords, self.match_mode)
        if require_keyword and not tags:
            drops["no_keyword"] = drops.get("no_keyword", 0) + 1
            return None
        set_tags(row, tags)
        return len(tags)

    def select(self, candidates: list, drops: dict, total_candidates: int) -> list:
        """Rank (score desc, stable) the surviving (score, row) pairs and cut to top_n."""
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        selected = candidates[: self.top_n_results]
        log.info(
            "Filtered %d candidate(s): %d passed (dropped — %s)",
            total_candidates + drops.get("discarded_urls", 0),
            len(candidates),
            ", ".join(f"{k}: {v}" for k, v in drops.items()) or "none",
        )
        self._report("filtering", len(candidates), total_candidates)
        if selected:
            scores = [score for score, _ in selected]
            log.info(
                "Selected top %d of %d passing candidate(s) — score min: %d, max: %d",
                len(selected), len(candidates), min(scores), max(scores),
            )
        return [row for _, row in selected]
