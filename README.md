# job_scraper

A FastAPI service that scrapes job boards for postings matching a set of keywords, ranks each
posting by how many *unique* keywords it mentions, and returns the top-N results as a JSON HTTP
response. Currently supports [findweb3.com](https://findweb3.com), built so additional sources can
be added as independent scraper modules without touching the request-handling logic. No files are
written to disk — every run happens on-demand via an HTTP request.

## Project structure

```
.
├── keywords.json              # Search/scoring keywords + filter config (locations, anti-keywords)
├── app.py                     # FastAPI app: GET /jobs reads env vars + keywords.json, runs scrapers, returns JSON
├── requirements.txt           # Pinned dependencies for the `job_scraper` conda env
├── utils/
│   ├── __init__.py            # Exposes the filter predicates for import as `from utils import ...`
│   └── filters.py             # Per-job filter predicates: location, anti-keywords, required experience
└── scrapers/
    ├── __init__.py            # Exposes Fw3Scraper for import as `from scrapers import Fw3Scraper`
    └── fw3_jobscraper.py       # Fw3Scraper: headless-Selenium scraper for findweb3.com; filters + ranks candidates internally
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

## Configuration

### `keywords.json`

A JSON object with three keys:

```json
{
  "keywords": ["python", "solidity", "foundry", "blockchain", "smart contracts", "defi", "evm", "quantitative", "linux", "git"],
  "acceptable_locations": ["remote", "mexico", "usa"],
  "anti_keywords": ["clearance required"]
}
```

- `keywords` drives two things:
  1. **Search** — every scraper queries the target site once per keyword.
  2. **Scoring & tagging** — a posting's score is the count of *unique* keywords found in its
     `Job Title` + plain-text `Job Description` (case-insensitive substring match). Matched
     keywords are recorded as that job's `Tags`.
- `acceptable_locations` and `anti_keywords` feed the filters — see [Filtering](#filtering) below.
  Leave either as `[]` to disable that filter.

`app.py` reads this file fresh on every request — edits take effect on the next call, no restart
needed.

### Request parameters

`GET /jobs` accepts three optional query params, each falling back to an env var if omitted:

| Query param | Env var fallback (default) | Meaning |
|---|---|---|
| `max_jobs_searched_per_keyword` | `MAX_JOBS_SEARCHED_PER_KEYWORD` (20) | Candidate postings explored per keyword search |
| `max_jobs_saved` | `MAX_JOBS_SAVED` (10) | How many top-ranked postings to return |
| `max_experience_years` | `MAX_EXPERIENCE_YEARS` (4) | See [Filtering](#filtering) below |

Env var defaults are read from a git-ignored `.env` file (via `python-dotenv`) at process startup.

## Filtering

Filtering happens **inside each scraper**, inline while it visits candidates — not in `app.py`
after the fact. As `Fw3Scraper` visits each candidate job URL, it extracts the job's fields, maps
them to the output schema, and immediately runs the result through three filter predicates from
[utils/filters.py](utils/filters.py):

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

### Ranking (evaluate all, then pick the best N)

The scraper does **not** stop as soon as it has `max_jobs_saved` passing candidates. It visits
*every* candidate found during search (up to `max_jobs_searched_per_keyword` per keyword), and each
one that survives the three filters gets a relevance score — the count of unique `keywords` matched
in its title + description. Only after all candidates have been visited does it sort the surviving
pool by score (descending) and return the best `max_jobs_saved`. This matters because stopping
early at the first N passing candidates would return "first found," not "most relevant" — a
higher-scoring candidate found later wouldn't get a chance to bump out a weaker, earlier one.

Each scraper logs drop counts per filter, plus the score range of its final selection, e.g.:

```
13:32:50 [INFO] Filtered 40 candidate(s): 12 passed (dropped — location: 20, anti_keywords: 3, experience: 5)
13:32:50 [INFO] Selected top 5 of 12 passing candidate(s) — score min: 3, max: 8
```

## Usage

Run the API locally:

```bash
conda activate job_scraper
uvicorn app:app --reload
```

Call it:

```bash
curl "http://127.0.0.1:8000/jobs?max_jobs_searched_per_keyword=10&max_jobs_saved=5&max_experience_years=4"
```

Interactive API docs (Swagger UI) are available at `http://127.0.0.1:8000/docs` once the server is
running.

## Response format

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
- `Tags` is a comma-separated string of the keywords (from `keywords.json`) that matched this specific posting.
- `Notes` is always `""` — reserved for manual fill-in downstream.
- `Company Name`, `Job Location`, and `Compensation` are `null`/empty when the scraper couldn't find a matching element on the page.

### Errors

- `keywords.json` missing or invalid → `500` with `{"detail": "Failed to read keywords.json: ..."}`.
- A scraper raises an exception mid-run → `502` with `{"detail": "Scraper '<name>' failed: ..."}`.

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
   def _build_scrapers(config, max_jobs_searched_per_keyword, max_jobs_saved, max_experience_years):
       return [
           ("findweb3.com", Fw3Scraper(max_jobs_per_keyword=max_jobs_searched_per_keyword, top_n_results=max_jobs_saved, ...)),
           ("otta.com", OttaScraper(max_jobs_per_keyword=20, top_n_results=10)),
       ]
   ```

If the new scraper should support the same location/anti-keyword/experience filtering and
best-of-N ranking, accept `acceptable_locations`, `anti_keywords`, and `max_experience_years` in its
`__init__`, apply the predicates from `utils/filters.py` inline in its own search loop, and rank all
surviving candidates by score before cutting to `top_n_results` — the same way `Fw3Scraper` does.

No changes to `get_jobs()`'s orchestration loop in `app.py` are needed — it just iterates
`_build_scrapers(...)` and concatenates results.

## Notes / known limitations

- Scraping depends on findweb3.com's current HTML structure — if the site's markup changes, the
  class/data-testid-based selectors in `scrapers/fw3_jobscraper.py` (used for `Company Name` and
  `Job Description`) may break and start returning `null`.
- `robots.txt` is respected and randomized delays (1–2.5s) are added between page loads to avoid
  hammering the site.
- `Job Url` points to the findweb3.com listing page, not an external application link — this
  scraper does not follow the "Apply" button.
- Each request runs Selenium synchronously in the request handler — a single `GET /jobs` call can
  take a while (one page load per keyword, plus one per surviving/candidate job detail page). There
  is no background job queue; the HTTP response only returns once scraping finishes.
