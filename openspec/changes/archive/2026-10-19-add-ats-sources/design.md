## Context

`web3career_jobscraper.py` already uses plain `requests`; ATS fetchers follow that pattern. One GET returns an entire board, so there is no per-job detail fetch.

## Decisions

1. Request field `boards: list[str] = []`, each `"<ats>:<token>"`, ats in `greenhouse|lever|ashby`, token `[A-Za-z0-9._-]+`. Invalid entries -> HTTP 422 at POST (fail fast, nothing runs). Duplicates collapsed case-insensitively.
2. Endpoints (documented public GETs):
   - Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` -> `jobs[]`: `id`, `title`, `absolute_url`, `location.name`, `updated_at`, `content` (HTML, entity-escaped), `departments`.
   - Lever: `https://api.lever.co/v0/postings/{company}?mode=json` -> list: `id`, `text`, `hostedUrl`, `applyUrl`, `categories.{location,team,commitment}`, `workplaceType`, `createdAt` (ms epoch), `description`/`descriptionPlain`, `salaryRange`.
   - Ashby: `https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true` -> `jobs[]`: `id`, `title`, `location`, `isRemote`, `workplaceType`, `publishedAt`, `jobUrl`, `applyUrl`, `descriptionHtml`/`descriptionPlain`, `compensation`.
   Field names above are from vendor docs as recalled; the first task captures one real sample per vendor as a fixture and the mapper is written against those fixtures.
3. Mapping: `company` = board token prettified unless the payload names it; `work_mode` from vendor flag (`isRemote`/`workplaceType`) first, then inference; legacy keys are also filled (`Job Title`, `Company Name`, `Job Description`, `Job Location`, `Compensation`, `Job Url`=canonical URL, `Tags`, `Notes`) so every consumer sees one shape.
4. Keyword relevance: ATS boards are not keyword-searchable, so rows are kept only if at least one `keywords` entry hits. Matching, `anti_keywords`, scoring and `match_mode` are NOT redefined here: ATS rows use `utils/filters.py::term_hits` exactly as specified in `improve-discovery-filters` (one rule for all sources). The `boards` field is the only request parameter owned by this change.
5. Politeness: User-Agent `job-scraper/1.0 (personal job search; contact in env JOB_SCRAPER_CONTACT)`; timeout 15 s; up to 3 tries with exponential backoff + jitter on 429/5xx/network errors, honoring `Retry-After` (capped 60 s); 404 = unknown board, no retry, reported in `errors[]`; boards fetched sequentially with 1 s gap. Callers should poll a given board at most a few times per day (documented; enforced by razs_jobs cadence, not here).
6. A failing board adds `{source: "greenhouse:stripe", error}` to `errors[]` and does not stop other boards or sources.
7. Tests inject a fake HTTP session (monkeypatched `requests.get`); no network.

## Registry

ATS adapters are registered in the registry owned by `improve-discovery-filters` (category `ats`, `requires_config` boards). Catalog shape, `GET /sources` and `sources` validation are defined there only.

## Risks

- Vendor payload drift -> mapper tolerates missing keys (null) and fixtures catch regressions.
- Large boards (hundreds of jobs) -> text filtering is in-memory and cheap.
