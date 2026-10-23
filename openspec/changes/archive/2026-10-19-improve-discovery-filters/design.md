## Decisions

1. All fields optional with neutral defaults: `sources=None` (current default sources), `known_keys=[]`, `since=None`, `work_modes=[]`, `seniority_exclude=[]`, `excluded_companies=[]`, `deadline_seconds=None`. Unknown or non-executable source ids -> 422 (see Source catalog).
2. Key forms accepted in `known_keys`: a canonical URL, `"<source>:<source_job_id>"`, or a `dedupe_key`. A candidate is known if any of its derived keys matches. Matching uses the helpers from `normalize-job-results` so Odoo and scraper agree. Existing `discarded_urls` keeps working unchanged; it is conceptually a subset.
3. Skip-before-fetch: scrapers compute keys from listing data (URL gives canonical URL and usually source id) and drop known candidates during search, like `discarded_urls`, so a known job never uses one of `max_jobs_searched_per_keyword` slots nor costs a detail request. `dedupe_key` can only be checked after detail data (company/location), so that form excludes after fetch.
4. Post-fetch filters in `utils/filters.py` as predicates in the existing style: `passes_since(job, since)`, `passes_work_mode`, `passes_seniority` (word-boundary match on title only, to avoid description false positives), `passes_excluded_companies` (normalized equality). Applied after `passes_experience`; drop counts logged like existing filters.
5. Global ranking: each scraper still returns its own filtered pool; `app.py` merges, de-duplicates by `canonical_url` then `dedupe_key` (higher score wins), sorts by score desc (stable on source order), and cuts to `max_jobs_saved`. For a single source or when sources return fewer than `max_jobs_saved` the output equals today's. Compatibility note: today the cap is per site and can return up to sites*`max_jobs_saved`; the cap becomes global. The frontend's discard queue requests `max_jobs_saved: N` and reads up to N replacements, so this is compatible (never more than asked). Scrapers get a `score` stored on an internal field removed before the response.
6. Deadline: monotonic deadline passed to scrapers; they check between page loads/boards and stop. App then marks remaining sources `errors[] {source, error: "deadline exceeded"}` and still returns status `done` with partial results (if any source produced rows or none failed otherwise). Default is no deadline (current behaviour).
7. Seniority and work-mode are best-effort text heuristics; documented as such.

## Source catalog

Static dict `SOURCES` in `scrapers/__init__.py`, plain data, no plugin system: `source_id -> {adapter: class | None, label, category (job_board|ats|feed), availability (available|planned|manual|defunct), needs_browser, requires_config (e.g. "boards", "token:ENV_NAME" or None), filters (supported server-side filters), hosts, ingestion (manual-paste description or None)}`. Per-source facts (which ids exist, methods, ToS notes) are owned by the changes that implement them (`add-web3-job-boards`, `add-ats-sources`); this change owns only the mechanism and the `findweb3`/`web3career` entries.

- `GET /sources` returns `{schema_version: "1", sources: [{id, name, category, availability, executable, requires_config, filters, hosts, ingestion}]}`. No network I/O, no adapter instantiation, so it is cheap and testable offline.
- `executable` is computed, never hand-set: true only if an adapter class is registered AND it is operational (its `requires_config` is satisfied, e.g. its token env var is set). Catalog-only entries (`adapter: None`, e.g. manual or defunct sources) are always `executable=false`. Availability `available` for a planned adapter therefore only turns `executable=true` once that adapter is implemented and registered; until then the entry is absent or `planned`.
- `sources` validation at POST: every id must exist and be executable; otherwise 422 listing the offending ids and why (unknown vs not executable). Never silently ignored. `sources` omitted = today's default (`findweb3` + `web3career` legacy), so the frontend is unaffected. ATS ids are selected via `boards` (`add-ats-sources`); they are listed in the catalog with `requires_config` = "boards".
- `_build_scrapers` iterates the registry instead of a hardcoded list; display-name vs id mismatch is resolved by using the registry id as `source`.

## Request parameter table (single authoritative definition)

| Field | Type | Default | Notes |
|---|---|---|---|
| `keywords` | list[str] | required | search terms and positive matching/ranking |
| `anti_keywords` | list[str] | `[]` | any hit excludes |
| `acceptable_locations` | list[str] | `[]` (was required) | case-insensitive substring; unknown location passes |
| `work_modes` | list[str] subset of remote/hybrid/onsite | `[]` | |
| `seniority_exclude` | list[str] | `[]` | |
| `max_experience_years` | int or null | `null` (was required) | null = no filter |
| `excluded_companies` | list[str] | `[]` | |
| `sources` | list[str] or null | `null` = current default (`findweb3` + `web3career` legacy path) | executable ids from the catalog; unknown or non-executable -> 422 naming them |
| `boards` | list[str] | `[]` | defined in `add-ats-sources` |
| `max_jobs_saved` | int | required | global cap |
| `max_jobs_searched_per_keyword` | int | required | per keyword, per aggregator source |
| `discarded_urls` | list[str] | `[]` | unchanged |
| `known_keys` | list[str] | `[]` | |
| `since` | ISO 8601 str or null | `null` | |
| `deadline_seconds` | number or null | `null` | |
| `match_mode` | `"substring"` or `"word"` | `"substring"` | below |

Unknown fields are still ignored (pydantic default).

## Keyword matching: today vs. the single rule

Today (verified in code): `utils/filters.py` `job_text()` = `Job Title` + space + tag-stripped `Job Description`; `passes_anti_keywords` (filters.py:55-61) lowercases text and each anti-keyword and tests plain substring (no word boundary, no accent folding, title and description). `match_keywords` in both scrapers (e.g. `web3career_jobscraper.py:259-262`) does the same case-insensitive substring test with `job_text`; `Tags` = matched keywords (original casing) and score = their count (unique, since each keyword is tested once). There is no minimum-hit requirement for aggregators: candidates come from the site's own search for each keyword, so a job with zero text hits can still be returned with score 0.

One rule for all sources (existing and ATS), selected by `match_mode`, applied to `title + description_text` (the same `job_text` string):
- `substring` (default): exactly today's behaviour (casefold-free `lower()` substring, no accent folding). Guarantees the frontend, which never sends `match_mode`, sees no change.
- `word`: both text and terms are normalized with `norm_text` (from `normalize-job-results`: NFKD, accents stripped, casefolded, punctuation to space, whitespace collapsed), then a term matches only as a whole word or whole phrase (the normalized term bounded by start/end or a space). So "go" does not match "mongo" or "category", "smart contracts" matches "Smart  Contracts!" and "SOLIDITY" matches "solidity", "Zürich" matches "zurich".
- Both modes: any `anti_keywords` hit excludes the job; `Tags`/score = unique `keywords` hit count; empty terms are ignored.
- "At least one keyword hit required" applies only to sources without server-side keyword search (ATS boards). Aggregators keep relying on the site search, as today (score 0 allowed).
- `seniority_exclude` always uses whole-word title matching via `norm_text` regardless of `match_mode` (new field, no legacy). `excluded_companies`: `norm_text(company) == norm_text(entry)` (equality, not substring; no legal-suffix stripping, so "ACME, Inc." equals "acme inc" but not "acme").
- Implementation: one helper `utils/filters.py::term_hits(job, terms, match_mode)` used by every scraper's keyword matching and anti-keyword check, replacing per-scraper copies.

## Risks

- Global cut changes result count for multi-site runs vs. today; accepted and documented.
- Heuristic filters can over-exclude; all are opt-in.
