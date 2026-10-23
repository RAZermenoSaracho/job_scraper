"""
Per-candidate filter predicates used by scrapers during their search loop.

`passes_discarded_urls` operates on the raw candidate URL and should be checked before
any HTTP request is made to fetch job details — it's the cheapest filter to evaluate and
saves a page load for URLs already known to be unwanted. The other three operate on the
job dict once its fields have been extracted and mapped to the output schema. All four
are no-ops (always pass) when their corresponding config is empty/None.
"""

import re
from datetime import datetime, timezone

from utils.normalize import identify_url, norm_text, to_iso_utc

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


def term_hits(job: dict, terms: list, match_mode: str = "substring") -> list:
    """
    The one keyword/anti-keyword matching rule for every source. Returns the unique
    `terms` (original casing, input order) found in job_text(job); empty terms are ignored.
    - "substring" (default): case-insensitive substring, exactly the pre-existing behaviour.
    - "word": text and terms go through norm_text (accents, case, punctuation, whitespace
      folded) and a term must match as a whole word/phrase.
    """
    terms = [t for t in dict.fromkeys(terms or []) if t and t.strip()]
    if not terms:
        return []
    text = job_text(job)
    if match_mode == "word":
        padded = f" {norm_text(text)} "
        return [t for t in terms if norm_text(t) and f" {norm_text(t)} " in padded]
    lowered = text.lower()
    return [t for t in terms if t.lower() in lowered]


def passes_anti_keywords(job: dict, anti_keywords: list, match_mode: str = "substring") -> bool:
    """A job fails if its title/description contains any anti-keyword. No-op if empty."""
    return not term_hits(job, anti_keywords, match_mode)


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


# ── Filters added by improve-discovery-filters (operate on normalized rows) ──


def _parse_dt(value) -> datetime | None:
    iso = to_iso_utc(value)
    return datetime.fromisoformat(iso.replace("Z", "+00:00")) if iso else None


def passes_since(job: dict, since) -> bool:
    """Fails only if `posted_at` is known and earlier than `since`. No-op if since is None."""
    if since is None:
        return True
    posted, limit = _parse_dt(job.get("posted_at")), _parse_dt(since)
    return posted is None or limit is None or posted >= limit


def passes_work_mode(job: dict, work_modes: list) -> bool:
    """Keeps jobs whose work_mode is listed or unknown. No-op if the list is empty."""
    if not work_modes:
        return True
    return (job.get("work_mode") or "unknown") in set(work_modes) | {"unknown"}


def passes_seniority(job: dict, seniority_exclude: list) -> bool:
    """Fails if the *title* contains an excluded term as a whole word (any match_mode)."""
    title = f" {norm_text(job.get('Job Title'))} "
    return not any(norm_text(t) and f" {norm_text(t)} " in title for t in seniority_exclude or [])


def passes_excluded_companies(job: dict, excluded_companies: list) -> bool:
    """Fails if norm_text(company) equals a listed norm_text name (equality, not substring)."""
    company = norm_text(job.get("Company Name"))
    return not company or company not in {norm_text(c) for c in excluded_companies or []}


def expand_known_keys(known_keys) -> set:
    """known_keys as given plus the canonical form of every URL entry."""
    known = {k for k in known_keys or [] if k}
    known |= {identify_url(k)["canonical_url"] for k in list(known) if k.startswith("http")}
    known.discard(None)
    return known


def url_is_known(url: str, known_keys: set) -> bool:
    """Search-time check from a listing URL: its canonical URL or `<source>:<id>` is known."""
    if not known_keys:
        return False
    ident = identify_url(url)
    return bool(
        url in known_keys
        or ident["canonical_url"] in known_keys
        or (ident["source"] and ident["source_job_id"]
            and f"{ident['source']}:{ident['source_job_id']}" in known_keys)
    )


def row_is_known(job: dict, known_keys: set) -> bool:
    """Post-fetch check: canonical_url, `<source>:<id>` or a non-empty dedupe_key is known."""
    if not known_keys:
        return False
    dedupe = job.get("dedupe_key") or ""
    return bool(
        job.get("canonical_url") in known_keys
        or (job.get("source") and job.get("source_job_id")
            and f"{job['source']}:{job['source_job_id']}" in known_keys)
        or (dedupe.strip("|") and dedupe in known_keys)
    )
