## ADDED Requirements

### Requirement: Boards request field
`POST /jobs` SHALL accept an optional `boards` list of strings of the form `<ats>:<token>` with ats one of `greenhouse`, `lever`, `ashby`. When absent or empty, no ATS source SHALL run and behaviour SHALL be identical to before. A malformed entry SHALL cause a 422 response and no job to start.

#### Scenario: Valid boards
- **WHEN** `boards` is `["greenhouse:stripe","lever:netflix","ashby:ramp"]`
- **THEN** the job is accepted and each board is fetched

#### Scenario: Malformed entry
- **WHEN** `boards` contains `"workday:acme"` or `"stripe"`
- **THEN** the response is 422

#### Scenario: Omitted
- **WHEN** `boards` is not sent
- **THEN** no ATS requests are made

### Requirement: Plain-HTTP ATS fetching
The service SHALL read Greenhouse, Lever and Ashby public job-board APIs using plain HTTP requests, without a browser, and SHALL NOT call any endpoint that requires employer credentials or submits applications.

#### Scenario: No browser
- **WHEN** boards are requested
- **THEN** no Selenium driver is started for them

### Requirement: ATS rows follow the normalized schema
Each ATS posting SHALL be returned as a job row per capability `job-result-schema` (change `normalize-job-results`) with `source` set to the vendor, `ats_type` set, `source_job_id` equal to the vendor's posting id, `apply_url` the vendor apply link, `canonical_url` in the vendor canonical form, `posted_at` from the vendor date when provided, and the legacy keys filled.

#### Scenario: Greenhouse posting
- **WHEN** a Greenhouse board returns job id 42 titled "Backend Engineer"
- **THEN** the row has `source` `greenhouse`, `source_job_id` `42`, a `canonical_url` of the form `https://boards.greenhouse.io/{token}/jobs/42` and `Job Title` "Backend Engineer"

#### Scenario: Lever timestamp
- **WHEN** a Lever posting has `createdAt` in epoch milliseconds
- **THEN** `posted_at` is the equivalent ISO 8601 UTC string

#### Scenario: Remote flag
- **WHEN** an Ashby posting has `isRemote` true
- **THEN** `work_mode` is `remote`

### Requirement: Keyword and filter application
ATS postings SHALL be kept only if their title or description matches at least one request keyword, SHALL pass the location, anti-keyword, experience and discarded-URL filters, and SHALL be scored like other sources, using the single matching rule (`match_mode`, word/phrase/accent handling) defined by capability `discovery-filters` (change `improve-discovery-filters`) rather than any ATS-specific rule.

#### Scenario: Non-matching posting
- **WHEN** a posting matches no keyword
- **THEN** it is not returned

#### Scenario: Shared matching rule
- **WHEN** `match_mode` is `word` and a posting's text contains "mongodb" but not "go"
- **THEN** anti_keyword "go" does not exclude it, the same as for findweb3 and web3career

#### Scenario: Discarded URL
- **WHEN** a posting's canonical or listing URL is in `discarded_urls`
- **THEN** it is not returned

### Requirement: Polite, bounded fetching
Each request SHALL use a 15 second timeout and a descriptive User-Agent. On 429, 5xx or network errors the service SHALL retry at most twice more with exponential backoff and jitter, honoring `Retry-After` up to 60 seconds. A 404 SHALL NOT be retried.

#### Scenario: Rate limited then success
- **WHEN** the first response is 429 with `Retry-After: 2` and the second is 200
- **THEN** the board's jobs are returned after waiting

#### Scenario: Unknown board
- **WHEN** a board returns 404
- **THEN** no retry occurs and `errors[]` has an entry for that board

### Requirement: Per-board failure isolation
A failing board SHALL be recorded in `errors[]` as `{source, error}` and SHALL NOT prevent other boards or sources from returning results.

#### Scenario: One board times out
- **WHEN** `lever:netflix` fails after retries and `greenhouse:stripe` succeeds
- **THEN** Greenhouse rows are returned and `errors[]` names the Lever board

### Requirement: Offline tests
Automated tests for ATS sources SHALL use recorded fixtures and a fake HTTP layer and SHALL NOT access the network.

#### Scenario: Test run offline
- **WHEN** the suite runs without network
- **THEN** all ATS tests pass
