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
├── app.py                    # FastAPI: POST /jobs, GET /jobs/{id}/status|result, GET /sources; merge/rank/cut, errors, deadline, optional X-API-Key
├── ecosystem.config.cjs      # pm2: job-scraper-api on 127.0.0.1:8420; loads WEB3CAREER_API_TOKEN from the token file at startup
├── utils/
│   ├── filters.py            # term_hits (the one matching rule) + per-candidate predicates
│   ├── normalize.py          # canonical_url, identify_url, dedupe_key, work mode, html_to_text, build_row (normalized row)
│   └── http.py               # polite GET: User-Agent, 15 s timeout, 3 tries, backoff+jitter, Retry-After, secret redaction
├── scrapers/
│   ├── __init__.py           # SOURCES registry (single source of truth), adapter_for, catalog, source_state
│   ├── base.py               # BaseScraper: shared kwargs, evaluate() filter chain, select() ranking, deadline
│   ├── fw3_jobscraper.py     # findweb3.com (Selenium, hardened)
│   ├── web3career_jobscraper.py  # web3.career legacy HTML scraper (frontend default path until API cut-over)
│   ├── web3career_api.py     # web3.career official API (needs WEB3CAREER_API_TOKEN)
│   ├── hashtagweb3_feed.py   # Hashtag Web3 JSON feed (one fetch per run, personal non-commercial use)
│   └── ats_scraper.py        # Greenhouse / Lever / Ashby board APIs
└── tests/                    # offline pytest; recorded fixtures in tests/fixtures/{ats,web3}
```

There is no `keywords.json` and no scraping-related `.env` values — see [API](#api) below for
where that config lives (the request body). The only env vars are operational: `WEB3CAREER_API_TOKEN`
(web3.career API), `WEB3CAREER_LEGACY_SCRAPER` (cut-over flag), `JOB_SCRAPER_API_KEY` (optional
`X-API-Key` guard), `JOB_SCRAPER_CONTACT` (put in the User-Agent of the plain-HTTP adapters).

## Context

This project is part of a personal job search workflow (see sibling folder `job_apps/`, which contains tailored resumes and job descriptions for specific applications, e.g. `job_apps/wintermute_algo_trader/`). The scraper's job is purely discovery/ranking — it does not apply to jobs or fill out application forms.

## utils/filters.py

Per-candidate **predicates**, all no-ops when their config is empty/`None`:

- **`term_hits(job, terms, match_mode)`** — the ONE keyword/anti-keyword rule for every source,
  on `job_text(job)` (title + tag-stripped description). `substring` (default) is the original
  case-insensitive substring test; `word` normalizes with `norm_text` (accents, case, punctuation,
  whitespace) and matches whole words/phrases only. Returns the unique terms hit (original casing);
  empty terms are ignored. `Tags`/score = this list; `passes_anti_keywords` is `not term_hits(...)`.
- **`passes_discarded_urls(url, set)`** — exact match on the raw URL; scrapers check it during
  **search** so a discarded URL never consumes a `max_jobs_per_keyword` slot or costs a detail fetch.
- **`url_is_known` / `row_is_known` / `expand_known_keys`** — `known_keys` (canonical URL,
  `"<source>:<source_job_id>"` or `dedupe_key`): URL/id forms are checked at search time (skip before
  fetch), `dedupe_key` after fetch.
- **`passes_location`** (empty location passes), **`passes_experience`** (compares the minimum of
  "N-M years"), **`passes_since`** (undated rows are kept), **`passes_work_mode`** (listed or
  `unknown`), **`passes_seniority`** (whole word, *title only*, any `match_mode`),
  **`passes_excluded_companies`** (`norm_text` equality, not substring).

`BaseScraper.evaluate()` (scrapers/base.py) runs them in this order: location → anti_keywords →
experience → since → work_mode → seniority → excluded_companies → known dedupe_key, then keyword
matching/score. Scrapers must not re-implement any of it. Seniority and work-mode are best-effort
text heuristics.

## utils/normalize.py — the normalized row

Every scraper builds rows with `build_row(...)`: the legacy keys (`Job Title`, `Company Name`,
`Job Description` (raw HTML), `Job Location`, `Compensation`, `Job Url`, `Tags` (csv), `Notes`)
**unchanged**, plus `source`, `source_job_id`, `canonical_url`, `apply_url`, `company`, `title`,
`location`, `work_mode` (`remote|hybrid|onsite|unknown`), `posted_at` (ISO UTC or null),
`description_html`, `description_text`, `salary`, `ats_type`, `tags` (list), `dedupe_key`. Unknown
values are `null` (never omitted). The result envelope is `{schema_version: "1", keywords, jobs,
errors}`; consumers must ignore unknown fields. `canonical_url` is deterministic and idempotent
(tracking params stripped; Greenhouse/Lever/Ashby/LinkedIn/Wellfound/web3.career/findweb3 forms,
see `identify_url`). `source_job_id` is the source's id; sources without one fall back to
`sha1(canonical_url)` (`id_fallback=True`).

## Scrapers

All scrapers subclass `BaseScraper` (kwargs: `max_jobs_per_keyword`, `top_n_results`,
`acceptable_locations`, `anti_keywords`, `discarded_urls`, `max_experience_years`, `on_progress`,
`known_keys`, `since`, `work_modes`, `seniority_exclude`, `excluded_companies`, `match_mode`,
`deadline`) and expose `run(keywords) -> list[row]`. Non-fatal problems go in `self.errors`
(`{source, error}`); `self.deadline_hit` marks a deadline stop. Each scraper ranks its own pool
(score desc, stable) and cuts to `top_n_results`; `app.py` merges and applies the global cap.

- **findweb3** (`fw3_jobscraper.py`) — headless Selenium (no public API; `findweb3.com/api/` is
  robots-disallowed and never called). No anti-detection options; chromedriver from PATH, else
  Selenium Manager. Resilient selectors (class substrings, structural XPath, JSON-LD) degrade to
  `null` + a logged warning; page loads retry with backoff and treat a 429 page as a failure.
  One keyword per search URL `https://findweb3.com/jobs/{keyword}`.
