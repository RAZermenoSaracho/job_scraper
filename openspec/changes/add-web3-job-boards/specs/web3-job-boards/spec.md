## ADDED Requirements

### Requirement: Web3 Career official API adapter
The service SHALL read web3.career through `GET https://web3.career/api/v1` using a token from environment variable `WEB3CAREER_API_TOKEN`. The adapter SHALL be executable only when the token is set; otherwise the catalog entry SHALL have availability `planned` with a reason and a request naming `web3career` for it SHALL be rejected with 422. `apply_url` SHALL be passed through exactly as returned by the API. The token SHALL NOT appear in logs, errors or fixtures.

#### Scenario: Token set
- **WHEN** `WEB3CAREER_API_TOKEN` is set and `web3career` is requested
- **THEN** jobs come from the API with `source` `web3career`, `source_job_id` equal to the API id and `apply_url` byte-identical to the API value

#### Scenario: Token missing
- **WHEN** the token is not set
- **THEN** `GET /sources` shows `web3career` as not executable with a reason, and an explicit request for the API adapter returns 422

#### Scenario: Rate limited
- **WHEN** the API answers 429 with `Retry-After`
- **THEN** the adapter waits and retries within bounds, and records `errors[]` if still failing

### Requirement: Legacy Web3 Career scraper retired behind a flag
The HTML scraper for web3.career SHALL remain the default for requests without `sources` until the API adapter is live; once live, a flag SHALL switch to the API adapter, and the HTML scraper SHALL then be removed in this change.

#### Scenario: Frontend request before cut-over
- **WHEN** the current frontend body is posted
- **THEN** web3.career results are still returned with legacy keys unchanged

### Requirement: Hashtag Web3 feed adapter
The service SHALL read `https://hashtagweb3.com/data/jobs-runtime.json` at most once per run, for personal non-commercial use, honouring `Retry-After`, using exponential backoff with jitter, and caching by ETag or Last-Modified when provided. `source_job_id` SHALL be the feed `id`; `canonical_url` and `apply_url` SHALL derive from `link`, so that links to Greenhouse, Lever or Ashby postings produce the same `canonical_url` as the ATS adapters.

#### Scenario: One fetch for many keywords
- **WHEN** a run has three keywords
- **THEN** the feed is fetched once and filtered client-side

#### Scenario: Conditional request
- **WHEN** a cached ETag exists
- **THEN** the request sends `If-None-Match` and a 304 reuses the cached body

#### Scenario: ATS dedupe
- **WHEN** a feed row's `link` is a Greenhouse posting also returned by the greenhouse adapter
- **THEN** both rows have the same `canonical_url` and the global merge keeps one

### Requirement: Findweb3 hardening
The findweb3 scraper SHALL NOT pass anti-detection Chrome options, SHALL avoid a runtime chromedriver download when a driver is otherwise available, SHALL extract fields with selectors that do not depend on hashed CSS-module class names, and SHALL retry with backoff and handle 429. It SHALL NOT call `findweb3.com/api/`.

#### Scenario: No anti-detection flags
- **WHEN** the driver options are built
- **THEN** they contain neither `AutomationControlled` nor `excludeSwitches`

#### Scenario: Selector drift
- **WHEN** a page lacks the previously used hashed classes but has JSON-LD or structural markup
- **THEN** company and location are still extracted, or null with a logged warning

### Requirement: Stable ids, canonical URLs and fallback
Each adapter SHALL emit the normalized row (capability `job-result-schema`) with a stable `source_job_id` (web3career API id, hashtagweb3 `id`, findweb3 `/job/<id>` slug) and a canonical URL. When a source supplies no id, `source_job_id` SHALL be the sha1 hex of `canonical_url`.

#### Scenario: Fallback id
- **WHEN** a row has a URL but no source id
- **THEN** `source_job_id` equals the sha1 of its `canonical_url` and is identical across runs

### Requirement: Pagination where supported
The web3career API adapter SHALL page through results when the API documents a paging mechanism, requesting at most 100 per page (`limit`), and SHALL stop at the requested cap, an empty page or a repeated page.

#### Scenario: Cap respected
- **WHEN** paging is available and the cap is 150
- **THEN** at most 150 jobs are fetched in pages of at most 100

### Requirement: Per-source isolation and partial failure
A failing adapter SHALL add `{source, error}` to `errors[]` and SHALL NOT discard other sources' results.

#### Scenario: Partial failure
- **WHEN** the hashtagweb3 fetch fails and findweb3 succeeds
- **THEN** findweb3 rows are returned and `errors[]` names `hashtagweb3`

### Requirement: Catalog-only manual and defunct sources
The catalog SHALL list `linkedin`, `wellfound`, `bitcoinerjobs` and `laborx` with availability `manual` and `cyfrin` with availability `defunct`, all with `executable=false`, no adapter and an `ingestion` note. The service SHALL NOT fetch, scrape or browse these sites. A `sources` request naming any of them SHALL return 422.

#### Scenario: LinkedIn listed, not runnable
- **WHEN** `GET /sources` is called
- **THEN** `linkedin` appears with availability `manual`, `executable` false and an ingestion note to paste the URL into Odoo

#### Scenario: Requested anyway
- **WHEN** `sources` contains `linkedin`
- **THEN** the response is 422 naming `linkedin`

### Requirement: Offline tests per adapter
Each executable adapter SHALL have unit tests using recorded fixtures with no live network, covering mapping, pagination (where applicable), dedupe and partial failure.

#### Scenario: Offline suite
- **WHEN** the suite runs without network and without `WEB3CAREER_API_TOKEN`
- **THEN** all web3 board tests pass
