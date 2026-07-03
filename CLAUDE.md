# job_scraper

Python app to scrape job boards for roles matching a set of keywords, rank results by keyword relevance, and save structured JSON output for downstream use in job applications.

## Project structure

```
.
├── keywords.json          # List of keywords used to search and score job postings
├── main.py                # Entry point: reads keywords, runs scrapers, writes output JSON
└── scrapers/
    └── fw3_jobscraper.py  # Fw3Scraper class for findweb3.com
```

## Context

This project is part of a personal job search workflow (see sibling folder `job_apps/`, which contains tailored resumes and job descriptions for specific applications, e.g. `job_apps/wintermute_algo_trader/`). The scraper's job is purely discovery/ranking — it does not apply to jobs or fill out application forms.

## keywords.json

A flat JSON array of strings representing skills/technologies/interests to search for and score against, e.g.:

```json
["python", "defi", "solidity", "blockchain", "smart contracts", "foundry", "quantitative", "linux", "git"]
```

These keywords drive two things:
1. **Search queries** — each scraper searches per-keyword against the target job board (site-specific, since APIs/URLs differ per source).
2. **Relevance scoring** — job postings are scored by how many *unique* keywords appear in their full text (title + description + requirements), not by frequency.

## scrapers/fw3_jobscraper.py

Exposes a single class `Fw3Scraper`. Key constraints specific to this site:
- The site's URL scheme only supports one keyword per search: `https://findweb3.com/jobs/{keyword}`
- Uses headless Selenium (no public API available)
- Caps the number of postings explored per keyword via `max_jobs_per_keyword` (constructor param)
- Extracts the real external `application_url` from `div.JobPage_jobApplyCard__tFzYs a.mediumButton`

### Scraper interface contract

Every scraper class must expose:

```python
class SomeScraper:
    def __init__(self, max_jobs_per_keyword: int, top_n_results: int) -> None: ...
    def run(self, keywords: list) -> list: ...
    # run() returns a clean list of job dicts — no file I/O, no _ fields
```

`main.py` is solely responsible for reading `keywords.json` and writing the output JSON.
Scrapers are pure data producers.

### Adding a new scraper

1. Create `scrapers/{site}_jobscraper.py` following the interface above.
2. Instantiate the class in `main.py`'s `SCRAPERS` list.
3. No changes to the orchestration loop needed.

**Multi-scraper top-N note:** each scraper applies its own `top_n_results` cut internally before
returning. `main.py` concatenates all results as-is. If a global re-rank across sites is ever
needed, sort `all_jobs` by a shared score field in `main.py` before writing the file.

## Output format

Results are saved as JSON files named `{YYYY}_{MM}_{DD}_results_{NNN}.json` (sequential numbering per day, e.g. `2026_07_03_results_001.json`), with this shape:

```json
{
  "keywords": ["python", "defi", "..."],
  "jobs": [
    {
      "name": "DeFi Algorithmic Trading Internship",
      "url": "https://findweb3.com/job/recoUnkn8Eb9kNPcK",
      "location": "Fully Remote",
      "type": "Internship",
      "compensation": null,
      "application_url": "https://www.wintermute.com/company/opportunities/..."
    }
  ]
}
```

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

- Key dependencies expected in this project: `selenium`, `webdriver-manager` (for automatic ChromeDriver management in headless scraping).

## Conventions

- Each new site-specific scraper lives in `scrapers/` (e.g. future additions like `otta_jobscraper.py`, `cryptojobs_jobscraper.py`) and should follow the same output schema so results from multiple sources stay compatible.
- Site-specific quirks (URL schemes, headless browser needs, rate limiting) are handled inside each scraper module — `main.py` should stay a thin orchestrator that doesn't need to know about per-site scraping details.
- Be respectful of target sites: check `robots.txt`, add delays between requests, and avoid hammering job detail pages.