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
import pandas as pd
import os
from dotenv import load_dotenv

from filters import filter_jobs
from scrapers import Fw3Scraper

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

ROOT_DIR = Path(__file__).resolve().parent
KEYWORDS_FILE = ROOT_DIR / "keywords.json"

MAX_JOBS_SEARCHED_PER_KEYWORD = int(os.environ.get("MAX_JOBS_SEARCHED_PER_KEYWORD", 20))
MAX_JOBS_SAVED = int(os.environ.get("MAX_JOBS_SAVED", 10))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", ROOT_DIR))
MAX_EXPERIENCE_YEARS = int(os.environ.get("MAX_EXPERIENCE_YEARS", 4))

OUTPUT_COLUMNS = [
    "Job Title",
    "Company Name",
    "Job Description",
    "Job Location",
    "Compensation",
    "Job Url",
    "Tags",
    "Notes",
]

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
        path = OUTPUT_DIR / f"{today}_results_{n:03d}.json"
        if not path.exists():
            return path
        n += 1


def main() -> None:
    config = json.loads(KEYWORDS_FILE.read_text())
    keywords = config["keywords"]
    print(f"Keywords loaded: {len(keywords)}")

    all_jobs: list = []

    for site_name, scraper in SCRAPERS:
        print(f"\n── {site_name} ──────────────────────────────")
        jobs = scraper.run(keywords)
        print(f"  Jobs returned : {len(jobs)}")
        all_jobs.extend(jobs)

    all_jobs, dropped = filter_jobs(all_jobs, config, MAX_EXPERIENCE_YEARS)
    print(f"\nDescartados por ubicación: {dropped['location']}")
    print(f"Descartados por anti-keywords: {dropped['anti_keywords']}")
    print(f"Descartados por experiencia: {dropped['experience']}")

    out_path = _output_path()
    out_path.write_text(
        json.dumps({"keywords": keywords, "jobs": all_jobs}, indent=2, ensure_ascii=False)
    )

    xlsx_path = out_path.with_suffix(".xlsx")
    df = pd.DataFrame(all_jobs, columns=OUTPUT_COLUMNS)
    df.to_excel(xlsx_path, index=False)

    print(f"\n  Jobs saved    : {len(all_jobs)}")
    print(f"  Output file   : {out_path.name}")
    print(f"  Excel file    : {xlsx_path.name}")
    print("\nDone.")


if __name__ == "__main__":
    main()
