## Status: DEFERRED. Decisions recorded for later.

1. Same pattern as `add-ats-sources`: plain `requests`, fixtures, descriptive User-Agent, 15 s timeout, bounded backoff, per-source `errors[]`.
2. Attribution: every feed row sets `attribution` text and keeps the source URL in `canonical_url`; RemoteOK rows must link to RemoteOK, Remotive rows to Remotive. `attribution` is an additive field on feed rows only (schema_version stays "1"; consumers ignore unknown fields).
3. Cadence caps (module-level, in memory, reset on restart): RemoteOK at most once per 6 hours, Remotive at most 4 per UTC day, HN at most once per 24 hours. A call inside the window returns an `errors[]` entry `{source, error: "rate-capped until <ts>"}` and no network request. A restart resets counters, so Odoo's schedule is the primary limit.
4. Remotive 24-hour delay: rows younger than 24 hours are not served by Remotive itself; no workaround attempted.
5. HN: find the newest "Ask HN: Who is hiring?" story via Algolia search by author `whoishiring`, read its comment children; each top-level comment is one candidate, `source_job_id` = comment id, `canonical_url` = `https://news.ycombinator.com/item?id=<id>`. Company/title parsed from the conventional first line `Company | Role | Location | ...`; failures leave nulls.
6. Scope: `hashtagweb3` (JSON feed) and `web3career` (API) belong to `add-web3-job-boards`; do not add them here.
7. Re-verify each provider's current terms before implementation.
