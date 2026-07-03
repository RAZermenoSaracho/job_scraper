"""
Job scraper entry point.

To add a scraper for a new site:
  1. Implement a class with a .run(keywords: list) -> list method in scrapers/
  2. Instantiate it with the desired config and add it to SCRAPERS below
main() handles all file I/O: reading keywords.json and writing results JSON.
"""

import json
import logging
from datetime import date
from pathlib import Path

from scrapers import Fw3Scraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

ROOT_DIR = Path(__file__).resolve().parent
KEYWORDS_FILE = ROOT_DIR / "keywords.json"

MAX_JOBS_SEARCHED_PER_KEYWORD = 10
MAX_JOBS_SAVED = 5

# Each entry is (display_name, scraper_instance).
# Scrapers run in order; their results are concatenated into one output file.
SCRAPERS = [
    ("findweb3.com", Fw3Scraper(max_jobs_per_keyword=MAX_JOBS_SEARCHED_PER_KEYWORD, top_n_results=MAX_JOBS_SAVED)),
    # ("otta.com", OttaScraper(max_jobs_per_keyword=20, top_n_results=10)),
]


def _output_path() -> Path:
    """Return ROOT/{YYYY}_{MM}_{DD}_results_{NNN}.json, incrementing NNN if needed."""
    today = date.today().strftime("%Y_%m_%d")
    n = 1
    while True:
        path = ROOT_DIR / f"{today}_results_{n:03d}.json"
        if not path.exists():
            return path
        n += 1


def main() -> None:
    keywords = json.loads(KEYWORDS_FILE.read_text())
    print(f"Keywords loaded: {len(keywords)}")

    all_jobs: list = []

    for site_name, scraper in SCRAPERS:
        print(f"\n── {site_name} ──────────────────────────────")
        jobs = scraper.run(keywords)
        print(f"  Jobs returned : {len(jobs)}")
        all_jobs.extend(jobs)

    out_path = _output_path()
    out_path.write_text(
        json.dumps({"keywords": keywords, "jobs": all_jobs}, indent=2, ensure_ascii=False)
    )

    print(f"\n  Jobs saved    : {len(all_jobs)}")
    print(f"  Output file   : {out_path.name}")
    print("\nDone.")


if __name__ == "__main__":
    main()
