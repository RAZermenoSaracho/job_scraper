## ADDED Requirements

### Requirement: Optional request fields with neutral defaults
`POST /jobs` SHALL accept the optional fields `sources`, `known_keys`, `since`, `work_modes`, `seniority_exclude`, `excluded_companies`, `deadline_seconds` and `match_mode`, with `acceptable_locations` and `max_experience_years` also becoming optional (defaults `[]` and `null`). The field names, types and defaults SHALL be exactly those in the design's parameter table. A request omitting all of them SHALL behave exactly as before this change. An unknown or non-executable source id in `sources` SHALL yield 422 naming the offending ids; ids SHALL never be silently ignored. Omitted `sources` SHALL run the current default sources (`findweb3`, `web3career`).

#### Scenario: Legacy request
- **WHEN** only today's fields are sent
- **THEN** all current sources run and results are filtered as before

#### Scenario: Source selection
- **WHEN** `sources` is `["web3career"]`
- **THEN** findweb3 is not started

#### Scenario: Unknown source
- **WHEN** `sources` contains `"foo"`
- **THEN** the response is 422 naming `foo`

#### Scenario: Non-executable source
- **WHEN** `sources` contains a catalog id with `executable=false` (e.g. `linkedin`)
- **THEN** the response is 422 naming it and no job starts

### Requirement: Source catalog endpoint
The service SHALL expose `GET /sources` returning `{schema_version, sources}` where each entry has `id`, `name`, `category`, `availability`, `executable`, `requires_config`, `filters`, `hosts` and `ingestion`. The data SHALL come from a static registry in `scrapers/__init__.py` and the endpoint SHALL perform no network I/O. `executable` SHALL be true only when an adapter is registered and operational (for example its required token is configured); catalog-only entries SHALL always be `executable=false`. The request `sources` field and `_build_scrapers` SHALL use the same registry.

#### Scenario: Catalog shape
- **WHEN** `GET /sources` is called
- **THEN** the response has `schema_version` and a `sources` list whose entries have all listed keys, including `findweb3` and `web3career` as `executable=true`

#### Scenario: Computed executable
- **WHEN** a registered adapter's required token is not configured
- **THEN** its entry has `executable=false` and a request naming it gets 422

#### Scenario: No network
- **WHEN** `GET /sources` is called with the network disabled
- **THEN** it responds normally

### Requirement: Known keys skip detail fetch
A candidate whose canonical URL, `<source>:<source_job_id>`, or (after fetch) `dedupe_key` appears in `known_keys` SHALL be excluded from the results. When it can be identified from listing data, it SHALL be skipped before its detail page is requested and SHALL NOT consume a per-keyword search slot.

#### Scenario: Known URL not fetched
- **WHEN** a listing link's canonical URL is in `known_keys`
- **THEN** no detail request is made for it and a new posting takes its slot

#### Scenario: Known by dedupe key
- **WHEN** a fetched job's `dedupe_key` is in `known_keys`
- **THEN** it is absent from the results

### Requirement: Since filter
When `since` is provided, jobs whose `posted_at` is earlier SHALL be excluded; jobs with null `posted_at` SHALL be kept.

#### Scenario: Old and undated
- **WHEN** `since` is `2026-10-01T00:00:00Z` and jobs are dated 2026-09-15, 2026-10-10 and undated
- **THEN** the 2026-09-15 job is excluded and the other two remain

### Requirement: Work mode, seniority and company filters
`work_modes` SHALL keep only jobs whose `work_mode` is listed or `unknown`. `seniority_exclude` SHALL drop jobs whose title contains a listed term as a whole word, case-insensitively. `excluded_companies` SHALL drop jobs whose normalized company equals a listed normalized name.

#### Scenario: Remote only
- **WHEN** `work_modes` is `["remote"]` and a job is `onsite`
- **THEN** it is excluded, while a `remote` and an `unknown` job remain

