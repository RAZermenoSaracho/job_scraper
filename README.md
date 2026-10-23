# job_scraper

A FastAPI service that scrapes job boards for postings matching a set of keywords, ranks each
posting by how many *unique* keywords it mentions, and returns the top-N results as JSON.
Sources: [findweb3.com](https://findweb3.com) (Selenium), [web3.career](https://web3.career) (official
API with a token, plus the legacy HTML scraper still used when `sources` is omitted),
[Hashtag Web3](https://hashtagweb3.com) (public JSON feed, **personal non-commercial use only**) and
employer boards on Greenhouse, Lever and Ashby (public JSON APIs, selected via `boards`).
LinkedIn, Wellfound, Bitcoiner Jobs and LaborX are listed in `GET /sources` as manual (`executable:
false`) and are never fetched; Cyfrin Jobs is defunct. New sources are independent scraper modules
registered in `scrapers/__init__.py`, without touching the request-handling logic. There's no config file
and no scraping parameters in env vars — every job is fully described by its `POST /jobs` request
body. Scraping runs as a background task and is tracked via an in-memory `job_id`: nothing is
written to disk, and the job-status tracking itself is ephemeral (in-process only, purged after an
hour, lost on restart) — it exists purely so a slow scrape doesn't have to hold an HTTP connection
open the whole time.

## Project structure

```
.
├── app.py                     # FastAPI: POST /jobs, GET /jobs/{id}/status|result, GET /sources
├── ecosystem.config.cjs       # pm2 process (port 8420); loads WEB3CAREER_API_TOKEN from the token file
├── requirements.txt           # Pinned dependencies for the `job_scraper` conda env
├── utils/
│   ├── filters.py             # term_hits (single matching rule) + filter predicates
│   ├── normalize.py           # canonical URL, ids, dedupe key, work mode, normalized row builder
│   └── http.py                # polite GET (User-Agent, timeout, retry/backoff, Retry-After, redaction)
├── scrapers/
│   ├── __init__.py            # SOURCES registry + catalog (GET /sources)
│   ├── base.py                # BaseScraper: shared kwargs, filter chain, ranking, deadline
│   ├── fw3_jobscraper.py      # findweb3.com (Selenium)
│   ├── web3career_api.py      # web3.career official API (needs WEB3CAREER_API_TOKEN)
│   ├── web3career_jobscraper.py  # web3.career legacy HTML scraper (default until cut-over)
│   ├── hashtagweb3_feed.py    # Hashtag Web3 JSON feed
│   └── ats_scraper.py         # Greenhouse / Lever / Ashby boards
└── tests/                     # offline pytest + recorded fixtures (tests/fixtures)
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
| `keywords` | yes | — | Search queries and relevance scoring/tagging (see [Ranking](#ranking-evaluate-all-then-pick-the-best-n)) |
| `max_jobs_searched_per_keyword` | yes | — | Candidate postings explored per keyword and aggregator source |
| `max_jobs_saved` | yes | — | **Global** cap on returned postings (after merging all sources) |
| `acceptable_locations` | no | `[]` | Location filter (case-insensitive substring; unknown location passes) |
| `anti_keywords` | no | `[]` | Any hit excludes the posting |
| `max_experience_years` | no | `null` | Experience filter; `null` = off |
| `discarded_urls` | no | `[]` | Job URLs to skip entirely — filtered during search, never occupy a slot |
| `sources` | no | `null` | Source ids to run; `null` = `findweb3` + `web3career` (legacy path). Unknown or non-executable ids → 422 naming them |
| `boards` | no | `[]` | ATS boards `"greenhouse:stripe"`, `"lever:netflix"`, `"ashby:ramp"`; malformed → 422; empty = no ATS runs |
| `known_keys` | no | `[]` | Canonical URLs, `"<source>:<source_job_id>"` or `dedupe_key`s already seen; skipped before any detail fetch |
| `since` | no | `null` | ISO 8601; rows with a known older `posted_at` are dropped (undated kept) |
| `work_modes` | no | `[]` | Subset of `remote`, `hybrid`, `onsite` (`unknown` always kept) |
| `seniority_exclude` | no | `[]` | Whole-word match on the title only (e.g. `senior`, `staff`) |
| `excluded_companies` | no | `[]` | Normalized equality with the company name |
| `match_mode` | no | `"substring"` | `"word"` = whole words/phrases, accent/case/punctuation-insensitive |
| `deadline_seconds` | no | `null` | Overall budget; on expiry partial results + an `errors[]` entry |

The seniority, work-mode and experience filters are best-effort text heuristics.

A request missing a required field, or with the wrong type, gets rejected by FastAPI/Pydantic with
`422` before the handler ever runs — no manual validation needed for that case.

## Filtering

`discarded_urls` is filtered **during search**, not afterward — see
[Discarding already-seen URLs](#discarding-already-seen-urls-without-shrinking-the-search) below.
The other three filters happen **inside each scraper**, inline while it visits candidates that
survived the search-time discard check, not in `app.py` after the fact. `Fw3Scraper` runs each
candidate through three filter predicates from [utils/filters.py](utils/filters.py), in this order:

1. **`passes_location`** — passes if `Job Location` is empty/unknown, or contains (case-insensitive
   substring) any entry from `acceptable_locations`; fails otherwise. No-op if `acceptable_locations`
   is `[]`.
2. **`passes_anti_keywords`** — fails if `Job Title` + plain-text `Job Description` (HTML stripped,
   for matching only — the stored field stays raw HTML) contains any `anti_keywords` entry. No-op
   if the list is `[]`.
3. **`passes_experience`** — scans the same title + description text for "years of experience"
   mentions (`"N+ years"`, `"N-M years"`, plain `"N years"` — which also covers `"at least N years"`
   / `"minimum of N years"`). For a range, only the **minimum** is compared: a job asking for
   `"3-7 years"` passes if `max_experience_years` is `4`, since you clear the 3-year floor. Fails
   only if some mention's minimum exceeds `max_experience_years`; jobs with no experience mention
   pass.

A candidate that fails any filter is dropped immediately and the scraper moves on to the next
candidate URL.

### Discarding already-seen URLs without shrinking the search

`passes_discarded_urls` runs inside `search_by_keyword` itself, as it scans the listing page's
links — **before** a URL is even added to the candidate pool, let alone before its detail page is
loaded. This matters: if discarding only happened later (during the candidate-visit loop), a
discarded URL would still have consumed one of the `max_jobs_searched_per_keyword` slots during
search, so every re-request with the same `discarded_urls` would "find" the same already-discarded
jobs again and again, permanently crowding out undiscovered ones. Instead, `search_by_keyword` skips
discarded links as it encounters them and keeps scanning further links on the (already fully
loaded) page until it has `max_jobs_searched_per_keyword` valid ones, or the page's links run out —
so it costs no extra network requests, just a wider scan of the same page.

### Ranking (evaluate all, then pick the best N)

The scraper does **not** stop as soon as it has `max_jobs_saved` passing candidates. It visits
*every* candidate returned by search (up to `max_jobs_searched_per_keyword` per keyword, already
excluding anything in `discarded_urls`), and each one that survives the other three filters gets a
relevance score — the count of unique `keywords` matched in its title + description. Only after all
candidates have been visited does it sort the surviving pool by score (descending) and return the
best `max_jobs_saved`. This matters because stopping early at the first N passing candidates would
return "first found," not "most relevant" — a higher-scoring candidate found later wouldn't get a
chance to bump out a weaker, earlier one.

Each scraper logs drop counts per filter, plus the score range of its final selection, e.g.:

```
13:32:50 [INFO] Filtered 40 candidate(s) found: 12 passed (dropped — discarded_urls: 6, location: 15, anti_keywords: 2, experience: 5)
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
uvicorn app:app --reload --port 8420
```

Kick off a job:

```bash
curl -X POST "http://127.0.0.1:8420/jobs" \
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
curl "http://127.0.0.1:8420/jobs/b43473f6-c35d-4639-92b4-a9f3bd4a41d8/status"
# -> {"status": "running", "stage": "scraping", "current": 7, "total": 20}

curl "http://127.0.0.1:8420/jobs/b43473f6-c35d-4639-92b4-a9f3bd4a41d8/result"
# -> 200 {"keywords": [...], "jobs": [...]}   (or 409 while still running)
```

Interactive API docs (Swagger UI) are available at `http://127.0.0.1:8420/docs` once the server is
running.

CORS is wide open (`ALLOWED_ORIGINS = ["*"]` in `app.py`, applied once via `CORSMiddleware` to the
whole app — it covers all three routes above). **Restrict this to the real frontend's production
domain(s) before deploying either service.**

## Response format (`GET /jobs/{job_id}/result`)

```json
{
  "schema_version": "1",
  "keywords": ["python", "solidity"],
  "errors": [{"source": "lever:netflix", "error": "HTTP 404 (not found)"}],
  "jobs": [
    {
      "Job Title": "Senior Solidity Engineer",
      "Company Name": "Maple Finance",
      "Job Description": "<div>...</div>",
      "Job Location": "Remote",
      "Compensation": "$125K – $273K",
      "Job Url": "https://findweb3.com/job/rec449xAzZN4Rw8lO",
      "Tags": "python, solidity",
      "Notes": "",
      "source": "findweb3",
      "source_job_id": "rec449xAzZN4Rw8lO",
      "canonical_url": "https://findweb3.com/job/rec449xAzZN4Rw8lO",
      "apply_url": null,
      "company": "Maple Finance",
      "title": "Senior Solidity Engineer",
      "location": "Remote",
      "work_mode": "remote",
      "posted_at": null,
      "description_html": "<div>...</div>",
      "description_text": "...",
      "salary": "$125K – $273K",
      "ats_type": null,
      "tags": ["python", "solidity"],
      "dedupe_key": "maple finance|senior solidity engineer|remote"
    }
  ]
}
```

The legacy keys (`Job Title` … `Notes`) are unchanged for the existing frontend; the snake_case keys
are the normalized contract (additive; unknown values are `null`, `tags` an empty list). Consumers
should ignore unknown fields and check `schema_version`. `jobs` is the merged, de-duplicated list of
every source's results, ranked by score and cut to `max_jobs_saved` globally. Field notes:
- `Job Description` / `description_html` is raw HTML — not sanitized; `description_text` is the plain-text form.
- `Tags`/`tags` are the request `keywords` that matched this posting; `Notes` is always `""`.
- `Job Url` is the listing page (or canonical ATS URL); `apply_url` is the employer/ATS/API apply link when the source provides one (web3.career API: passed through unmodified).
- `errors[]` lists failed sources/boards (and `deadline exceeded`); the run is `done` unless every source failed.

## Sources: `GET /sources`

Static catalog (no network I/O) with `id`, `name`, `category`, `availability`, `executable`,
`requires_config`, `filters`, `hosts`, `ingestion` (plus a `reason` when not executable).
`executable` is computed from the registry: `web3career` is executable only when
`WEB3CAREER_API_TOKEN` is set (pm2 reads it from the token file at startup). Sample:

```json
{"schema_version": "1", "sources": [
  {"id": "findweb3", "category": "job_board", "availability": "available", "executable": true, "requires_config": null},
  {"id": "web3career", "category": "job_board", "availability": "available", "executable": true, "requires_config": "token:WEB3CAREER_API_TOKEN"},
  {"id": "hashtagweb3", "category": "job_board", "availability": "available", "executable": true, "requires_config": null},
  {"id": "greenhouse", "category": "ats", "availability": "available", "executable": true, "requires_config": "boards"},
  {"id": "linkedin", "category": "job_board", "availability": "manual", "executable": false, "ingestion": "Paste the job URL into Odoo ..."},
  {"id": "cyfrin", "category": "job_board", "availability": "defunct", "executable": false}
]}
```

(abridged; each entry carries all keys). Hashtag Web3 data is used for personal, non-commercial
purposes only, fetched at most once per run.

### Errors

- `POST /jobs` — request body fails Pydantic validation (missing/wrong-typed field) → `422`, handled automatically by FastAPI. Otherwise always `202` — scraping failures surface later, via polling.
- `GET /jobs/{job_id}/status` and `GET /jobs/{job_id}/result` — unknown `job_id` (never existed, or purged after an hour) → `404`.
- `GET /jobs/{job_id}/result` — job still `running` → `409`; job `status == "error"` (scraper configuration or `run()` raised) → `500` with the stored error message.

## Adding a new scraper

1. Subclass `BaseScraper` (`scrapers/base.py`) in `scrapers/{site}.py`; implement `run(keywords)`;
   build rows with `utils.normalize.build_row`; during search call `self.skip_url(url)` (discarded +
   known keys), per candidate call `self.evaluate(row, keywords, drops)` (the shared filter chain
   and matching rule), and finish with `self.select(...)`. Honour `self.expired()` (deadline).
2. Register it in `SOURCES` in `scrapers/__init__.py` (`adapter`, `category`, `availability`,
   `requires_config`, `hosts`, ...). `executable` is computed — never set by hand.
3. Add recorded fixtures under `tests/fixtures/` and offline tests; run `pytest`.

`_build_scrapers()` in `app.py` iterates the registry, so no orchestration changes are needed.

## Notes / known limitations

- findweb3 depends on the site's markup: selectors fall back from class substrings to structural
  XPath and JSON-LD, and a field nothing can find is `null` with a logged warning.
- `robots.txt` is respected and randomized delays (1–2.5s) are added between page loads to avoid
  hammering the site.
- This service never applies to jobs; `Job Url`/`apply_url` are links only.
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
