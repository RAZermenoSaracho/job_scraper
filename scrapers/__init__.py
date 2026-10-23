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
from scrapers.hashtagweb3_feed import HashtagWeb3Scraper
from scrapers.web3career_api import Web3CareerApiScraper
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
    # Official API adapter; executable only when the token is configured. The legacy HTML
    # scraper stays the default for requests that omit `sources` (see adapter_for).
    "web3career": {
        "adapter": Web3CareerApiScraper, "legacy_adapter": Web3CareerScraper, "label": "web3.career",
        "category": "job_board", "availability": "available", "needs_browser": False,
        "requires_config": "token:WEB3CAREER_API_TOKEN",
        "filters": FILTERS, "hosts": ["web3.career"], "ingestion": None,
    },
    "hashtagweb3": {
        "adapter": HashtagWeb3Scraper, "label": "Hashtag Web3", "category": "job_board",
        "availability": "available", "needs_browser": False, "requires_config": None,
        "filters": FILTERS, "hosts": ["hashtagweb3.com"], "ingestion": None,
    },
    # Catalog-only: never fetched, scraped or browsed by this service (terms prohibit
    # automated access); ingestion is the user pasting a job URL into Odoo.
    "linkedin": {
        "adapter": None, "label": "LinkedIn", "category": "job_board", "availability": "manual",
        "needs_browser": False, "requires_config": None, "filters": [], "hosts": ["linkedin.com"],
        "ingestion": "Paste the job URL into Odoo (LinkedIn User Agreement section 8.2 and robots.txt prohibit automated access).",
    },
    "wellfound": {
        "adapter": None, "label": "Wellfound", "category": "job_board", "availability": "manual",
        "needs_browser": False, "requires_config": None, "filters": [], "hosts": ["wellfound.com"],
        "ingestion": "Paste the job URL into Odoo (Wellfound terms III(4),(6) prohibit scraping).",
    },
    "bitcoinerjobs": {
        "adapter": None, "label": "Bitcoiner Jobs", "category": "job_board", "availability": "manual",
        "needs_browser": False, "requires_config": None, "filters": [], "hosts": ["bitcoinerjobs.com"],
        "ingestion": "Paste the job URL into Odoo (terms ban robots/spiders; automate only with written permission or a feed).",
    },
    "laborx": {
        "adapter": None, "label": "LaborX", "category": "job_board", "availability": "manual",
        "needs_browser": False, "requires_config": None, "filters": [], "hosts": ["laborx.com"],
        "ingestion": "Paste the job URL into Odoo (terms section 4 1.1(h): any automated use is prohibited).",
    },
    "cyfrin": {
        "adapter": None, "label": "Cyfrin Jobs", "category": "job_board", "availability": "defunct",
        "needs_browser": False, "requires_config": None, "filters": [], "hosts": ["jobs.cyfrin.io"],
        "ingestion": "Closed 2025-12-09; nothing to ingest.",
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


def adapter_for(source_id: str, explicit: bool):
    """
    Adapter class for a registry id. Requests that omit `sources` (explicit=False) keep the
    legacy web3.career HTML scraper — the existing frontend's path — unless the API token
    is configured AND env WEB3CAREER_LEGACY_SCRAPER is switched off (0/false/no/off):
    that flag is the cut-over to the official API adapter.
    """
    entry = SOURCES[source_id]
    legacy = entry.get("legacy_adapter")
    if legacy and not explicit:
        flag = os.environ.get("WEB3CAREER_LEGACY_SCRAPER", "1").strip().lower()
        if flag not in ("0", "false", "no", "off") or not source_state(source_id)[0]:
            return legacy
    return entry["adapter"]


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
