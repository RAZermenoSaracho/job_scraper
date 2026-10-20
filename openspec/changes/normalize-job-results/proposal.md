## Why

Scraped rows have no stable id, date, apply URL, work mode or source field, and one failing scraper aborts the whole run (`app.py` marks the job `error` and discards other sources' results). The Odoo app `razs_jobs` (change `add-job-discovery`) needs a deterministic, deduplicable row shape. This change is the single authoritative definition of that contract; consumers reference it by name and never re-specify it.

## What Changes

- Every job row gains additive normalized fields next to the unchanged legacy keys: `source`, `source_job_id`, `canonical_url`, `apply_url`, `company`, `title`, `location`, `work_mode`, `posted_at`, `description_html`, `description_text`, `salary`, `ats_type`, `tags` (list), `dedupe_key`.
- Result envelope gains `schema_version` (`"1"`) and `errors[]` (`{source, error}`); legacy `keywords` and `jobs` stay.
- Per-source error isolation: a failing source is recorded in `errors[]` and the run continues; the run only becomes `error` if every source failed.
- New pure module `utils/normalize.py` (canonical URL, stable id, dedupe key, work-mode inference, HTML to text) with pytest.
- Stable `source_job_id` for the two existing sources (findweb3, web3.career), and canonical URL forms for linkedin, wellfound, web3career, findweb3 and hashtagweb3 so Odoo's manual URL paste dedupes identically (URL rules only; LinkedIn/Wellfound are never fetched).
- Optional `X-API-Key` check, off unless env `JOB_SCRAPER_API_KEY` is set (see design).
- Request body unchanged. Frontend (`job_scraper_frontend`) keeps working with zero edits.

## Non-goals

- LinkedIn: no scraping or automation, ever (User Agreement section 8.2 prohibits scraping, bots and automated access). Out of scope for all job_scraper changes.
- No persistence in the scraper; Odoo owns history.
- No new endpoints, no new dependencies.

## Capabilities

### New Capabilities
- `job-result-schema`: normalized row, envelope, canonical URL/id/dedupe rules, error isolation, legacy compatibility.

### Modified Capabilities
None (no main specs exist yet).

## Impact

- Code: `app.py` (envelope, error isolation, optional key check), `scrapers/fw3_jobscraper.py`, `scrapers/web3career_jobscraper.py` (emit normalized fields), new `utils/normalize.py`, tests.
- Docs: CLAUDE.md/AGENTS.md/README are stale (fw3-only, port 8000); update to port 8420 and both sources.
- Consumers: razs_jobs `add-job-discovery`; `add-ats-sources` and `improve-discovery-filters` depend on this change.
