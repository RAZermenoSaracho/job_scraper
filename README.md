# job_scraper

A FastAPI service that scrapes job boards for postings matching a set of keywords, ranks each
posting by how many *unique* keywords it mentions, and returns the top-N results as JSON.
Currently supports [findweb3.com](https://findweb3.com), built so additional sources can be added
as independent scraper modules without touching the request-handling logic. There's no config file
and no scraping parameters in env vars — every job is fully described by its `POST /jobs` request
body. Scraping runs as a background task and is tracked via an in-memory `job_id`: nothing is
written to disk, and the job-status tracking itself is ephemeral (in-process only, purged after an
hour, lost on restart) — it exists purely so a slow scrape doesn't have to hold an HTTP connection
open the whole time.

## Project structure

```
.
├── app.py                     # FastAPI app: POST /jobs schedules a background scrape; GET .../status and .../result poll it
├── requirements.txt           # Pinned dependencies for the `job_scraper` conda env
├── utils/
│   ├── __init__.py            # Exposes the filter predicates for import as `from utils import ...`
│   └── filters.py             # Per-candidate filter predicates: discarded URLs, location, anti-keywords, required experience
└── scrapers/
    ├── __init__.py            # Exposes Fw3Scraper for import as `from scrapers import Fw3Scraper`
    └── fw3_jobscraper.py       # Fw3Scraper: headless-Selenium scraper for findweb3.com; filters + ranks candidates internally, reports progress
```

## Prerequisites

- Python 3.11
- Conda
- Google Chrome (required by Selenium/ChromeDriver)

## Installation

```bash
conda create -n job_scraper python=3.11 -y
conda activate job_scraper
pip install -r requirements.txt
```

## Async job lifecycle

A full scrape can take 2-4 minutes (one page load per keyword, plus one per candidate job detail
page), which is long enough to exceed some edge/tunnel proxy timeouts (e.g. Cloudflare Tunnel) if
done synchronously inside a single request. So `POST /jobs` only *schedules* the scrape and returns
right away; the caller polls for progress and fetches the result once it's done.

1. **`POST /jobs`** — validates the body (see below), generates a `job_id` (`uuid4`), stores its
   initial state, schedules the actual scraping as a FastAPI `BackgroundTask`, and returns
   `202 {"job_id": "..."}` immediately.
2. **`GET /jobs/{job_id}/status`** — returns the job's current progress:
   ```json
   {"status": "running", "stage": "scraping", "current": 7, "total": 20}
   ```
   - `status` is one of `running`, `done`, `error`.
   - `stage` is a short label for the current step: `starting` → `searching_keywords` →
     `scraping` → `filtering` → `done` (or `error`).
   - `current`/`total` are stage-relative counters (e.g. during `scraping`, "candidate 7 of 20").
   - If `status == "error"`, the body also includes `"error": "<message>"`.
   - `404` if `job_id` is unknown (never existed, or already purged — see below).
3. **`GET /jobs/{job_id}/result`** — returns `{"keywords": [...], "jobs": [...]}` (same schema as
   before) once `status == "done"`. `409` if it's still `running`; `500` with the failure message
   if `status == "error"`; `404` if `job_id` is unknown.

Job state lives in an in-memory dict (`jobs_status` in `app.py`) — there's no database or file
behind it. Entries older than an hour are purged the next time a new job is created (no scheduler,
just a check on each `POST /jobs`). This means job state doesn't survive a server restart, and only
this process knows about a given `job_id`.

### Request body (`POST /jobs`)

Validated by the `JobsRequest` Pydantic model in `app.py`. There's no `keywords.json` and no
scraping-related `.env` values — every scraping parameter is sent in the request body:

```json
{
  "keywords": ["python", "solidity", "foundry", "blockchain", "smart contracts", "defi", "evm", "quantitative", "linux", "git"],
  "acceptable_locations": ["remote", "mexico", "usa"],
  "anti_keywords": ["clearance required"],
  "discarded_urls": ["https://findweb3.com/job/recAbc123"],
  "max_jobs_searched_per_keyword": 20,
  "max_jobs_saved": 10,
  "max_experience_years": 4
}
```

| Field | Required | Default | Meaning |
|---|---|---|---|
| `keywords` | yes | — | Drives search queries and relevance scoring/tagging (see [Ranking](#ranking-evaluate-all-then-pick-the-best-n)) |
| `acceptable_locations` | yes | — | Feeds the location filter — see [Filtering](#filtering) |
| `anti_keywords` | no | `[]` | Feeds the anti-keyword filter |
| `discarded_urls` | no | `[]` | Job URLs to skip entirely (e.g. ones the client already showed the user) — see [Filtering](#filtering) |
| `max_jobs_searched_per_keyword` | yes | — | Candidate postings explored per keyword search |
| `max_jobs_saved` | yes | — | How many top-ranked postings to return |
| `max_experience_years` | yes | — | Feeds the experience filter |

A request missing a required field, or with the wrong type, gets rejected by FastAPI/Pydantic with
`422` before the handler ever runs — no manual validation needed for that case.

## Filtering

Filtering happens **inside each scraper**, inline while it visits candidates — not in `app.py`
after the fact. `Fw3Scraper` runs each candidate through four filter predicates from
[utils/filters.py](utils/filters.py), in this order:

1. **`passes_discarded_urls`** — checked first, on the raw candidate URL, **before** any page load
   for that candidate. Fails if the URL is in `discarded_urls`. This is the cheapest filter and
   saves an HTTP request to the target site for URLs the caller already doesn't want (e.g. a
   frontend re-requesting after the user dismissed some results). No-op if `discarded_urls` is `[]`.
2. **`passes_location`** — passes if `Job Location` is empty/unknown, or contains (case-insensitive
   substring) any entry from `acceptable_locations`; fails otherwise. No-op if `acceptable_locations`
   is `[]`.
3. **`passes_anti_keywords`** — fails if `Job Title` + plain-text `Job Description` (HTML stripped,
   for matching only — the stored field stays raw HTML) contains any `anti_keywords` entry. No-op
   if the list is `[]`.
4. **`passes_experience`** — scans the same title + description text for "years of experience"
   mentions (`"N+ years"`, `"N-M years"`, plain `"N years"` — which also covers `"at least N years"`
   / `"minimum of N years"`). For a range, only the **minimum** is compared: a job asking for
   `"3-7 years"` passes if `max_experience_years` is `4`, since you clear the 3-year floor. Fails
   only if some mention's minimum exceeds `max_experience_years`; jobs with no experience mention
   pass.

A candidate that fails any filter is dropped immediately and the scraper moves on to the next
candidate URL. Filters 2–4 only run once a candidate has already passed the URL check and had its
fields extracted.

### Ranking (evaluate all, then pick the best N)

The scraper does **not** stop as soon as it has `max_jobs_saved` passing candidates. It visits
*every* candidate found during search (up to `max_jobs_searched_per_keyword` per keyword, minus
any skipped via `discarded_urls`), and each one that survives the other three filters gets a
relevance score — the count of unique `keywords` matched in its title + description. Only after all
candidates have been visited does it sort the surviving pool by score (descending) and return the
best `max_jobs_saved`. This matters because stopping early at the first N passing candidates would
return "first found," not "most relevant" — a higher-scoring candidate found later wouldn't get a
chance to bump out a weaker, earlier one.

Each scraper logs drop counts per filter, plus the score range of its final selection, e.g.:

```
13:32:50 [INFO] Filtered 40 candidate(s): 12 passed (dropped — discarded_urls: 6, location: 15, anti_keywords: 2, experience: 5)
13:32:50 [INFO] Selected top 5 of 12 passing candidate(s) — score min: 3, max: 8
```

### Progress reporting

`Fw3Scraper` accepts an optional `on_progress(stage: str, current: int, total: int)` callback
(constructor kwarg). It's invoked at the same points the scraper already logs progress via
`logger.info` — searching each keyword, visiting each candidate, and the final filter summary — so
`app.py` can mirror that progress into `jobs_status[job_id]` without the scraper knowing anything
about FastAPI, background tasks, or how progress gets surfaced to a caller.

## Usage

Run the API locally:

```bash
conda activate job_scraper
uvicorn app:app --reload
```

Kick off a job:

```bash
curl -X POST "http://127.0.0.1:8000/jobs" \
  -H "Content-Type: application/json" \
  -d '{
        "keywords": ["python", "solidity", "defi"],
        "acceptable_locations": ["remote", "mexico"],
        "anti_keywords": [],
        "discarded_urls": [],
        "max_jobs_searched_per_keyword": 10,
        "max_jobs_saved": 5,
        "max_experience_years": 4
      }'
# -> 202 {"job_id": "b43473f6-c35d-4639-92b4-a9f3bd4a41d8"}
```

Poll for progress, then fetch the result:

```bash
curl "http://127.0.0.1:8000/jobs/b43473f6-c35d-4639-92b4-a9f3bd4a41d8/status"
# -> {"status": "running", "stage": "scraping", "current": 7, "total": 20}

curl "http://127.0.0.1:8000/jobs/b43473f6-c35d-4639-92b4-a9f3bd4a41d8/result"
# -> 200 {"keywords": [...], "jobs": [...]}   (or 409 while still running)
```

Interactive API docs (Swagger UI) are available at `http://127.0.0.1:8000/docs` once the server is
running.

CORS is wide open (`ALLOWED_ORIGINS = ["*"]` in `app.py`, applied once via `CORSMiddleware` to the
whole app — it covers all three routes above). **Restrict this to the real frontend's production
domain(s) before deploying either service.**

## Response format (`GET /jobs/{job_id}/result`)

```json
{
  "keywords": ["python", "solidity", "foundry", "blockchain", "smart contracts", "defi", "evm", "quantitative", "linux", "git"],
  "jobs": [
    {
      "Job Title": "Senior Solidity Engineer",
      "Company Name": "Maple Finance",
      "Job Description": "<div class=\"JobPage_jobDescription__abc123\">...</div>",
      "Job Location": null,
      "Compensation": "$125K – $273K",
      "Job Url": "https://findweb3.com/job/rec449xAzZN4Rw8lO",
      "Tags": "python, solidity, defi",
      "Notes": ""
    }
  ]
}
```

`jobs` is the concatenation of every scraper's results, each already filtered and ranked down to
its own `max_jobs_saved` (see [Filtering](#filtering) above). Field notes:
- `Job Description` is raw HTML as extracted from the page — not sanitized or converted to plain text.
- `Tags` is a comma-separated string of the request's `keywords` that matched this specific posting.
- `Notes` is always `""` — reserved for manual fill-in downstream.
- `Company Name`, `Job Location`, and `Compensation` are `null`/empty when the scraper couldn't find a matching element on the page.

### Errors

- `POST /jobs` — request body fails Pydantic validation (missing/wrong-typed field) → `422`, handled automatically by FastAPI. Otherwise always `202` — scraping failures surface later, via polling.
- `GET /jobs/{job_id}/status` and `GET /jobs/{job_id}/result` — unknown `job_id` (never existed, or purged after an hour) → `404`.
- `GET /jobs/{job_id}/result` — job still `running` → `409`; job `status == "error"` (scraper configuration or `run()` raised) → `500` with the stored error message.

## Adding a new scraper

1. Create `scrapers/{site}_jobscraper.py` implementing this interface:

   ```python
   class SomeScraper:
       def __init__(self, max_jobs_per_keyword: int, top_n_results: int) -> None: ...
       def run(self, keywords: list) -> list: ...
       # run() returns a clean list of job dicts (matching the output schema above) —
       # no file I/O, no internal/"_"-prefixed fields.
   ```

2. Export it from `scrapers/__init__.py`.
3. Import and add it to the list returned by `_build_scrapers()` in `app.py`:

   ```python
   def _build_scrapers(request: JobsRequest, on_progress=None) -> list:
       return [
           ("findweb3.com", Fw3Scraper(max_jobs_per_keyword=request.max_jobs_searched_per_keyword, top_n_results=request.max_jobs_saved, on_progress=on_progress, ...)),
           ("otta.com", OttaScraper(max_jobs_per_keyword=20, top_n_results=10)),
       ]
   ```

If the new scraper should support the same discarded-URL/location/anti-keyword/experience
filtering and best-of-N ranking, accept `acceptable_locations`, `anti_keywords`, `discarded_urls`,
and `max_experience_years` in its `__init__`, apply the predicates from `utils/filters.py` inline
in its own search loop (checking `discarded_urls` first, before any page load), and rank all
surviving candidates by score before cutting to `top_n_results` — the same way `Fw3Scraper` does.
If it should report progress too, accept an `on_progress(stage, current, total)` kwarg and call it
at the same points it would otherwise `logger.info` progress.

No changes to `create_job()`'s orchestration loop in `app.py` are needed — it just iterates
`_build_scrapers(request, on_progress)` and concatenates results.

## Notes / known limitations

- Scraping depends on findweb3.com's current HTML structure — if the site's markup changes, the
  class/data-testid-based selectors in `scrapers/fw3_jobscraper.py` (used for `Company Name` and
  `Job Description`) may break and start returning `null`.
- `robots.txt` is respected and randomized delays (1–2.5s) are added between page loads to avoid
  hammering the site.
- `Job Url` points to the findweb3.com listing page, not an external application link — this
  scraper does not follow the "Apply" button.
- Scraping runs as a FastAPI `BackgroundTask`. Since it's a sync function (Selenium is sync),
  Starlette runs it via its threadpool rather than on the event loop, so it doesn't block other
  requests — but it also means there's no hard concurrency limit beyond that threadpool; many
  simultaneous jobs will contend for it (and for whatever CPU/network the underlying Chrome
  processes need).
- Job tracking is in-memory and per-process: it doesn't survive a restart, isn't shared across
  multiple server instances, and is purged after an hour. If you need durability or horizontal
  scaling, this would need to move to something like Redis or a database.
- No config file and no scraping parameters in env vars: every request must supply the full
  `JobsRequest` body. If you want to avoid re-scraping jobs a user already saw/dismissed, the
  caller is responsible for tracking those URLs and passing them back in `discarded_urls` on the
  next request.
