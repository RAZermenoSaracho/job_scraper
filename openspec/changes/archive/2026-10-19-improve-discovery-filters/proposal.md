## Why

razs_jobs polls the scraper repeatedly. Today each run re-fetches detail pages for jobs already seen, cannot select sources, cannot filter by work mode, seniority or company, has no overall deadline, and caps `max_jobs_saved` per site rather than across sites. This change makes repeated, incremental runs cheap and predictable.

## What Changes

All new request fields are optional; absent fields preserve current behaviour.
- `sources`: list of source names to run (`findweb3`, `web3career`, ATS names via `boards`); default all current sources.
- `known_keys`: list of keys already seen (canonical URLs, `source:source_job_id`, or `dedupe_key` values); matching candidates are skipped before any detail fetch and excluded from results.
- `since`: ISO 8601 timestamp; rows with a known `posted_at` older than it are excluded; rows without a date are kept.
- Filters: `work_modes` (subset of remote/hybrid/onsite), `seniority_exclude` (e.g. intern, junior, senior, staff, principal, lead), `excluded_companies`.
- `match_mode` (`substring` default, or `word`): one keyword/anti-keyword matching rule shared by every source (see design). Default preserves today's behaviour exactly.
- Existing required fields `acceptable_locations` and `max_experience_years` become optional (defaults `[]` / `null` = filter off); `keywords`, `max_jobs_searched_per_keyword`, `max_jobs_saved` stay required.
- This change is the single owner of the request-parameter table (names, types, defaults); `boards` is owned by `add-ats-sources`.
- Source catalog: a static registry in `scrapers/__init__.py` and `GET /sources` (no network I/O) listing every known source with `category`, `availability`, `executable`, `requires_config`, `filters`, `hosts`, `ingestion`. `sources` is validated against it: unknown or non-executable ids -> 422 naming them.
- `deadline_seconds`: overall run budget; on expiry the run stops, returns what it has, and records an `errors[]` entry.
- Global ranking: after all sources finish, results are merged, ranked by score and cut to `max_jobs_saved`.

## Non-goals

- LinkedIn/Indeed discovery (terms prohibit automated access). LinkedIn and other manual sources appear in the catalog only (`executable=false`); no adapter, no browser extraction.
- Persistent state in the scraper; history stays in Odoo and arrives via `known_keys`.

## Capabilities

### New Capabilities
- `discovery-filters`: request fields, source catalog (`GET /sources`) and `sources` validation, skip-before-fetch, global ranking, deadline.

### Modified Capabilities
None.

## Impact

- Code: `scrapers/__init__.py` (registry), `app.py` (`GET /sources`, `JobsRequest`, `_build_scrapers` iterating the registry, merge/rank/cut, deadline), `utils/filters.py` (new predicates), both existing scrapers (honour `known_keys` at search time), tests.
- Depends on `normalize-job-results` (keys, `posted_at`, `work_mode`, `errors[]`).
- Compatibility: frontend sends none of the new fields; `max_jobs_saved: N` still returns at most N rows.
