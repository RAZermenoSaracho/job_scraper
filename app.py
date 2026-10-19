"""
FastAPI wrapper around the job scraper. POST /jobs kicks off scraping in the
background and returns a job_id immediately (202) — scraping can take 2-4 minutes,
long enough to exceed edge/tunnel proxy timeouts if done synchronously in the
request. Poll GET /jobs/{job_id}/status for progress and GET /jobs/{job_id}/result
once done. No files are written to disk, and no server-side config file or env var
is read for scraping parameters — every run is fully described by the POST body.
Filtering (discarded URLs, location, anti-keywords, required experience) and
relevance ranking happen inside each scraper; this module only handles request
validation, scraper wiring, background job tracking, and the HTTP response/error
shape.
"""

import hmac
import logging
import os
import time
import uuid
from collections.abc import Callable

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from scrapers import Fw3Scraper, Web3CareerScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SCHEMA_VERSION = "1"


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Enforce X-API-Key only when env JOB_SCRAPER_API_KEY is set (off by default)."""
    expected = os.environ.get("JOB_SCRAPER_API_KEY")
    if expected and not hmac.compare_digest(x_api_key or "", expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


app = FastAPI(title="job_scraper", dependencies=[Depends(require_api_key)])

# Dev-only: allows any origin so the frontend can call this API from anywhere while
# it's being built. Restrict this to the real frontend's production domain(s) before
# deploying either service. Applies to every route below (CORSMiddleware wraps the
# whole app), so /jobs, /jobs/{job_id}/status, and /jobs/{job_id}/result are all covered.
ALLOWED_ORIGINS = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job tracking — deliberately not persisted anywhere; this process is the
# only thing that knows about a job_id, and it forgets after JOB_STATUS_TTL_SECONDS.
jobs_status: dict[str, dict] = {}
JOB_STATUS_TTL_SECONDS = 60 * 60


class JobsRequest(BaseModel):
    """POST /jobs body. Fully describes a scraping run — nothing is read from disk or env."""

    keywords: list[str]
    acceptable_locations: list[str]
    anti_keywords: list[str] = []
    discarded_urls: list[str] = []
    max_jobs_searched_per_keyword: int
    max_jobs_saved: int
    max_experience_years: int


def _purge_old_jobs() -> None:
    """Drop jobs_status entries older than JOB_STATUS_TTL_SECONDS. Called on each new job."""
    cutoff = time.time() - JOB_STATUS_TTL_SECONDS
    stale_ids = [job_id for job_id, state in jobs_status.items() if state["created_at"] < cutoff]
    for job_id in stale_ids:
        del jobs_status[job_id]


def _build_scrapers(
    request: JobsRequest,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> list:
    """
    Return [(display_name, scraper_instance), ...] for this request. Built entirely
    from the request body — there is no config file or env var fallback for scraping
    parameters. on_progress, if given, is wired into every scraper that supports it.
    """
    return [
        ("findweb3.com", Fw3Scraper(
            max_jobs_per_keyword=request.max_jobs_searched_per_keyword,
            top_n_results=request.max_jobs_saved,
            acceptable_locations=request.acceptable_locations,
            anti_keywords=request.anti_keywords,
            discarded_urls=request.discarded_urls,
            max_experience_years=request.max_experience_years,
            on_progress=on_progress,
        )),
        ("web3.career", Web3CareerScraper(
            max_jobs_per_keyword=request.max_jobs_searched_per_keyword,
            top_n_results=request.max_jobs_saved,
            acceptable_locations=request.acceptable_locations,
            anti_keywords=request.anti_keywords,
            discarded_urls=request.discarded_urls,
            max_experience_years=request.max_experience_years,
            on_progress=on_progress,
        )),
        # ("otta.com", OttaScraper(...)),
    ]


def _run_scrape_job(job_id: str, request: JobsRequest) -> None:
    """
    Run every configured scraper for this job, updating jobs_status[job_id] as it goes.
    Runs as a FastAPI BackgroundTask — Starlette executes sync background callables via
    its threadpool, so this (and the sync Selenium calls inside it) doesn't block the
    event loop or other requests.
    """
    def on_progress(stage: str, current: int, total: int) -> None:
        jobs_status[job_id].update(stage=stage, current=current, total=total)

    try:
        scrapers = _build_scrapers(request, on_progress)
    except Exception as exc:
        log.exception("Failed to configure scrapers for job %s", job_id)
        jobs_status[job_id].update(status="error", error=f"Failed to configure scrapers: {exc}")
        return

    all_jobs: list = []
    errors: list = []
    for site_name, scraper in scrapers:
        try:
            all_jobs.extend(scraper.run(request.keywords))
        except Exception as exc:
            log.exception("Scraper '%s' failed for job %s", site_name, job_id)
            errors.append({"source": site_name, "error": _short_error(exc)})

    # Error only if every source failed (then the old frontend behaviour: show the error).
    if scrapers and len(errors) == len(scrapers):
        message = "; ".join(f"Scraper '{e['source']}' failed: {e['error']}" for e in errors)
        jobs_status[job_id].update(status="error", error=message)
        return

    jobs_status[job_id].update(
        status="done",
        stage="done",
        result={
            "schema_version": SCHEMA_VERSION,
            "keywords": request.keywords,
            "jobs": all_jobs,
            "errors": errors,
        },
    )


def _short_error(exc: Exception) -> str:
    """One-line error text for errors[]: no stack trace, truncated."""
    return (str(exc) or type(exc).__name__).replace("\n", " ")[:200]


def _get_job_or_404(job_id: str) -> dict:
    state = jobs_status.get(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Unknown job_id: {job_id}")
    return state


@app.post("/jobs", status_code=202)
def create_job(request: JobsRequest, background_tasks: BackgroundTasks) -> dict:
    """
    Validate the request, register a new job, and schedule its scraping as a background
    task. Returns {"job_id": ...} immediately — the caller polls
    GET /jobs/{job_id}/status and GET /jobs/{job_id}/result for progress/output.
    """
    _purge_old_jobs()

    job_id = str(uuid.uuid4())
    jobs_status[job_id] = {
        "status": "running",
        "stage": "starting",
        "current": 0,
        "total": 0,
        "result": None,
        "error": None,
        "created_at": time.time(),
    }

    background_tasks.add_task(_run_scrape_job, job_id, request)

    return {"job_id": job_id}


@app.get("/jobs/{job_id}/status")
def get_job_status(job_id: str) -> dict:
    """Return the current status/stage/progress for job_id (404 if unknown)."""
    state = _get_job_or_404(job_id)

    response = {
        "status": state["status"],
        "stage": state["stage"],
        "current": state["current"],
        "total": state["total"],
    }
    if state["status"] == "error":
        response["error"] = state["error"]
    return response


@app.get("/jobs/{job_id}/result")
def get_job_result(job_id: str) -> dict:
    """
    Return the final {"keywords": [...], "jobs": [...]} for job_id once scraping has
    finished. 409 if it's still running, or the scraper's error (500) if it failed.
    """
    state = _get_job_or_404(job_id)

    if state["status"] == "error":
        raise HTTPException(status_code=500, detail=state["error"])
    if state["status"] != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Job '{job_id}' is still in progress (status={state['status']})",
        )

    return state["result"]
