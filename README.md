# job_scraper

Scrapes job boards for postings matching a set of keywords, ranks each posting by how many
*unique* keywords it mentions, and saves the top-N results per site to a JSON file. Currently
supports [findweb3.com](https://findweb3.com), built so additional sources can be added as
independent scraper modules without touching the orchestration logic.

## Project structure

```
.
├── keywords.json              # Flat JSON array of keywords used for search + scoring
├── main.py                    # Entry point: reads keywords, runs scrapers, writes output JSON
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

A flat JSON array of strings — skills, technologies, or interests to search and score against:

```json
["python", "solidity", "foundry", "blockchain", "smart contracts", "defi", "evm", "quantitative", "linux", "git"]
```

Each keyword drives two things:
1. **Search** — every scraper queries the target site once per keyword.
2. **Scoring** — a posting's score is the count of *unique* keywords found anywhere in its
   text (case-insensitive substring match), not keyword frequency.

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

## Usage

```bash
conda activate job_scraper
python main.py
```

Expected console output:

```
Keywords loaded: 10

── findweb3.com ──────────────────────────────
  Jobs returned : 5

  Jobs saved    : 5
  Output file   : 2026_07_03_results_001.json

Done.
```

## Output format

Files are written to the project root as `{YYYY}_{MM}_{DD}_results_{NNN}.json`, where `NNN` is a
sequential counter starting at `001` and incrementing for each run on the same day (existing
files are never overwritten).

Example:

```json
{
  "keywords": ["python", "solidity", "foundry", "blockchain", "smart contracts", "defi", "evm", "quantitative", "linux", "git"],
  "jobs": [
    {
      "name": "Senior Solidity Engineer",
      "url": "https://findweb3.com/job/rec449xAzZN4Rw8lO",
      "location": null,
      "type": "Contract or full time employment opportunities including all local statutory benefits",
      "compensation": "Base salary paid in your choice of crypto (USDC) or local fiat currency",
      "application_url": "https://jobs.lever.co/maple-finance/509d69ac-1513-4a2e-b660-d9f843c5182b?lever-origin=applied&lever-source%5B%5D=findweb3.com"
    }
  ]
}
```

`jobs` is the concatenation of every scraper's results, each already cut down to its own
`top_n_results`. Fields (`location`, `type`, `compensation`) are `null` when the scraper couldn't
find a matching element on the page.

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
  selectors in `scrapers/fw3_jobscraper.py` (e.g. `div.JobPage_jobApplyCard__tFzYs a.mediumButton`)
  may break and start returning `null`/fallback values.
- `robots.txt` is respected and randomized delays (1–2.5s) are added between page loads to avoid
  hammering the site.
- `application_url` is extracted from the job page's "Apply" button to capture the real external
  application link. If that button/link can't be found, it falls back to the findweb3.com listing
  URL itself.
