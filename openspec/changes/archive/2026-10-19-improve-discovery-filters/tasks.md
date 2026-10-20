## 1. Request model
- [x] 1.1 Add optional fields (incl. `match_mode` Literal; make `acceptable_locations`, `max_experience_years` optional) to `JobsRequest` with defaults and 422 on unknown `sources`
- [x] 1.2 `scrapers/__init__.py`: static `SOURCES` registry (adapter or None, label, category, availability, needs_browser, requires_config, filters, hosts, ingestion) with `findweb3` and `web3career` entries; `_build_scrapers` iterates it, filtered by `sources`
- [x] 1.3 `GET /sources` returning `{schema_version, sources[]}` with computed `executable` (adapter registered and operational); no network I/O
- [x] 1.4 `sources` validation: unknown or non-executable ids -> 422 naming them; omitted = legacy default
- [x] 1.5 Contract tests: `/sources` shape and ids/availability, `executable` computed from registry and config, rejection of unknown and non-executable ids, omitted `sources` unchanged

## 2. Filters
- [x] 2.1 `utils/filters.py`: `passes_since`, `passes_work_mode`, `passes_seniority`, `passes_excluded_companies`, known-key matcher (uses `utils/normalize.py`)
- [x] 2.2 `tests/test_filters.py`: cases from the spec scenarios

- [x] 2.3 `utils/filters.py`: `term_hits(job, terms, match_mode)` (uses `norm_text`); route `passes_anti_keywords` and both scrapers' `match_keywords` through it; default `substring` identical to current code
- [x] 2.4 Tests: legacy-equivalence over a corpus (old vs new default), word/phrase/accent/case scenarios, company equality, seniority title-only

## 3. Scrapers and app
- [x] 3.1 findweb3 and web3career: skip known keys at search time; apply new filters; check deadline between fetches
- [x] 3.2 `app.py`: merge, dedupe, global rank, cut to `max_jobs_saved`; deadline handling and `errors[]`
- [x] 3.3 `tests/test_app.py`: legacy request unchanged, global cut/order, deadline partial result, discard-queue style request (`max_jobs_saved` = N)
- [x] 3.4 Scraper tests: known URL causes no detail request
- [x] 3.5 Update CLAUDE.md/AGENTS.md/README
