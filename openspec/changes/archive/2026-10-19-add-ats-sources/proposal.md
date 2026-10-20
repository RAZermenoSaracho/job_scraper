## Why

Employer ATS boards expose public, documented JSON APIs that give clean, dated, stable-id postings with no browser. They are the best discovery source for razs_jobs and the main target for browser-based applying (account-less Greenhouse, Lever, Ashby). Selenium scraping of aggregators is slow and brittle by comparison.

## What Changes

- New scraper(s) fetching Greenhouse (`boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`), Lever (`api.lever.co/v0/postings/{company}?mode=json`) and Ashby (`api.ashbyhq.com/posting-api/job-board/{org}`) with plain `requests`; no Selenium.
- New optional request field `boards`: list of `"ats:token"` strings, e.g. `["greenhouse:stripe","lever:netflix","ashby:ramp"]`. Absent/empty means no ATS source runs, so existing requests are unchanged.
- Rows use the `normalize-job-results` schema (`source` = `greenhouse|lever|ashby`, `ats_type` set, `apply_url` = the posting's apply link, `posted_at` from vendor dates, `source_job_id` = vendor id).
- Adapters register in the `SOURCES` registry (`improve-discovery-filters`) with `category: ats`, `requires_config: boards`; the catalog spec itself is not duplicated here.
- Per-board timeout, bounded retries with backoff honoring `Retry-After`, a descriptive User-Agent, and failures recorded per board in `errors[]`.
- Fixture-based tests only; no live network in tests.

## Non-goals

- Submitting applications through vendor POST APIs (needs employer keys; applying is browser-based in razs_jobs).
- Discovering board tokens by guessing or crawling; the owner supplies them.
- LinkedIn and Indeed (terms prohibit automated access). Workday, SmartRecruiters: later.

## Capabilities

### New Capabilities
- `ats-sources`: board spec parsing, three ATS fetchers, mapping, politeness.

### Modified Capabilities
None.

## Impact

- Code: new `scrapers/ats_scraper.py` (mapping + fetch), `app.py` (`boards` field, wiring), fixtures under `tests/fixtures/ats/`.
- Depends on `normalize-job-results` (row shape, `errors[]`, canonical URLs). No new dependencies (`requests` already used).
- Terms: all three endpoints are documented public read APIs for job boards; keep polling modest (see design).
