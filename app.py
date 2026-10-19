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
import re
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from scrapers import DEFAULT_SOURCES, SOURCES, catalog, source_state

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
    acceptable_locations: list[str] = []
    anti_keywords: list[str] = []
    discarded_urls: list[str] = []
    max_jobs_searched_per_keyword: int
    max_jobs_saved: int
    max_experience_years: int | None = None
    sources: list[str] | None = None  # None = DEFAULT_SOURCES (findweb3 + web3career)
    known_keys: list[str] = []
    since: datetime | None = None
    work_modes: list[Literal["remote", "hybrid", "onsite"]] = []
    seniority_exclude: list[str] = []
    excluded_companies: list[str] = []
    deadline_seconds: float | None = Field(default=None, gt=0)
    match_mode: Literal["substring", "word"] = "substring"
    boards: list[str] = []  # ATS boards as "<greenhouse|lever|ashby>:<token>"

    @field_validator("boards")
    @classmethod
    def _check_boards(cls, boards: list[str]) -> list[str]:
        seen: dict = {}
        for entry in boards:
            m = re.fullmatch(r"(greenhouse|lever|ashby):([A-Za-z0-9._-]+)", entry.strip(), re.IGNORECASE)
            if not m:
                raise ValueError(f"invalid board '{entry}': expected <greenhouse|lever|ashby>:<token>")
            seen.setdefault(f"{m[1]}:{m[2]}".lower(), f"{m[1].lower()}:{m[2]}")
        return list(seen.values())


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
    Return [(source_id, scraper_instance), ...] for this request, iterating the registry
    in scrapers/__init__.py. Built entirely from the request body — there is no config
    file or env var fallback for scraping parameters.
    """
    source_ids = DEFAULT_SOURCES if request.sources is None else list(dict.fromkeys(request.sources))
    kwargs = dict(
        max_jobs_per_keyword=request.max_jobs_searched_per_keyword,
        top_n_results=request.max_jobs_saved,
        acceptable_locations=request.acceptable_locations,
        anti_keywords=request.anti_keywords,
        discarded_urls=request.discarded_urls,
        max_experience_years=request.max_experience_years,
        on_progress=on_progress,
        known_keys=request.known_keys,
        since=request.since,
        work_modes=request.work_modes,
        seniority_exclude=request.seniority_exclude,
        excluded_companies=request.excluded_companies,
        match_mode=request.match_mode,
    )
    scrapers = [
        (sid, SOURCES[sid]["adapter"](**kwargs))
        for sid in source_ids if SOURCES[sid]["category"] != "ats"
    ]
    # ATS adapters run only for the vendors of the requested boards (and, if `sources` is
    # given, only when the vendor id is listed there).
    for ats in ("greenhouse", "lever", "ashby"):
        tokens = [b.split(":", 1)[1] for b in request.boards if b.startswith(f"{ats}:")]
        if tokens and (request.sources is None or ats in request.sources):
            scrapers.append((ats, SOURCES[ats]["adapter"](boards=tokens, **kwargs)))
    return scrapers


def _score(job: dict) -> int:
    """Relevance score = unique keywords matched (Tags)."""
    tags = job.get("tags")
    if tags is None:
        tags = [t for t in (job.get("Tags") or "").split(",") if t.strip()]
    return len(tags)


def _merge_and_cut(jobs: list, cap: int) -> list:
    """
    Merge rows from all sources: drop duplicates by canonical_url / dedupe_key (higher
    score wins, keeping first position), rank by score desc (stable on source order)
    and cut to `cap`.
    """
    kept: list = []
    index: dict = {}
    for job in jobs:
        dedupe = job.get("dedupe_key") or ""
        keys = [k for k in (job.get("canonical_url"), dedupe if dedupe.strip("|") else None) if k]
        pos = next((index[k] for k in keys if k in index), None)
        if pos is None:
            pos = len(kept)
            kept.append(job)
        elif _score(job) > _score(kept[pos]):
            kept[pos] = job
        for k in keys:
            index[k] = pos
    return sorted(kept, key=_score, reverse=True)[:cap]


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

    deadline = time.monotonic() + request.deadline_seconds if request.deadline_seconds else None
    all_jobs: list = []
    errors: list = []
    failed = 0
    for site_name, scraper in scrapers:
        if deadline is not None:
            if time.monotonic() >= deadline:
                errors.append({"source": site_name, "error": "deadline exceeded"})
                continue
            scraper.deadline = deadline
        try:
            all_jobs.extend(scraper.run(request.keywords))
        except Exception as exc:
            log.exception("Scraper '%s' failed for job %s", site_name, job_id)
            failed += 1
            # A scraper that tracks its own per-board failures reports those instead.
            errors.extend(getattr(scraper, "errors", None) or
                          [{"source": site_name, "error": _short_error(exc)}])
            continue
        errors.extend(getattr(scraper, "errors", None) or [])
        if getattr(scraper, "deadline_hit", False):
            errors.append({"source": site_name, "error": "deadline exceeded"})

    # Error only if every source failed (then the old frontend behaviour: show the error).
    if scrapers and failed == len(scrapers):
        message = "; ".join(f"Scraper '{e['source']}' failed: {e['error']}" for e in errors)
        jobs_status[job_id].update(status="error", error=message)
        return

    jobs_status[job_id].update(
        status="done",
        stage="done",
        result={
            "schema_version": SCHEMA_VERSION,
            "keywords": request.keywords,
            "jobs": _merge_and_cut(all_jobs, request.max_jobs_saved),
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


def _validate_sources(sources: list | None) -> None:
    """422 naming every unknown or non-executable source id; never silently ignored."""
    problems = []
    for sid in dict.fromkeys(sources or []):
        if sid not in SOURCES:
            problems.append(f"unknown source '{sid}'")
        else:
            executable, reason, _ = source_state(sid)
            if not executable:
                problems.append(f"source '{sid}' is not executable ({reason})")
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))


@app.get("/sources")
def get_sources() -> dict:
    """Static source catalog (no network I/O, no adapter instantiation)."""
    return {"schema_version": SCHEMA_VERSION, "sources": catalog()}


@app.post("/jobs", status_code=202)
def create_job(request: JobsRequest, background_tasks: BackgroundTasks) -> dict:
    """
    Validate the request, register a new job, and schedule its scraping as a background
    task. Returns {"job_id": ...} immediately — the caller polls
    GET /jobs/{job_id}/status and GET /jobs/{job_id}/result for progress/output.
    """
    _validate_sources(request.sources)
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
