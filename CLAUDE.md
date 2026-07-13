# job_scraper

A FastAPI service that scrapes job boards for roles matching a set of keywords, ranks results by
keyword relevance, and returns structured JSON over HTTP for downstream use in job applications.
No files are written to disk — every run is triggered by an HTTP request and returned in the
response.

## Project structure

```
.
├── keywords.json          # Search/scoring keywords + filter config (locations, anti-keywords)
├── app.py                 # FastAPI app: GET /jobs reads env vars + keywords.json, runs scrapers, returns JSON
├── utils/
│   └── filters.py         # Per-job filter predicates (location, anti-keywords, required experience)
└── scrapers/
    └── fw3_jobscraper.py  # Fw3Scraper class for findweb3.com — filters + ranks candidates internally
```

## Context

This project is part of a personal job search workflow (see sibling folder `job_apps/`, which contains tailored resumes and job descriptions for specific applications, e.g. `job_apps/wintermute_algo_trader/`). The scraper's job is purely discovery/ranking — it does not apply to jobs or fill out application forms.

## keywords.json

A JSON object with three keys:

```json
{
  "keywords": ["python", "defi", "solidity", "blockchain", "smart contracts", "foundry", "quantitative", "linux", "git"],
  "acceptable_locations": ["remote", "mexico", "usa"],
  "anti_keywords": ["clearance required"]
}
```

- `keywords` — skills/technologies/interests to search for and score against. Drives two things:
  1. **Search queries** — each scraper searches per-keyword against the target job board (site-specific, since APIs/URLs differ per source).
  2. **Relevance scoring** — a job's score is the count of *unique* keywords found in its `Job Title` + plain-text `Job Description`. Matched keywords are also recorded as that job's `Tags`.
