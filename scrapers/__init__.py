"""
Static source registry (plain data, no plugin system). It is the single source of truth
for GET /sources, `sources` validation and `_build_scrapers` in app.py.

Entry keys: adapter (class or None for catalog-only sources), label, category
(job_board|ats|feed), availability (available|planned|manual|defunct), needs_browser,
requires_config (None, "boards", or "token:ENV_NAME"), filters, hosts, ingestion
(manual-paste description or None).

`executable` is never stored: it is computed by `source_state()` — an adapter is
registered AND its requires_config is satisfied.
"""

import os

from scrapers.ats_scraper import AshbyScraper, GreenhouseScraper, LeverScraper
from scrapers.fw3_jobscraper import Fw3Scraper
from scrapers.web3career_jobscraper import Web3CareerScraper

FILTERS = ["location", "anti_keywords", "experience", "since", "work_mode", "seniority",
           "excluded_companies", "known_keys", "discarded_urls", "match_mode"]

# Sources run when a request omits `sources` (what the existing frontend relies on).
DEFAULT_SOURCES = ["findweb3", "web3career"]

SOURCES = {
    "findweb3": {
        "adapter": Fw3Scraper, "label": "findweb3.com", "category": "job_board",
        "availability": "available", "needs_browser": True, "requires_config": None,
        "filters": FILTERS, "hosts": ["findweb3.com"], "ingestion": None,
    },
    "web3career": {
        "adapter": Web3CareerScraper, "label": "web3.career", "category": "job_board",
        "availability": "available", "needs_browser": False, "requires_config": None,
        "filters": FILTERS, "hosts": ["web3.career"], "ingestion": None,
    },
    # ATS boards are selected via the request's `boards` list ("<ats>:<token>").
    "greenhouse": {
        "adapter": GreenhouseScraper, "label": "Greenhouse", "category": "ats",
        "availability": "available", "needs_browser": False, "requires_config": "boards",
        "filters": FILTERS, "hosts": ["boards.greenhouse.io", "job-boards.greenhouse.io"], "ingestion": None,
    },
    "lever": {
        "adapter": LeverScraper, "label": "Lever", "category": "ats",
        "availability": "available", "needs_browser": False, "requires_config": "boards",
        "filters": FILTERS, "hosts": ["jobs.lever.co"], "ingestion": None,
    },
    "ashby": {
        "adapter": AshbyScraper, "label": "Ashby", "category": "ats",
        "availability": "available", "needs_browser": False, "requires_config": "boards",
        "filters": FILTERS, "hosts": ["jobs.ashbyhq.com"], "ingestion": None,
    },
}


def source_state(source_id: str) -> tuple[bool, str | None, str]:
    """(executable, reason when not executable, effective availability) for a registry id."""
    entry = SOURCES[source_id]
    availability = entry["availability"]
    if entry["adapter"] is None:
        return False, f"catalog only ({availability}); no adapter", availability
    config = entry["requires_config"] or ""
    if config.startswith("token:") and not os.environ.get(config[6:]):
        return False, f"{config[6:]} not set", "planned"
    return True, None, availability


def catalog() -> list[dict]:
    """The GET /sources entries. No network I/O, no adapter instantiation."""
    out = []
    for source_id, entry in SOURCES.items():
        executable, reason, availability = source_state(source_id)
        out.append({
            "id": source_id, "name": entry["label"], "category": entry["category"],
            "availability": availability, "executable": executable,
            "requires_config": entry["requires_config"], "filters": entry["filters"],
            "hosts": entry["hosts"], "ingestion": entry["ingestion"], "reason": reason,
        })
    return out