#### Scenario: Seniority whole word
- **WHEN** `seniority_exclude` is `["senior"]` and titles are "Senior Engineer" and "Seniority Analyst"
- **THEN** only "Senior Engineer" is dropped

#### Scenario: Excluded company
- **WHEN** `excluded_companies` is `["ACME, Inc."]` and a job's company is "acme inc"
- **THEN** it is excluded

### Requirement: Global ranking and cap
After all sources finish, the service SHALL merge their results, remove duplicates by `canonical_url` then `dedupe_key`, rank by relevance score descending, and return at most `max_jobs_saved` jobs in total. `max_jobs_saved` SHALL never be exceeded.

#### Scenario: Cross-source best first
- **WHEN** two sources return jobs with scores 3,1 and 5,2 and `max_jobs_saved` is 3
- **THEN** the result contains the jobs scored 5, 3, 2

#### Scenario: Duplicate across sources
- **WHEN** two sources return the same `dedupe_key`
- **THEN** only the higher-scored row is returned

### Requirement: Overall deadline
When `deadline_seconds` is provided, the run SHALL stop starting new page or board fetches after that many seconds, return the results gathered so far, and add an `errors[]` entry per interrupted source. Without it, no deadline SHALL apply.

#### Scenario: Deadline hit
- **WHEN** the deadline expires during the second source
- **THEN** status is `done`, first-source results are returned and `errors[]` notes the interrupted source

### Requirement: One keyword matching rule for every source
`keywords` and `anti_keywords` SHALL be matched against the job title plus tag-stripped description text by the same function for every source, selected by `match_mode`. With `match_mode` absent or `substring` the behaviour SHALL equal the pre-change behaviour (case-insensitive substring). With `word` terms SHALL match only as whole words or phrases after accent-folding, casefolding and punctuation/whitespace normalization. Any anti-keyword hit SHALL exclude the job; `Tags` and score SHALL be the unique keywords matched.

#### Scenario: Legacy default unchanged
- **WHEN** a request without `match_mode` has anti_keyword "go" and a description containing "mongodb"
- **THEN** the job is excluded, as today

#### Scenario: Word mode avoids substring false positives
- **WHEN** `match_mode` is `word`, anti_keyword is "go" and the text contains "mongodb" but not the word "go"
- **THEN** the job is kept

#### Scenario: Multi-word phrase, case and punctuation
- **WHEN** `match_mode` is `word` and keyword "smart contracts" meets text "Senior SMART  Contracts, Solidity"
- **THEN** it matches and counts once

#### Scenario: Accents
- **WHEN** `match_mode` is `word` and anti_keyword "zurich" meets text "Based in Zürich"
- **THEN** the job is excluded

#### Scenario: Same rule across sources
- **WHEN** the same text is evaluated for a findweb3, web3career and an ATS row
- **THEN** the match outcome and score are identical

#### Scenario: Ranking
- **WHEN** one job hits 3 distinct keywords (one of them twice) and another hits 1
- **THEN** scores are 3 and 1

### Requirement: Company and seniority matching
`excluded_companies` SHALL match by equality of `norm_text` values (case- and accent-insensitive, punctuation-insensitive, no substring matching). `seniority_exclude` SHALL match whole words in the title only, in every `match_mode`.

#### Scenario: Not a substring
- **WHEN** `excluded_companies` is `["acme"]` and a job's company is "Acme Labs"
- **THEN** the job is kept

#### Scenario: Seniority in description only
- **WHEN** `seniority_exclude` is `["senior"]` and only the description says "work with senior staff"
- **THEN** the job is kept

### Requirement: Frontend compatibility
A request body containing exactly the legacy fields SHALL produce the same filtering, `Tags`, ordering within a source, status shape and result shape as before this change, and legacy fields `keywords` and `jobs` SHALL remain in the result.

#### Scenario: Current frontend body
- **WHEN** the body from `job_scraper_frontend/src/utils/jobRequest.js` is posted
- **THEN** it is accepted with 202 and polling returns `status`, `stage`, `current`, `total` and a result with `jobs`
