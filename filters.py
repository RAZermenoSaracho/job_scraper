"""
Post-scrape filtering: drops jobs by location, anti-keywords, and required
experience, so main.py never writes discarded jobs to the output files.
"""

import re

_TAG_RE = re.compile(r"<[^>]+>")

# Matches "N+ years", "N-M years" (base N, not M), and plain "N years" (covers
# "at least N years" / "minimum of N years", since the number still sits right
# before "years"). Group 1 is always the minimum years required.
EXPERIENCE_PATTERN = re.compile(r"(\d+)\s*(?:\+|-\s*\d+)?\s*years?", re.IGNORECASE)


def _strip_html(html: str) -> str:
    return _TAG_RE.sub(" ", html or "")


def _job_text(job: dict) -> str:
    """Job Title + plain-text Job Description, for keyword/regex matching only."""
    return f"{job.get('Job Title') or ''} {_strip_html(job.get('Job Description') or '')}"


def filter_by_location(jobs: list, acceptable_locations: list) -> tuple[list, int]:
    """Keep jobs whose Job Location matches an acceptable location, or has none set."""
    if not acceptable_locations:
        return jobs, 0

    lowered_locations = [loc.lower() for loc in acceptable_locations]
    kept, dropped = [], 0

    for job in jobs:
        location = (job.get("Job Location") or "").strip().lower()
        if not location or any(loc in location for loc in lowered_locations):
            kept.append(job)
        else:
            dropped += 1

    return kept, dropped


def filter_by_anti_keywords(jobs: list, anti_keywords: list) -> tuple[list, int]:
    """Drop jobs whose title or description contains any anti-keyword."""
    if not anti_keywords:
        return jobs, 0

    lowered_anti = [kw.lower() for kw in anti_keywords]
    kept, dropped = [], 0

    for job in jobs:
        text = _job_text(job).lower()
        if any(kw in text for kw in lowered_anti):
            dropped += 1
        else:
            kept.append(job)

    return kept, dropped


def filter_by_experience(jobs: list, max_experience_years: int) -> tuple[list, int]:
    """Drop jobs where every mention of required years of experience exceeds the max."""
    kept, dropped = [], 0

    for job in jobs:
        text = _job_text(job)
        required_minimums = (int(m.group(1)) for m in EXPERIENCE_PATTERN.finditer(text))
        if any(minimum > max_experience_years for minimum in required_minimums):
            dropped += 1
        else:
            kept.append(job)

    return kept, dropped


def filter_jobs(jobs: list, config: dict, max_experience_years: int) -> tuple[list, dict]:
    """
    Apply location, anti-keyword, and experience filters in sequence.
    Returns (surviving_jobs, drop_counts) where drop_counts has keys
    "location", "anti_keywords", "experience".
    """
    jobs, dropped_location = filter_by_location(jobs, config.get("acceptable_locations", []))
    jobs, dropped_anti_keywords = filter_by_anti_keywords(jobs, config.get("anti_keywords", []))
    jobs, dropped_experience = filter_by_experience(jobs, max_experience_years)

    return jobs, {
        "location": dropped_location,
        "anti_keywords": dropped_anti_keywords,
        "experience": dropped_experience,
    }
