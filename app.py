"""
FastAPI wrapper around the job scraper. POST /jobs runs the configured scrapers on
demand and returns results directly in the HTTP response — no files are written to
disk, and no server-side config file or env var is read for scraping parameters.
Every run is fully described by the POST body. Filtering (discarded URLs, location,
anti-keywords, required experience) and relevance ranking happen inside each scraper;
this module only handles request validation, scraper wiring, and the HTTP
response/error shape.
"""

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from scrapers import Fw3Scraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

app = FastAPI(title="job_scraper")

# Dev-only: allows any origin so the frontend can call this API from anywhere while
# it's being built. Restrict this to the real frontend's production domain(s) before
# deploying either service.
ALLOWED_ORIGINS = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class JobsRequest(BaseModel):
    """POST /jobs body. Fully describes a scraping run — nothing is read from disk or env."""

    keywords: list[str]
    acceptable_locations: list[str]
    anti_keywords: list[str] = []
    discarded_urls: list[str] = []
    max_jobs_searched_per_keyword: int
    max_jobs_saved: int
    max_experience_years: int


def _build_scrapers(request: JobsRequest) -> list:
    """
    Return [(display_name, scraper_instance), ...] for this request. Built entirely
    from the request body — there is no config file or env var fallback for scraping
    parameters.
    """
    return [
        ("findweb3.com", Fw3Scraper(
            max_jobs_per_keyword=request.max_jobs_searched_per_keyword,
            top_n_results=request.max_jobs_saved,
            acceptable_locations=request.acceptable_locations,
            anti_keywords=request.anti_keywords,
            discarded_urls=request.discarded_urls,
            max_experience_years=request.max_experience_years,
        )),
        # ("otta.com", OttaScraper(...)),
    ]


@app.post("/jobs")
def get_jobs(request: JobsRequest) -> dict:
    """
    Run the configured scrapers with this request's config and return
    {"keywords": [...], "jobs": [...]}, where each job matches the standard schema
    (Job Title, Company Name, Job Description, Job Location, Compensation, Job Url,
    Tags, Notes).
    """
    try:
        scrapers = _build_scrapers(request)
    except Exception as exc:
        log.exception("Failed to configure scrapers")
        raise HTTPException(status_code=500, detail=f"Failed to configure scrapers: {exc}")

    all_jobs: list = []
    for site_name, scraper in scrapers:
        try:
            all_jobs.extend(scraper.run(request.keywords))
        except Exception as exc:
            log.exception("Scraper '%s' failed", site_name)
            raise HTTPException(status_code=502, detail=f"Scraper '{site_name}' failed: {exc}")

    return {"keywords": request.keywords, "jobs": all_jobs}
