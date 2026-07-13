"""
FastAPI wrapper around the job scraper. GET /jobs runs the configured scrapers on
demand and returns results directly in the HTTP response — no JSON/XLSX files are
written to disk. Filtering (location, anti-keywords, required experience) and
relevance ranking happen inside each scraper; this module only handles request
parameters, config loading, and the HTTP response/error shape.
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query

from scrapers import Fw3Scraper

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent
KEYWORDS_FILE = ROOT_DIR / "keywords.json"

DEFAULT_MAX_JOBS_SEARCHED_PER_KEYWORD = int(os.environ.get("MAX_JOBS_SEARCHED_PER_KEYWORD", 20))
DEFAULT_MAX_JOBS_SAVED = int(os.environ.get("MAX_JOBS_SAVED", 10))
DEFAULT_MAX_EXPERIENCE_YEARS = int(os.environ.get("MAX_EXPERIENCE_YEARS", 4))

app = FastAPI(title="job_scraper")


def _build_scrapers(
    config: dict,
    max_jobs_searched_per_keyword: int,
    max_jobs_saved: int,
    max_experience_years: int,
) -> list:
    """
    Return [(display_name, scraper_instance), ...] for this request. Built per-request
    (not at import time) since the request's query params feed straight into the
    scraper constructors.
    """
    return [
        ("findweb3.com", Fw3Scraper(
            max_jobs_per_keyword=max_jobs_searched_per_keyword,
            top_n_results=max_jobs_saved,
            acceptable_locations=config.get("acceptable_locations", []),
            anti_keywords=config.get("anti_keywords", []),
            max_experience_years=max_experience_years,
        )),
        # ("otta.com", OttaScraper(...)),
    ]


@app.get("/jobs")
def get_jobs(
    max_jobs_searched_per_keyword: int = Query(DEFAULT_MAX_JOBS_SEARCHED_PER_KEYWORD, ge=1),
    max_jobs_saved: int = Query(DEFAULT_MAX_JOBS_SAVED, ge=1),
    max_experience_years: int = Query(DEFAULT_MAX_EXPERIENCE_YEARS, ge=0),
) -> dict:
    """
    Run the configured scrapers with keywords.json + these overrides and return
    {"keywords": [...], "jobs": [...]}, where each job matches the standard schema
    (Job Title, Company Name, Job Description, Job Location, Compensation, Job Url,
    Tags, Notes).
    """
    try:
        config = json.loads(KEYWORDS_FILE.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read keywords.json: {exc}")

    keywords = config["keywords"]
    scrapers = _build_scrapers(
        config, max_jobs_searched_per_keyword, max_jobs_saved, max_experience_years
    )

    all_jobs: list = []
    for site_name, scraper in scrapers:
        try:
            all_jobs.extend(scraper.run(keywords))
        except Exception as exc:
            log.exception("Scraper '%s' failed", site_name)
            raise HTTPException(status_code=502, detail=f"Scraper '{site_name}' failed: {exc}")

    return {"keywords": keywords, "jobs": all_jobs}
