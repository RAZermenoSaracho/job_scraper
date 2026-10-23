## ADDED Requirements

### Requirement: Normalized job row
Every job in the result `jobs` array SHALL contain, in addition to all legacy keys, the keys `source`, `source_job_id`, `canonical_url`, `apply_url`, `company`, `title`, `location`, `work_mode`, `posted_at`, `description_html`, `description_text`, `salary`, `ats_type`, `tags` and `dedupe_key`. Unknown values SHALL be `null` (`tags` an empty list), never omitted. `work_mode` SHALL be one of `remote`, `hybrid`, `onsite`, `unknown`. `posted_at` SHALL be an ISO 8601 UTC string or `null`. `description_text` SHALL be the plain-text form of `description_html`.

#### Scenario: Row from an existing source
- **WHEN** a findweb3 or web3.career job is returned
- **THEN** it has all legacy keys with unchanged values and all normalized keys, with `source` equal to `findweb3` or `web3career`

#### Scenario: Unknown optional data
- **WHEN** a source provides no date or salary
- **THEN** `posted_at` and `salary` are `null` and the keys are still present

#### Scenario: Description text
- **WHEN** `description_html` is `<p>Build &amp; ship</p><ul><li>Rust</li></ul>`
- **THEN** `description_text` contains no tags and reads "Build & ship Rust" with collapsed whitespace

### Requirement: Result envelope and schema version
The result response SHALL include `schema_version` with value `"1"` and an `errors` array, alongside the legacy `keywords` and `jobs`. Consumers are expected to ignore unknown fields; removing, renaming or retyping any field SHALL require a new `schema_version`.

#### Scenario: Envelope on success
- **WHEN** a run completes without source failures
- **THEN** the result has `schema_version` `"1"`, `errors` equal to `[]`, plus `keywords` and `jobs`

### Requirement: Canonical URL
`canonical_url` SHALL be derived deterministically: lowercase scheme and host, no default port, no fragment, no trailing slash except the root path, tracking parameters (`utm_*`, `ref`, `gh_src`, `source`, `lever-source`, `lever-origin`, `fbclid`, `gclid`, `mc_*`) removed, remaining query parameters sorted. Greenhouse, Lever and Ashby job URLs SHALL map to their canonical forms `https://boards.greenhouse.io/{token}/jobs/{id}`, `https://jobs.lever.co/{company}/{uuid}` and `https://jobs.ashbyhq.com/{org}/{uuid}`. LinkedIn (`https://www.linkedin.com/jobs/view/<id>/`, keeping its trailing slash as a documented exception), Wellfound (`https://wellfound.com/jobs/<id>`), web3.career, findweb3 and hashtagweb3 URLs SHALL also have deterministic canonical forms so that rows scraped by this service and URLs pasted manually into Odoo dedupe identically; canonicalization performs no network access. Canonicalization SHALL be idempotent.

#### Scenario: Tracking stripped
- **WHEN** the URL is `HTTPS://Web3.Career/job-x/123/?utm_source=a&ref=b#apply`
- **THEN** `canonical_url` is `https://web3.career/job-x/123`

#### Scenario: Greenhouse forms converge
- **WHEN** the URL is `https://job-boards.greenhouse.io/stripe/jobs/42?gh_src=x`
- **THEN** `canonical_url` is `https://boards.greenhouse.io/stripe/jobs/42`

#### Scenario: Lever apply suffix
- **WHEN** the URL is `https://jobs.lever.co/netflix/abc-123/apply?lever-source=x`
- **THEN** `canonical_url` is `https://jobs.lever.co/netflix/abc-123`

#### Scenario: LinkedIn paste form
- **WHEN** the URL is `https://de.linkedin.com/comm/jobs/view/3912345678/?trackingId=x&refId=y`
- **THEN** `canonical_url` is `https://www.linkedin.com/jobs/view/3912345678/` and `source_job_id` is `3912345678`

#### Scenario: Wellfound slug dropped
- **WHEN** the URL is `https://wellfound.com/jobs/1234567-senior-engineer?utm_source=a`
- **THEN** `canonical_url` is `https://wellfound.com/jobs/1234567` and `source_job_id` is `1234567`

#### Scenario: Web3 boards
- **WHEN** URLs from web3.career (`/slug/<id>`), findweb3 (`/job/<id>`) or a hashtagweb3 `link` are canonicalized
- **THEN** each yields one stable form, and a hashtagweb3 `link` to Greenhouse/Lever/Ashby yields that ATS canonical form

#### Scenario: Idempotent
- **WHEN** canonicalization is applied to its own output
- **THEN** the value is unchanged

### Requirement: Stable source job id
`source_job_id` SHALL be identical across runs for the same posting on the same source and SHALL be derived from the posting URL or vendor id, never from list position or run time.

#### Scenario: Same posting twice
- **WHEN** the same web3.career posting is scraped in two runs
- **THEN** both rows have the same `source_job_id` and `canonical_url`

#### Scenario: Id not derivable
- **WHEN** an id cannot be derived from the URL
- **THEN** `source_job_id` is `null` and `canonical_url` and `dedupe_key` are still set

### Requirement: Dedupe key
`dedupe_key` SHALL equal normalized company, title and location joined by `|`, where normalization is case-folded, accent-folded, punctuation-insensitive and whitespace-collapsed.

#### Scenario: Cosmetic differences
- **WHEN** two rows differ only by case, punctuation or spacing in company/title/location
- **THEN** their `dedupe_key` values are equal

### Requirement: Per-source error isolation
A failure in one source SHALL NOT discard results from other sources. Each failed source SHALL add `{source, error}` to `errors` with a short message and no stack trace. The run status SHALL be `done` if at least one source succeeded and `error` only if every source failed.

#### Scenario: One source fails
- **WHEN** web3.career raises while findweb3 succeeds
- **THEN** status is `done`, `jobs` contains the findweb3 rows and `errors` has one entry with `source` `web3career`

#### Scenario: All sources fail
- **WHEN** every source raises
- **THEN** status is `error` with an `error` message, as before

### Requirement: Backward compatibility
The request body, the three routes and their status codes (202, 409, 404, 500), the status payload keys (`status`, `stage`, `current`, `total`, `error`), the result keys `keywords` and `jobs`, and every legacy job key (`Job Title`, `Company Name`, `Job Description`, `Job Location`, `Compensation`, `Job Url`, `Tags`, `Notes`) SHALL keep their names, types and meaning. A request containing only today's fields SHALL be accepted and behave as before.

#### Scenario: Existing frontend request
- **WHEN** the current frontend request body is posted
- **THEN** it returns 202 with `job_id` and the result contains all legacy keys the frontend reads

### Requirement: Optional API key
The service SHALL enforce an `X-API-Key` header on all routes only when the environment variable `JOB_SCRAPER_API_KEY` is set; when unset, behaviour SHALL be unchanged. A missing or wrong key SHALL return 401.

#### Scenario: Disabled by default
- **WHEN** the variable is unset and a request has no key
- **THEN** the request is served normally

#### Scenario: Enabled
- **WHEN** the variable is set and the header is missing or wrong
- **THEN** the response is 401