- **web3career** — official API (`web3career_api.py`): `GET https://web3.career/api/v1` with the
  token (query param, always redacted), `limit` ≤ 100, **no paging** (docs + live check), one
  request per keyword/tag, `apply_url` passed through byte-for-byte. Legacy HTML scraper
  (`web3career_jobscraper.py`, `requests` + JSON-LD) stays the default when `sources` is omitted
  until `WEB3CAREER_LEGACY_SCRAPER=0` and the token is set (cut-over), then the API adapter serves
  the default path too. Removing the legacy scraper is deferred until the cut-over is verified.
- **hashtagweb3** (`hashtagweb3_feed.py`) — one GET of the public JSON feed per run (cached by
  ETag/Last-Modified), personal non-commercial use only, client-side filtering; `link` usually is an
  ATS URL so `canonical_url` converges with ATS rows. No descriptions (title-only matching).
- **greenhouse / lever / ashby** (`ats_scraper.py`) — public board JSON APIs via `utils/http.py`; one
  GET per board, sequential with a 1 s gap, per-board failures in `errors[]`. Kept only if at least
  one keyword hits.
- Catalog-only (never fetched): `linkedin`, `wellfound`, `bitcoinerjobs`, `laborx` (manual — paste
  the URL into Odoo) and `cyfrin` (defunct). LinkedIn: no adapter, no browser extraction, ever.

### Source registry and `GET /sources`

`SOURCES` in `scrapers/__init__.py` is plain data (adapter class or `None`, label, category,
availability, needs_browser, requires_config, filters, hosts, ingestion). `executable` is
**computed** (`source_state`): adapter registered AND `requires_config` satisfied (e.g.
`token:WEB3CAREER_API_TOKEN` set); unmet config shows availability `planned` with a `reason`.
`GET /sources` returns `{schema_version, sources: [{id, name, category, availability, executable,
requires_config, filters, hosts, ingestion, reason}]}` with no network I/O. `sources` in a request
is validated against it (unknown or non-executable ids → 422 naming them).

### Adding a new scraper

1. Subclass `BaseScraper` in `scrapers/{site}.py`; build rows with `build_row`; call
   `self.skip_url(url)` during search, `self.evaluate(row, keywords, drops)` per candidate,
   `self.select(...)` at the end; honour `self.expired()`.
