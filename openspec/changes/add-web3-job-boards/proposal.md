## Why

Web3 boards are the owner's primary discovery targets, but today only two are served, both by fragile HTML scraping (findweb3 via Selenium with hashed CSS classes and anti-detection flags; web3.career via regex over raw HTML). Research (2026-10-19) found cleaner, permitted routes: web3.career has an official API, and Hashtag Web3 publishes a JSON feed for personal use. Other boards (LinkedIn, Wellfound, Bitcoiner Jobs, LaborX) prohibit automation and Cyfrin Jobs has closed; they must be recorded as non-executable so nothing automates them by accident.

## What Changes

- New `web3career` API adapter: `GET https://web3.career/api/v1` with the owner's own token (env `WEB3CAREER_API_TOKEN`). Executable only when the token is set; otherwise the catalog shows availability `planned` with a reason. `apply_url` is passed through unmodified (API terms). The legacy HTML scraper stays only for the existing frontend path until the API adapter is live, then is retired in this change behind a flag.
- New `hashtagweb3` feed adapter: JSON `https://hashtagweb3.com/data/jobs-runtime.json`, personal non-commercial use, at most one fetch per run, honours `Retry-After`/backoff, caches by ETag/Last-Modified. Its `link` is usually a Greenhouse/Lever/Ashby URL, so `canonical_url` from `link` dedupes against ATS rows.
- `findweb3` hardening: remove anti-detection Chrome flags, avoid runtime chromedriver download where possible, resilient selectors, retries and 429 handling.
- Catalog-only entries (no adapter, `executable=false`) for `linkedin`, `wellfound`, `bitcoinerjobs`, `laborx` (manual) and `cyfrin` (defunct).
- Every adapter emits the normalized row, stable `source_job_id`, canonical URLs, pagination where the source supports it, per-source rate limits/backoff and error isolation.

## Non-goals

- LinkedIn: catalog entry only. No adapter, no browser extraction, no guest endpoints, no alert-email intake here (deferred; ingestion is the user pasting a URL into Odoo).
- No scraping of Wellfound, Bitcoiner Jobs or LaborX (ToS prohibit automation) unless the owner obtains written permission or a feed; that would be a separate change.
- No bypass of anti-bot measures, no calls to findweb3's `/api/` (robots-disallowed).

## Capabilities

### New Capabilities
- `web3-job-boards`: web3career API adapter, hashtagweb3 feed adapter, findweb3 hardening, catalog entries for manual/defunct sources.

### Modified Capabilities
None.

## Impact

- Code: new `scrapers/web3career_api.py`, `scrapers/hashtagweb3_feed.py`; edits to `scrapers/fw3_jobscraper.py`, `scrapers/web3career_jobscraper.py` (legacy, flagged), `scrapers/__init__.py` registry entries; fixtures under `tests/fixtures/web3/`.
- Depends on `normalize-job-results` (row shape, canonical URLs, `errors[]`) and `improve-discovery-filters` (registry, `GET /sources`, `sources` validation).
- New env var `WEB3CAREER_API_TOKEN` (never logged, never in fixtures).
