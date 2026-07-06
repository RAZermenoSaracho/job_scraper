# job_scraper

Scrapes job boards for postings matching a set of keywords, ranks each posting by how many
*unique* keywords it mentions, and saves the top-N results per site to JSON and XLSX files.
Currently supports [findweb3.com](https://findweb3.com), built so additional sources can be
added as independent scraper modules without touching the orchestration logic.

## Project structure

```
.
├── keywords.json              # Search/scoring keywords + filter config (locations, anti-keywords)
├── main.py                    # Entry point: reads keywords.json, runs scrapers, filters, writes output JSON + XLSX
├── filters.py                 # Post-scrape filtering: location, anti-keywords, required experience
├── requirements.txt           # Pinned dependencies for the `job_scraper` conda env
└── scrapers/
    ├── __init__.py            # Exposes Fw3Scraper for import as `from scrapers import Fw3Scraper`
    └── fw3_jobscraper.py       # Fw3Scraper: headless-Selenium scraper for findweb3.com
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
  2. **Scoring & tagging** — for each posting, the keywords found anywhere in its text
     (case-insensitive substring match) become that job's `Tags`. Postings are ranked by
     *unique* keyword match count, not frequency.
- `acceptable_locations` and `anti_keywords` feed the post-scrape filters — see
  [Filtering](#filtering) below. Leave either as `[]` to disable that filter.

### Per-scraper parameters

Scrapers are instantiated in the `SCRAPERS` list in [main.py](main.py):

```python
MAX_JOBS_SEARCHED_PER_KEYWORD = 10
MAX_JOBS_SAVED = 5

SCRAPERS = [
    ("findweb3.com", Fw3Scraper(max_jobs_per_keyword=MAX_JOBS_SEARCHED_PER_KEYWORD, top_n_results=MAX_JOBS_SAVED)),
]
```

- `max_jobs_per_keyword` — max postings explored per keyword search (limits scraping volume/time).
- `top_n_results` — how many top-scoring postings that scraper keeps in its final output.

To change these, edit the constants or the constructor arguments directly in `main.py`.

### Filtering

After all scrapers run, `main.py` calls `filter_jobs()` from [filters.py](filters.py) to drop
jobs before writing any output. Three filters run in sequence:

1. **Location** — keeps a job if `Job Location` is empty/unknown, or contains (case-insensitive
   substring) any entry from `acceptable_locations`; drops it otherwise. Disabled (no-op) if
   `acceptable_locations` is `[]`.
2. **Anti-keywords** — drops a job if `Job Title` or `Job Description` (HTML stripped, for
   matching only — the stored field stays raw HTML) contains any `anti_keywords` entry.
   Disabled if the list is `[]`.
3. **Experience** — reads `MAX_EXPERIENCE_YEARS` from `.env` (default `4`) and scans the title +
   description for "years of experience" mentions (`"N+ years"`, `"N-M years"`, plain
   `"N years"` — which also covers `"at least N years"` / `"minimum of N years"`). For a range,
   only the **minimum** is compared: a job asking for `"3-7 years"` is kept if your max is `4`,
   since you clear the 3-year floor. A job is dropped only if some mention's minimum exceeds
   `MAX_EXPERIENCE_YEARS`; jobs with no experience mention are kept.

Console output reports how many jobs each filter dropped, e.g.:

```
Descartados por ubicación: 3
Descartados por anti-keywords: 1
Descartados por experiencia: 2
```

## Usage

```bash
conda activate job_scraper
python main.py
```

Expected console output:

```
Keywords loaded: 10

── findweb3.com ──────────────────────────────
  Jobs returned : 8

Descartados por ubicación: 2
Descartados por anti-keywords: 0
Descartados por experiencia: 1

  Jobs saved    : 5
  Output file   : 2026_07_06_results_001.json
  Excel file    : 2026_07_06_results_001.xlsx

Done.
```

## Output format

Each run writes two files sharing the same base name,
`{YYYY}_{MM}_{DD}_results_{NNN}.{json,xlsx}`, where `NNN` is a sequential counter starting at
`001` and incrementing for each run on the same day (existing files are never overwritten).

- `.json` — `{"keywords": [...], "jobs": [...]}`, the full run output.
- `.xlsx` — the same job rows as a single-sheet spreadsheet, one row per job, columns matching
  the schema below (no `keywords` wrapper).

JSON example:

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

`jobs` is the concatenation of every scraper's results, each already cut down to its own
`top_n_results` and then run through the filters described above. Field notes:
- `Job Description` is raw HTML as extracted from the page — not sanitized or converted to plain text (stored as-is in both the JSON and the XLSX cell).
- `Tags` is a comma-separated string of the keywords (from `keywords.json`) that matched this specific posting.
- `Notes` is always `""` — reserved for manual fill-in after scraping.
- `Company Name`, `Job Location`, and `Compensation` are `null`/empty when the scraper couldn't find a matching element on the page.

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
3. Import and add it to the `SCRAPERS` list in `main.py`:

   ```python
   SCRAPERS = [
       ("findweb3.com", Fw3Scraper(max_jobs_per_keyword=10, top_n_results=5)),
       ("otta.com", OttaScraper(max_jobs_per_keyword=20, top_n_results=10)),
   ]
   ```

No changes to the orchestration loop in `main.py` are needed — it just iterates `SCRAPERS` and
concatenates results.

## Notes / known limitations

- Scraping depends on findweb3.com's current HTML structure — if the site's markup changes, the
  class/data-testid-based selectors in `scrapers/fw3_jobscraper.py` (used for `Company Name` and
  `Job Description`) may break and start returning `null`.
- `robots.txt` is respected and randomized delays (1–2.5s) are added between page loads to avoid
  hammering the site.
- `Job Url` points to the findweb3.com listing page, not an external application link — this
  scraper no longer follows the "Apply" button.
