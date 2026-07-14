"""
Per-candidate filter predicates used by scrapers during their search loop.

`passes_discarded_urls` operates on the raw candidate URL and should be checked before
any HTTP request is made to fetch job details — it's the cheapest filter to evaluate and
saves a page load for URLs already known to be unwanted. The other three operate on the
job dict once its fields have been extracted and mapped to the output schema. All four
are no-ops (always pass) when their corresponding config is empty/None.
"""

import re

_TAG_RE = re.compile(r"<[^>]+>")

# Matches "N+ years", "N-M years" (base N, not M), and plain "N years" (covers
# "at least N years" / "minimum of N years", since the number still sits right
# before "years"). Group 1 is always the minimum years required.
EXPERIENCE_PATTERN = re.compile(r"(\d+)\s*(?:\+|-\s*\d+)?\s*years?", re.IGNORECASE)


def _strip_html(html: str) -> str:
    return _TAG_RE.sub(" ", html or "")


def job_text(job: dict) -> str:
    """
    Job Title + plain-text Job Description, for keyword/regex matching only (relevance
    scoring, anti-keyword matching, experience matching) — the stored Job Description
    field itself stays raw HTML.
    """
    return f"{job.get('Job Title') or ''} {_strip_html(job.get('Job Description') or '')}"


def passes_discarded_urls(url: str, discarded_urls: set) -> bool:
    """A URL fails if it's in discarded_urls (exact match). No-op if the set is empty."""
    if not discarded_urls:
        return True
    return url not in discarded_urls


def passes_location(job: dict, acceptable_locations: list) -> bool:
    """
    A job passes if its Job Location is empty/None (unknown location isn't
    disqualifying), or if it contains (case-insensitive substring) any entry
    from acceptable_locations. No-op (always passes) if the list is empty.
    """
    if not acceptable_locations:
        return True

    location = (job.get("Job Location") or "").strip().lower()
    if not location:
        return True

    return any(loc.lower() in location for loc in acceptable_locations)


def passes_anti_keywords(job: dict, anti_keywords: list) -> bool:
    """A job fails if its title/description contains any anti-keyword. No-op if empty."""
    if not anti_keywords:
        return True

    text = job_text(job).lower()
    return not any(kw.lower() in text for kw in anti_keywords)


def passes_experience(job: dict, max_experience_years: int | None) -> bool:
    """
    A job fails if some "years of experience" mention's minimum exceeds
    max_experience_years. Jobs with no mention at all pass. No-op (always
    passes) if max_experience_years is None.
    """
    if max_experience_years is None:
        return True

    text = job_text(job)
    required_minimums = (int(m.group(1)) for m in EXPERIENCE_PATTERN.finditer(text))
    return not any(minimum > max_experience_years for minimum in required_minimums)