2. Register it in `SOURCES` (`scrapers/__init__.py`). `_build_scrapers` in `app.py` picks it up.
3. Add fixtures under `tests/fixtures/` and offline tests.

## API

Run locally (the pm2 service `job-scraper-api` listens on **127.0.0.1:8420**):

```bash
conda activate job_scraper
uvicorn app:app --reload --port 8420
```

CORS is wide open (`ALLOWED_ORIGINS = ["*"]` in `app.py`). **Restrict this to the real frontend's
production domain(s) before deploying.** If env `JOB_SCRAPER_API_KEY` is set, every route requires
a matching `X-API-Key` header (constant-time compare, 401 otherwise); unset = disabled (default).

Scraping is async: `POST /jobs` schedules the work and returns a `job_id`; poll
`GET /jobs/{job_id}/status`, then fetch `GET /jobs/{job_id}/result`.

### `POST /jobs` — schedule a scrape

| Field | Type | Default |
|---|---|---|
| `keywords` | list[str] | required |
| `max_jobs_searched_per_keyword` | int | required |
| `max_jobs_saved` | int (global cap) | required |
| `acceptable_locations` | list[str] | `[]` |
| `anti_keywords` | list[str] | `[]` |
| `max_experience_years` | int or null | `null` |
| `discarded_urls` | list[str] | `[]` |
| `sources` | list[str] or null | `null` = findweb3 + web3career (legacy path) |
| `boards` | list of `"<greenhouse|lever|ashby>:<token>"` | `[]` (no ATS runs); malformed → 422 |
| `known_keys` | list[str] | `[]` |
| `since` | ISO 8601 or null | `null` |
| `work_modes` | subset of remote/hybrid/onsite | `[]` |
| `seniority_exclude` | list[str] | `[]` |
| `excluded_companies` | list[str] | `[]` |
| `match_mode` | `"substring"` or `"word"` | `"substring"` |
| `deadline_seconds` | number > 0 or null | `null` |

Unknown fields are ignored. The frontend (`job_scraper_frontend`) sends only the seven legacy fields
and must keep working unchanged (compat tests in `tests/test_app.py::TestFrontendCompatibility`).
`boards` run per vendor and only when `sources` is omitted or lists the vendor id.

Malformed bodies get `422`; otherwise `202 {"job_id": "<uuid4>"}` — failures surface via polling.
`_run_scrape_job` runs each source in its own try/except: a failing source goes to `errors[]`
(`{source, error}`, short, no stack) and the run continues; status is `error` only if **every**
source failed. After all sources finish, rows are merged, de-duplicated by `canonical_url` /
`dedupe_key` (higher score wins), ranked by score and cut to `max_jobs_saved` globally. With
`deadline_seconds`, sources not started in time or interrupted get `errors[]` "deadline exceeded"
and the run still finishes `done` with partial results.

### `GET /jobs/{job_id}/status` — poll progress

```json
{"status": "running", "stage": "scraping", "current": 7, "total": 20}
```

`status`: `running` | `done` | `error` (with `"error"` message). `stage`: `starting` →
`searching_keywords` → `scraping` → `filtering` → `done`. `404` if unknown/purged.

### `GET /jobs/{job_id}/result` — fetch the finished output

`{"schema_version": "1", "keywords": [...], "jobs": [<normalized row>...], "errors": [...]}` once
`done`. `404` unknown, `409` still running, `500` with the stored message if `status == "error"`.

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

- Each new site-specific scraper lives in `scrapers/`, is registered in `SOURCES`, and emits the normalized row so results from multiple sources stay compatible.
- Never scrape sources whose terms prohibit automation (LinkedIn, Wellfound, Bitcoiner Jobs, LaborX); never bypass anti-bot measures. Never log or commit the web3.career token.
- Site-specific quirks (URL schemes, headless browser needs, rate limiting) are handled inside each scraper module — `app.py` should stay a thin request handler that doesn't need to know about per-site scraping details.
- Be respectful of target sites: check `robots.txt`, add delays between requests, and avoid hammering job detail pages.
