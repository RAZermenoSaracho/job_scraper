## ADDED Requirements

### Requirement: Deferred feed sources
The feed sources `remoteok`, `remotive` and `hn` SHALL be selectable through the `sources` request field (capability `discovery-filters`) and SHALL return rows per capability `job-result-schema`. This change is deferred and SHALL NOT alter behaviour of requests that do not name these sources.

#### Scenario: Not requested
- **WHEN** `sources` is omitted
- **THEN** no feed is contacted

#### Scenario: Feed requested
- **WHEN** `sources` is `["remoteok"]`
- **THEN** rows have `source` `remoteok` and the normalized keys

### Requirement: Attribution
Rows from RemoteOK and Remotive SHALL include a non-empty `attribution` and a `canonical_url` pointing at the provider's own posting page.

#### Scenario: Backlink present
- **WHEN** a RemoteOK row is returned
- **THEN** `canonical_url` is on `remoteok.com` and `attribution` names RemoteOK

### Requirement: Cadence caps
The service SHALL perform at most one RemoteOK fetch per 6 hours, at most 4 Remotive fetches per UTC day and at most one HN fetch per 24 hours. A capped call SHALL make no network request and SHALL add an `errors[]` entry for that source.

#### Scenario: Fifth Remotive call
- **WHEN** four Remotive fetches already happened today
- **THEN** the fifth makes no request and `errors[]` contains a rate-capped entry for `remotive`

### Requirement: Hacker News best-effort parsing
HN rows SHALL use the comment id as `source_job_id` and the item URL as `canonical_url`; fields that cannot be parsed from the conventional `Company | Role | Location` first line SHALL be `null`.

#### Scenario: Unparseable comment
- **WHEN** a comment does not follow the pipe convention
- **THEN** `company` and `title` are `null` and the row is still returned with its text as description

### Requirement: Offline tests
Tests SHALL use fixtures and SHALL NOT access the network.

#### Scenario: Offline
- **WHEN** the suite runs without network
- **THEN** feed tests pass
