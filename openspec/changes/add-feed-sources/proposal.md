## Why

Public job feeds (RemoteOK, Remotive, Hacker News "Who is hiring") broaden discovery beyond employer boards at very low cost. They carry attribution and rate conditions, so they are scoped separately.

## STATUS: DEFERRED

Do not implement until `normalize-job-results` and `add-ats-sources` are in use and the owner asks for feeds. This proposal exists to fix the shape and the constraints now.

## What Changes

- Feed scrapers using plain HTTP, selectable via `sources` (`remoteok`, `remotive`, `hn`), rows in the `normalize-job-results` schema.
- RemoteOK (`remoteok.com/api`): terms require linking back and crediting RemoteOK; rows keep their RemoteOK URL as `canonical_url` and carry attribution.
- Remotive (`remotive.com/api/remote-jobs`): at most 4 requests per day and a 24-hour publication delay; attribution required; no republishing.
- Hacker News "Who is hiring" via Algolia (`hn.algolia.com/api/v1`): newest monthly thread only; free-form text, so rows are best-effort (title/company/location heuristics), flagged for human review.
- Cadence caps enforced in the scraper (in-process minimum interval per feed) in addition to Odoo's schedule.

## Non-goals

Hashtag Web3 and Web3 Career are NOT feeds of this change; they are owned by `add-web3-job-boards`.

LinkedIn (User Agreement section 8.2 prohibits scraping and bots), Indeed, Workday, any anti-bot circumvention.

## Capabilities

### New Capabilities
- `feed-sources`: feed fetchers, attribution, cadence caps.

## Impact

New `scrapers/feeds.py`, fixtures, tests. Depends on `normalize-job-results`. Terms to re-verify at implementation time (recalled, not freshly fetched).
