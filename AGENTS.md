# job_scraper

A FastAPI service that scrapes job boards for roles matching a set of keywords, ranks results by
keyword relevance, and returns structured JSON over HTTP. There is no config file and no
server-side scraping config in env vars — every `POST /jobs` request fully describes the run
(keywords, location/anti-keyword/discarded-URL filters, and limits) in its body. Nothing is
written to disk. Scraping runs as a background task tracked by an in-memory `job_id` (`jobs_status`
in `app.py`) — the caller polls for progress and fetches the result once it's done, rather than
holding one HTTP request open for the whole scrape. That job-tracking dict is itself ephemeral
(in-process, purged after an hour, lost on restart) — it exists only to support polling, not as
durable state.

## Project structure

```
.
├── app.py                 # FastAPI app: POST /jobs schedules a background scrape; GET .../status and .../result poll it
├── utils/
│   └── filters.py         # Per-candidate filter predicates (discarded URLs, location, anti-keywords, required experience)
└── scrapers/
    └── fw3_jobscraper.py  # Fw3Scraper class for findweb3.com — filters + ranks candidates internally, reports progress
```

There is no `keywords.json` and no scraping-related `.env` values — see [API](#api) below for
where that config now lives (the request body).

## Context

This project is part of a personal job search workflow (see sibling folder `job_apps/`, which contains tailored resumes and job descriptions for specific applications, e.g. `job_apps/wintermute_algo_trader/`). The scraper's job is purely discovery/ranking — it does not apply to jobs or fill out application forms.

## utils/filters.py

Exposes four per-candidate **predicate** functions, all no-ops (always pass) when their
corresponding config is empty/`None`:

1. **`passes_discarded_urls(url, discarded_urls)`** — operates on the raw candidate URL string
   (a `set`), not a job dict. Fails if `url` is in `discarded_urls`. Scrapers check this **before**
   making any HTTP request for the candidate's detail page — it's the cheapest filter, so it runs
   first and skips a page load entirely for already-unwanted URLs.
2. **`passes_location(job, acceptable_locations)`** — passes if `Job Location` is empty/`None`
   (unknown location isn't disqualifying), or contains (case-insensitive substring) any entry from
   `acceptable_locations`.
3. **`passes_anti_keywords(job, anti_keywords)`** — fails if `job_text(job)` contains any
   `anti_keywords` entry (case-insensitive substring).
4. **`passes_experience(job, max_experience_years)`** — extracts every "years of experience"
   mention from `job_text(job)` via `EXPERIENCE_PATTERN` (a regex matching `"N+ years"`,
   `"N-M years"`, and plain `"N years"` — the latter also covers `"at least N years"` /
   `"minimum of N years"` since the number still immediately precedes "years"). For a range like
   `"N-M years"`, only the minimum `N` is compared — a job requiring `"3-7 years"` passes if
   `max_experience_years >= 3`, even though 4 doesn't reach 7. Fails only if *some* mention's
   minimum exceeds `max_experience_years`. Jobs with no experience mention at all pass.

The last three take a job dict already in the output schema shape. `job_text(job)` (also exported)
builds `Job Title` + plain-text `Job Description` (HTML tags stripped) — used by both these
predicates and the scraper's relevance scoring, so filtering and scoring always look at the same
text. `app.py` never imports this module; scrapers call these functions themselves, inline.

## scrapers/fw3_jobscraper.py

Exposes a single class `Fw3Scraper`. Key constraints specific to this site:
- The site's URL scheme only supports one keyword per search: `https://findweb3.com/jobs/{keyword}`
- Uses headless Selenium (no public API available)
- Caps the number of postings explored per keyword via `max_jobs_per_keyword` (constructor param)
- `Company Name` and `Job Description` are extracted from the job detail page via best-effort
  XPath selectors (class/data-testid containing "company"/"description"); `Job Description` is
  stored as raw HTML, unsanitized — no plain-text conversion is done in the scraper.
- **Filters inline, then ranks.** `run()` visits every candidate URL found during search (up to
  `max_jobs_per_keyword` per keyword). For each: `passes_discarded_urls` is checked first, before
  any page load. If it passes, the candidate's fields are extracted and mapped to the output
  schema, then run through `passes_location` → `passes_anti_keywords` → `passes_experience`.
  Rejected candidates are dropped immediately. A candidate that survives all four is scored (count
  of unique `keywords` matches in its `job_text`), and only *after every candidate has been
  visited* is the surviving pool sorted by score (descending) and cut down to the best
  `top_n_results`. This is deliberate: stopping early at the first `top_n_results` passing
  candidates would return "first found," not "most relevant" — a later candidate with a much better
  score wouldn't get a chance to bump out an earlier, weaker one. Drop counts per filter, plus the
  min/max score among the final selection, are logged at the end of `run()`.
- **Reports progress via an optional callback, not just logs.** The constructor accepts
  `on_progress: Callable[[str, int, int], None] | None`. `run()` calls `self._report(stage,
  current, total)` at the same three points it already calls `logger.info` with progress info
  (once per keyword while searching, once per candidate while scraping, once at the end with the
  final filtered/passed counts) — `_report` just invokes `on_progress` if one was given. The
  scraper has zero knowledge of FastAPI, background tasks, or `jobs_status`; `app.py` supplies a
  closure that writes into `jobs_status[job_id]`.

### Scraper interface contract

Every scraper class must expose:

```python
class SomeScraper:
    def __init__(self, max_jobs_per_keyword: int, top_n_results: int) -> None: ...
    def run(self, keywords: list) -> list: ...
    # run() returns a clean list of job dicts matching the output schema below —
    # no file I/O, no _ fields
```

`Fw3Scraper` additionally accepts `acceptable_locations`, `anti_keywords`, `discarded_urls`,
`max_experience_years`, and `on_progress` kwargs (all optional, default to "no filtering" / "no
progress reporting") so it can apply `utils/filters.py` inline, rank by relevance, and report
progress. Any future scraper that wants the same support should accept the same kwargs and apply
the same predicates + ranking + progress calls during its own search loop — this isn't (yet)
enforced as part of the strict interface contract above, since not every scraper will need it.

`app.py` is solely responsible for validating the request body and shaping the HTTP response. It
does not filter, score, or otherwise inspect job contents, and it reads no config file or env var
for scraping parameters — scrapers are the only place that filtering/scoring happens, and the
request body is the only source of scraping config.

### Adding a new scraper

1. Create `scrapers/{site}_jobscraper.py` following the interface above.
2. Instantiate the class in `app.py`'s `_build_scrapers()` function.
3. No changes to `create_job()` / `_run_scrape_job()`'s orchestration loop needed.

**Multi-scraper top-N note:** each scraper applies its own `top_n_results` cut internally (on its
own filtered-and-ranked pool). `app.py` concatenates all results as-is. If a global re-rank across
sites is ever needed, sort the combined list by a shared score field in `app.py` before returning
the response.

## API

Run locally:

```bash
conda activate job_scraper
uvicorn app:app --reload
```

CORS is wide open (`ALLOWED_ORIGINS = ["*"]` in `app.py`, applied once via `CORSMiddleware` for the
whole app — covers all three routes below) for development. **Restrict this to the real frontend's
production domain(s) before deploying either service.**

Scraping is async: `POST /jobs` schedules the work and returns a `job_id`; the caller polls
`GET /jobs/{job_id}/status` and then fetches `GET /jobs/{job_id}/result`. This exists because a
full scrape (one page load per keyword, plus one per candidate) can take 2-4 minutes — long enough
to exceed some edge/tunnel proxy timeouts (e.g. Cloudflare Tunnel) if done synchronously in one
request/response cycle.

### `POST /jobs` — schedule a scrape

Request body — validated by the `JobsRequest` Pydantic model in `app.py`. **This fully replaces
`keywords.json` and the scraping env vars** — there is no server-side fallback for any of these:

```json
{
  "keywords": ["python", "solidity", "..."],
  "acceptable_locations": ["remote", "mexico", "..."],
  "anti_keywords": ["senior staff", "..."],
  "discarded_urls": ["https://findweb3.com/job/rec123", "..."],
  "max_jobs_searched_per_keyword": 20,
  "max_jobs_saved": 10,
  "max_experience_years": 4
}
```

| Field | Required? | Default |
|---|---|---|
| `keywords` | yes | — |
| `acceptable_locations` | yes | — |
| `anti_keywords` | no | `[]` |
| `discarded_urls` | no | `[]` |
| `max_jobs_searched_per_keyword` | yes | — |
| `max_jobs_saved` | yes | — |
| `max_experience_years` | yes | — |

`discarded_urls` — job URLs (as previously returned in a job's `Job Url` field) to exclude from
this run. Checked as early as possible per candidate (see `passes_discarded_urls` above), so a
client re-requesting after discarding some results doesn't pay for re-scraping them.

A malformed body (missing a required field, wrong type) never reaches the handler — FastAPI/Pydantic
reject it with `422` automatically before `create_job()` runs. Otherwise, this endpoint always
succeeds with `202 {"job_id": "<uuid4>"}` — a scraper failure surfaces later, via polling, not here.

`create_job()`:
1. Calls `_purge_old_jobs()` (drops any `jobs_status` entries older than `JOB_STATUS_TTL_SECONDS`,
   currently 1 hour — the only "cleanup" mechanism, no scheduler).
2. Generates `job_id = str(uuid.uuid4())` and seeds `jobs_status[job_id]` with
   `{"status": "running", "stage": "starting", "current": 0, "total": 0, "result": None, "error":
   None, "created_at": time.time()}`.
3. Schedules `_run_scrape_job(job_id, request)` via `BackgroundTasks.add_task(...)`. `_run_scrape_job`
   is a plain sync function; Starlette runs sync background callables through its threadpool (not
   the event loop), so the Selenium calls inside it don't block other requests.
4. Returns `{"job_id": job_id}` immediately.

`_run_scrape_job` builds a closure `on_progress(stage, current, total)` that does
`jobs_status[job_id].update(stage=stage, current=current, total=total)`, passes it into
`_build_scrapers(request, on_progress)`, runs each scraper, and on completion sets
`jobs_status[job_id]` to `status="done"`, `stage="done"`, and
`result={"keywords": ..., "jobs": ...}`. If building scrapers or any scraper's `run()` raises, it
sets `status="error"` and `error="<message>"` instead, and returns without running further
scrapers.

### `GET /jobs/{job_id}/status` — poll progress

```json
{"status": "running", "stage": "scraping", "current": 7, "total": 20}
```

- `status`: `running` | `done` | `error`.
- `stage`: `starting` → `searching_keywords` → `scraping` → `filtering` → `done` (or `error`).
- `current`/`total`: stage-relative counters, as reported by the scraper's `on_progress` callback.
- If `status == "error"`, the body also includes `"error": "<message>"`.
- `404` if `job_id` isn't in `jobs_status` (never existed, or already purged).

### `GET /jobs/{job_id}/result` — fetch the finished output

Returns the same shape previously written to the output JSON file, once `status == "done"`:

```json
{
  "keywords": ["python", "defi", "..."],
  "jobs": [
    {
      "Job Title": "DeFi Algorithmic Trading Internship",
      "Company Name": "Wintermute",
      "Job Description": "<div>...</div>",
      "Job Location": "Fully Remote",
      "Compensation": null,
      "Job Url": "https://findweb3.com/job/recoUnkn8Eb9kNPcK",
      "Tags": "python, defi, quantitative",
      "Notes": ""
    }
  ]
}
```

Field notes:
- `Job Description` is the raw HTML extracted from the site — not sanitized or converted to plain text.
- `Tags` is a comma-separated string of the request's `keywords` that matched this specific posting.
- `Notes` is always `""` — reserved for manual fill-in downstream.
- `Job Url` points to the listing on the source site (e.g. findweb3.com), not an external application link.

Errors: `404` if `job_id` is unknown; `409` if `status == "running"` (with a message saying so);
`500` with the stored error message if `status == "error"`.

## Environment

This project uses a dedicated **conda** environment named `job_scraper` (Python 3.11). Always activate it before running any `pip install`, `python`, or dependency-related command:

```bash
conda activate job_scraper
```

- Do not install packages globally or into any other environment — always confirm `job_scraper` is active first (check with `conda info --envs` or by inspecting the shell prompt).
- Dependencies are tracked in `requirements.txt` at the project root. After installing or updating any package, run `pip freeze > requirements.txt` to keep it in sync.
- To recreate the environment from scratch:

```bash
conda create -n job_scraper python=3.11 -y
conda activate job_scraper
pip install -r requirements.txt
```

- Key dependencies expected in this project: `selenium`, `webdriver-manager` (for automatic ChromeDriver management in headless scraping — pulls in `python-dotenv` as its own transitive dependency, not something `app.py` uses), `fastapi` and `uvicorn` (for the HTTP API).
- **No env vars configure scraping** (no `MAX_JOBS_SEARCHED_PER_KEYWORD`, `MAX_JOBS_SAVED`, `MAX_EXPERIENCE_YEARS`, or `OUTPUT_DIR` — all removed). Everything scraping-related comes from the `POST /jobs` request body, every request, with no fallback. Any env vars for how the server itself runs (host/port, etc.) are unrelated to this and would be passed to `uvicorn` directly (e.g. `uvicorn app:app --host 0.0.0.0 --port 8000`).

## Conventions

- Each new site-specific scraper lives in `scrapers/` (e.g. future additions like `otta_jobscraper.py`, `cryptojobs_jobscraper.py`) and should follow the same output schema so results from multiple sources stay compatible.
- Site-specific quirks (URL schemes, headless browser needs, rate limiting) are handled inside each scraper module — `app.py` should stay a thin request handler that doesn't need to know about per-site scraping details.
- Be respectful of target sites: check `robots.txt`, add delays between requests, and avoid hammering job detail pages.
