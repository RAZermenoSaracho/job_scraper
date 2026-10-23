## Context

Classification (authoritative, from research 2026-10-19):

| id | category | availability | executable | method |
|---|---|---|---|---|
| findweb3 | job_board | available | true | existing Selenium scraper, hardened |
| web3career | job_board | available | true only when `WEB3CAREER_API_TOKEN` set (else `planned` + reason) | official API |
| hashtagweb3 | job_board | available | true | JSON feed, personal non-commercial use |
| linkedin | job_board | manual | false | user pastes job URL into Odoo; ToS section 8.2 + robots prohibit automation |
| wellfound | job_board | manual | false | paste URL; ToS III(4),(6) prohibit scraping |
| bitcoinerjobs | job_board | manual | false | paste URL; ToS bans robots/spiders; automate only with written permission or a feed |
| laborx | job_board | manual | false | paste URL; ToS section 4 1.1(h) "any automated use ... is prohibited" |
| cyfrin | job_board | defunct | false | closed 2025-12-09 |

greenhouse/lever/ashby are `ats`, owned by `add-ats-sources`.

Registry rule (mechanism owned by `improve-discovery-filters`): `executable` is computed from the registry, never hand-set. "available" for an adapter in this change only becomes `executable=true` once the adapter is implemented and registered (and, for web3career, the token is present). Before that the entry is absent or `planned`.

## Decisions

1. **web3career API.** `GET https://web3.career/api/v1?token=...&limit=<=100` plus filters (`tag`, `remote`, and role/country/description params named in the docs). Token only from env, sent as query param, redacted from logs and errors. Terms: link attribution required; `apply_url` is passed through unmodified (no tracking params, no rewriting) and rows keep it as `apply_url`. `canonical_url` is the web3.career posting URL when the API returns one, else derived. `source_job_id` = API job id. Pagination: first task verifies in https://docs.bondex.app/api-reference whether paging exists (the landing page shows only `limit`, no cursor/offset); if none, one request per keyword/tag with `limit` up to 100 and no paging. 429 -> backoff, honour `Retry-After`; other errors isolated in `errors[]`. Token absent: catalog shows `planned` with reason "WEB3CAREER_API_TOKEN not set", and a request naming it gets 422 (via the registry).
2. **Legacy web3career path.** The HTML scraper (`scrapers/web3career_jobscraper.py`) remains the frontend default (`sources` omitted) until the API adapter is live. Then a flag (env `WEB3CAREER_LEGACY_SCRAPER`, default on until cut-over) switches the id `web3career` to the API adapter when the token is set; once stable, the HTML scraper and flag are removed in this change. The legacy ToS clause remains unverified (https://web3.career/legal returned 403); another reason to retire it.
3. **hashtagweb3 feed.** One GET of `https://hashtagweb3.com/data/jobs-runtime.json` per run (never per keyword): cached in memory by ETag/Last-Modified (`If-None-Match`/`If-Modified-Since`), honouring `Retry-After` and exponential backoff with jitter on 429/5xx (site limit 120 req/min/IP; we use far less). Personal non-commercial use only; recorded in the design and README. Filtering is client-side (keywords, anti-keywords, `active`, location, `workplaceType`, `date` for `since`). Fields: `id` (UUID, ATS id) -> `source_job_id`; `link` -> `canonical_url` (normalized, so Greenhouse/Lever/Ashby links converge with ATS rows) and `apply_url`; `date` -> `posted_at`; `workplaceType` -> `work_mode`. The feed has no descriptions; description stays empty (null) and enrichment via the ATS adapters is out of scope. Catalog covers last 30 days only.
4. **findweb3 hardening.** Remove `--disable-blink-features=AutomationControlled` and `excludeSwitches`; resolve chromedriver without runtime download when a driver is available on PATH or via Selenium Manager, documenting any remaining download; replace hashed CSS-module class selectors (`JobPage_companyName__...`) with resilient ones (JSON-LD/`og:` meta/structural selectors, text-based fallbacks) so a redeploy degrades to null fields with a logged warning rather than silently; add retries with backoff and 429 handling; keep random delays. Do not call `/api/` (robots-disallowed). `source_job_id` = last segment of `/job/<id>`. Normalized fields come from `normalize-job-results`.
5. **Stable ids.** web3career API id; hashtagweb3 `id` (UUID); findweb3 `/job/<id>` slug. Documented fallback when a source gives none: `sha1(canonical_url)` hex.
6. **Rate limits and isolation.** Each adapter has its own timeout (15 s), bounded retries, minimum inter-request gap, and runs inside the per-source error isolation of `normalize-job-results`; a failure yields `errors[]` and never aborts other sources. Deadline checks use `improve-discovery-filters`.
7. **Catalog-only entries.** `linkedin`, `wellfound`, `bitcoinerjobs`, `laborx`, `cyfrin` are registry entries with `adapter=None` and an `ingestion` note (e.g. "paste job URL into Odoo"). They are listed by `GET /sources` with `executable=false`, and `sources` naming them -> 422. LinkedIn: no adapter, no browser extraction; alert-email intake is deferred and out of scope.
8. **Tests.** pytest with recorded fixtures under `tests/fixtures/web3/`, patched HTTP layer, no live network, no token in fixtures.

## Verified 2026-10-19 (task 1.1, implementation)

- Docs (docs.bondex.app, Web3.Career Jobs API) and one live call: response is a top-level array `[str, str, [job...]]`; **no paging** (only `limit` <= 100, one `tag` per request, `remote`, `country`, `show_description`); invalid tags give HTTP 200 with an empty list; 429 on rate limit.
- Live job fields differ from the OpenAPI model: `id`, `title`, `company`, `location`, `description` (HTML), `tags` (list), `apply_url` (a `web3.career/r/<code>` redirect), `date`, `date_epoch`, `is_remote`, `country`, `city`, `salary_min_value`, `salary_max_value`, `salary_currency`, `salary_unit`; there is **no `url`**. The adapter derives the posting URL as `https://web3.career/<slug(title company)>/<id>` and maps `date_epoch` to `posted_at`.
- Consequence: the "pagination where supported" requirement is vacuous for this API; the adapter makes one request per keyword with `limit` = min(max_jobs_searched_per_keyword, 100).
- hashtagweb3 feed (one live GET): top-level array of ~1500 jobs of all kinds (not only web3), fields `id, title, company, link, date, location, workplaceType, isRemote, active, salary, department, slug, source, dateSource, dateVerified`; no ETag/Last-Modified were sent, so conditional requests only engage if the server starts sending them.

## Risks

- web3.career API schema and paging are not fully confirmed (only `apply_url` and `limit` seen): task 1.1 verifies against the docs before coding; mapping tolerates missing keys.
- hashtagweb3 commercial-use conditions are only partly read; use stays personal.
- Selenium cannot be fully eliminated for findweb3; hardening reduces, not removes, fragility.
