# job_scraper

Python app to scrape job boards for roles matching a set of keywords, rank results by keyword relevance, and save structured JSON + XLSX output for downstream use in job applications.

## Project structure

```
.
├── keywords.json          # Search/scoring keywords + filter config (locations, anti-keywords)
├── main.py                # Entry point: reads keywords.json, runs scrapers, filters, writes output JSON + XLSX
├── filters.py             # Post-scrape filtering: location, anti-keywords, required experience
└── scrapers/
    └── fw3_jobscraper.py  # Fw3Scraper class for findweb3.com
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
  2. **Relevance scoring** — for each job posting, the keywords that appear anywhere in its full text (title + description + requirements) are recorded as that job's `Tags`. Jobs are ranked by *unique* keyword match count, not by frequency.
- `acceptable_locations` — see [filters.py](#filterspy) below.
- `anti_keywords` — see [filters.py](#filterspy) below.

## filters.py

Applied in `main.py` after all scrapers run, before the output files are written (so discarded
jobs never cost JSON/XLSX write time). Exposes `filter_jobs(jobs, config, max_experience_years)`,
which runs three filters in sequence and returns `(surviving_jobs, drop_counts)`:

1. **Location** (`filter_by_location`) — a job is kept if its `Job Location` is empty/`None` (unknown
   location isn't disqualifying), or if it contains (case-insensitive substring) any entry from
   `acceptable_locations`. Otherwise dropped. If `acceptable_locations` is empty, this filter is a no-op.
2. **Anti-keywords** (`filter_by_anti_keywords`) — a job is dropped if `Job Title` + the plain-text
   version of `Job Description` (HTML tags stripped for matching only — the stored field stays raw
   HTML) contains any `anti_keywords` entry (case-insensitive substring). No-op if the list is empty.
3. **Experience** (`filter_by_experience`) — extracts every "years of experience" mention from the
   same title+description text via `EXPERIENCE_PATTERN` (a regex matching `"N+ years"`, `"N-M years"`,
   and plain `"N years"` — the latter also covers `"at least N years"` / `"minimum of N years"` since
   the number still immediately precedes "years"). For a range like `"N-M years"`, only the minimum
   `N` is compared — a job requiring `"3-7 years"` is kept if `MAX_EXPERIENCE_YEARS >= 3`, even though
   4 doesn't reach 7. A job is dropped only if *some* mention's minimum exceeds `MAX_EXPERIENCE_YEARS`.
   Jobs with no experience mention at all are kept.

`MAX_EXPERIENCE_YEARS` is read from the environment in `main.py` (default `4`).

## scrapers/fw3_jobscraper.py

Exposes a single class `Fw3Scraper`. Key constraints specific to this site:
- The site's URL scheme only supports one keyword per search: `https://findweb3.com/jobs/{keyword}`
- Uses headless Selenium (no public API available)
- Caps the number of postings explored per keyword via `max_jobs_per_keyword` (constructor param)
- `Company Name` and `Job Description` are extracted from the job detail page via best-effort
  XPath selectors (class/data-testid containing "company"/"description"); `Job Description` is
  stored as raw HTML, unsanitized — no plain-text conversion is done in the scraper.

### Scraper interface contract

Every scraper class must expose:

```python
class SomeScraper:
    def __init__(self, max_jobs_per_keyword: int, top_n_results: int) -> None: ...
    def run(self, keywords: list) -> list: ...
    # run() returns a clean list of job dicts matching the output schema below —
    # no file I/O, no _ fields
```

`main.py` is solely responsible for reading `keywords.json` and writing the output JSON + XLSX.
Scrapers are pure data producers.

### Adding a new scraper

1. Create `scrapers/{site}_jobscraper.py` following the interface above.
2. Instantiate the class in `main.py`'s `SCRAPERS` list.
3. No changes to the orchestration loop needed.

**Multi-scraper top-N note:** each scraper applies its own `top_n_results` cut internally before
returning. `main.py` concatenates all results as-is. If a global re-rank across sites is ever
needed, sort `all_jobs` by a shared score field in `main.py` before writing the file.

## Output format

Each run writes two files sharing the same base name, `{YYYY}_{MM}_{DD}_results_{NNN}`
(sequential numbering per day, e.g. `2026_07_03_results_001`):

- `{base}.json` — full output, wrapped with the `keywords` list used for the run
- `{base}.xlsx` — same job rows, one sheet, no `keywords` wrapper (columns match the job schema)

JSON shape:

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

This is post-filter output — jobs dropped by `filters.py` (bad location, anti-keyword match, or
required experience above `MAX_EXPERIENCE_YEARS`) never appear here. `main.py` prints how many
were dropped by each filter on every run.

Field notes:
- `Job Description` is the raw HTML extracted from the site — not sanitized or converted to plain text.
- `Tags` is a comma-separated string of the `keywords.json` entries that matched this specific posting.
- `Notes` is always `""` on output — reserved for manual fill-in after scraping.
- `Job Url` points to the listing on the source site (e.g. findweb3.com), not an external application link.

Only the top N jobs by keyword match score are kept (default N=10, configurable).

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

- Key dependencies expected in this project: `selenium`, `webdriver-manager` (for automatic ChromeDriver management in headless scraping), `pandas` and `openpyxl` (for XLSX export), `python-dotenv`.
- Runtime config lives in a git-ignored `.env` file, loaded via `python-dotenv`: `MAX_JOBS_SEARCHED_PER_KEYWORD`, `MAX_JOBS_SAVED`, `OUTPUT_DIR`, `MAX_EXPERIENCE_YEARS` (see [filters.py](#filterspy)).

## Conventions

- Each new site-specific scraper lives in `scrapers/` (e.g. future additions like `otta_jobscraper.py`, `cryptojobs_jobscraper.py`) and should follow the same output schema so results from multiple sources stay compatible.
- Site-specific quirks (URL schemes, headless browser needs, rate limiting) are handled inside each scraper module — `main.py` should stay a thin orchestrator that doesn't need to know about per-site scraping details.
- Be respectful of target sites: check `robots.txt`, add delays between requests, and avoid hammering job detail pages.