- `acceptable_locations` — see [utils/filters.py](#utilsfilterspy) below.
- `anti_keywords` — see [utils/filters.py](#utilsfilterspy) below.

`app.py` reads this file fresh on every request (not cached at import time).

## utils/filters.py

Exposes three per-job **predicate** functions — `passes_location(job, acceptable_locations)`,
`passes_anti_keywords(job, anti_keywords)`, `passes_experience(job, max_experience_years)` — each
taking a single job dict (already in the output schema shape) and returning `bool`. Also exposes
`job_text(job)`, which builds `Job Title` + plain-text `Job Description` (HTML tags stripped) —
used both by these predicates and by the scraper's relevance scoring, so filtering and scoring
always look at the same text. `app.py` never imports this module; scrapers call these functions
themselves, inline, right after building each candidate's job dict. All three predicates are no-ops
(always return `True`) when their corresponding config list/value is empty/`None`.

1. **Location** (`passes_location`) — passes if `Job Location` is empty/`None` (unknown location
   isn't disqualifying), or contains (case-insensitive substring) any entry from `acceptable_locations`.
2. **Anti-keywords** (`passes_anti_keywords`) — fails if `job_text(job)` contains any
   `anti_keywords` entry (case-insensitive substring).
3. **Experience** (`passes_experience`) — extracts every "years of experience" mention from
   `job_text(job)` via `EXPERIENCE_PATTERN` (a regex matching `"N+ years"`, `"N-M years"`,
   and plain `"N years"` — the latter also covers `"at least N years"` / `"minimum of N years"` since
   the number still immediately precedes "years"). For a range like `"N-M years"`, only the minimum
   `N` is compared — a job requiring `"3-7 years"` passes if `max_experience_years >= 3`, even though
   4 doesn't reach 7. Fails only if *some* mention's minimum exceeds `max_experience_years`. Jobs with
   no experience mention at all pass.

`max_experience_years` is a per-request value in `app.py` (query param, defaulting to the
`MAX_EXPERIENCE_YEARS` env var) passed into the scraper's constructor.

## scrapers/fw3_jobscraper.py

Exposes a single class `Fw3Scraper`. Key constraints specific to this site:
- The site's URL scheme only supports one keyword per search: `https://findweb3.com/jobs/{keyword}`
- Uses headless Selenium (no public API available)
- Caps the number of postings explored per keyword via `max_jobs_per_keyword` (constructor param)
- `Company Name` and `Job Description` are extracted from the job detail page via best-effort
  XPath selectors (class/data-testid containing "company"/"description"); `Job Description` is
  stored as raw HTML, unsanitized — no plain-text conversion is done in the scraper.
- **Filters inline, then ranks.** `run()` visits every candidate URL found during search (up to
  `max_jobs_per_keyword` per keyword). As soon as a candidate's fields are extracted and mapped to
  the output schema, it's run through `passes_location` → `passes_anti_keywords` →
  `passes_experience` from `utils/filters.py`. Rejected candidates are dropped immediately. A
  candidate that survives all three is scored (count of unique `keywords` matches in its
  `job_text`), and only *after every candidate has been visited* is the surviving pool sorted by
  score (descending) and cut down to the best `top_n_results`. This is deliberate: stopping early
  at the first `top_n_results` passing candidates would return "first found," not "most relevant" —
  a later candidate with a much better score wouldn't get a chance to bump out an earlier, weaker
  one. Drop counts per filter, plus the min/max score among the final selection, are logged at the
  end of `run()`.

### Scraper interface contract

Every scraper class must expose:

```python
class SomeScraper:
    def __init__(self, max_jobs_per_keyword: int, top_n_results: int) -> None: ...
    def run(self, keywords: list) -> list: ...
    # run() returns a clean list of job dicts matching the output schema below —
    # no file I/O, no _ fields
```

`Fw3Scraper` additionally accepts `acceptable_locations`, `anti_keywords`, and
`max_experience_years` kwargs (all optional, default to "no filtering") so it can apply
`utils/filters.py` inline and rank by relevance. Any future scraper that wants the same filtering
support should accept the same kwargs and apply the same predicates + ranking during its own search
loop — this isn't (yet) enforced as part of the strict interface contract above, since not every
scraper will need it.

`app.py` is solely responsible for reading env vars/query params + `keywords.json` and shaping the
HTTP response. It does not filter, score, or otherwise inspect job contents — scrapers are the only
place that happens.

### Adding a new scraper

1. Create `scrapers/{site}_jobscraper.py` following the interface above.
2. Instantiate the class in `app.py`'s `_build_scrapers()` function.
3. No changes to `get_jobs()`'s orchestration loop needed.

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

### `GET /jobs`

Query params (all optional; defaults come from the matching env var, see Environment below):

| Param | Default source | Meaning |
|---|---|---|
| `max_jobs_searched_per_keyword` | `MAX_JOBS_SEARCHED_PER_KEYWORD` env var (20) | Candidate postings explored per keyword |
| `max_jobs_saved` | `MAX_JOBS_SAVED` env var (10) | Top-N results returned (post-filter, post-ranking) |
| `max_experience_years` | `MAX_EXPERIENCE_YEARS` env var (4) | See `passes_experience` above |

Response body — same shape previously written to the output JSON file:

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
- `Tags` is a comma-separated string of the `keywords.json` entries that matched this specific posting.
- `Notes` is always `""` — reserved for manual fill-in downstream.
- `Job Url` points to the listing on the source site (e.g. findweb3.com), not an external application link.

Errors: a `keywords.json` read/parse failure returns `500` with `{"detail": "..."}`; a scraper
raising an exception returns `502` with `{"detail": "Scraper '<name>' failed: ..."}`.

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

- Key dependencies expected in this project: `selenium`, `webdriver-manager` (for automatic ChromeDriver management in headless scraping), `fastapi` and `uvicorn` (for the HTTP API), `python-dotenv`.
- Runtime config lives in a git-ignored `.env` file, loaded via `python-dotenv`: `MAX_JOBS_SEARCHED_PER_KEYWORD`, `MAX_JOBS_SAVED`, `MAX_EXPERIENCE_YEARS` — these are only *defaults*; any of them can be overridden per-request via query params on `GET /jobs`. (`OUTPUT_DIR` is no longer used — it was for file-output paths, which no longer exist.)

## Conventions

- Each new site-specific scraper lives in `scrapers/` (e.g. future additions like `otta_jobscraper.py`, `cryptojobs_jobscraper.py`) and should follow the same output schema so results from multiple sources stay compatible.
- Site-specific quirks (URL schemes, headless browser needs, rate limiting) are handled inside each scraper module — `app.py` should stay a thin request handler that doesn't need to know about per-site scraping details.
- Be respectful of target sites: check `robots.txt`, add delays between requests, and avoid hammering job detail pages.
