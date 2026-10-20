## Context

`POST /jobs` -> 202 `{job_id}`; `GET /jobs/{id}/status` -> `{status, stage, current, total, [error]}`; `GET /jobs/{id}/result` -> `{keywords, jobs}`. Legacy job keys: `Job Title`, `Company Name`, `Job Description` (raw HTML), `Job Location`, `Compensation`, `Job Url`, `Tags` (csv of matched keywords), `Notes`.

Frontend (`/Users/razs/production/job_scraper_frontend`, pm2 `job-scraper-frontend`) reads exactly:
- Request: `keywords, acceptable_locations, anti_keywords, discarded_urls, max_jobs_searched_per_keyword, max_jobs_saved, max_experience_years` (`src/utils/jobRequest.js`; discard queue overrides `max_jobs_saved`).
- Responses: `job_id`; status `status` ("done"/"error"/other), `stage`, `current`, `total`, `error`; error body `detail`; result `jobs` (array). `result.keywords` is not read.
- Row: `Job Title, Company Name, Job Description, Job Location, Compensation, Job Url, Tags, Notes`; `Job Url` is also the discard key. Client-only `__key`/`__noReplacement` are added locally.
Everything above MUST keep its name, type and meaning.

## Decisions

1. Additive only. New keys are snake_case, legacy keys keep their spaces. Unknown keys are ignored by the frontend (verified: it indexes by name).
2. `schema_version: "1"`. Adding fields stays "1"; renaming/removing/retyping bumps it. Consumers MUST ignore unknown fields and check the version.
3. Field semantics (null when unknown, never missing):
   - `source`: `findweb3`, `web3career`, and later `greenhouse|lever|ashby`.
   - `source_job_id`: stable per source. web3.career: numeric trailing id in the job URL; findweb3: the job slug/id segment of its URL (last path segment). ATS: the vendor's id.
   - `canonical_url`: listing/posting URL, canonicalized. `apply_url`: employer/ATS apply link when the source provides it, else null (legacy `Job Url` is a listing page).
   - `company`/`title`/`location`: trimmed copies of legacy values. `work_mode`: `remote|hybrid|onsite|unknown`, inferred from location/title/tags text with word matching; unknown if no signal.
   - `posted_at`: ISO 8601 UTC string or null. `description_html`: legacy HTML unchanged; `description_text`: tags stripped, entities decoded, whitespace collapsed. `salary`: free text copy of `Compensation` or null. `ats_type`: vendor if the apply/canonical URL host is greenhouse/lever/ashby, else null. `tags`: list of strings.
   - `dedupe_key`: `norm(company)|norm(title)|norm(location)` where norm = NFKD casefold, punctuation to space, whitespace collapsed; missing part is empty string.
4. Canonical URL: lowercase scheme and host, drop default ports, fragment and trailing slash (except root), remove query params `utm_*`, `ref`, `gh_src`, `source`, `lever-source`, `lever-origin`, `fbclid`, `gclid`, `mc_*`; keep and sort remaining params. ATS canonical forms: Greenhouse `https://boards.greenhouse.io/{token}/jobs/{id}` (also from `job-boards.greenhouse.io`), Lever `https://jobs.lever.co/{company}/{uuid}` (apply suffix `/apply` removed), Ashby `https://jobs.ashbyhq.com/{org}/{uuid}`. Board canonical forms (so Odoo's manual URL paste dedupes identically to scraped rows; Odoo reuses the same rules): LinkedIn `https://www.linkedin.com/jobs/view/<id>/` (host normalized to `www.linkedin.com`, `/comm/` prefix, query and slug text removed, numeric id kept, trailing slash kept as the one documented exception to rule "no trailing slash"); Wellfound `https://wellfound.com/jobs/<id>-<slug>` reduced to `https://wellfound.com/jobs/<id>` (keep the numeric id, drop slug); web3.career `https://web3.career/<slug>/<id>`; findweb3 `https://findweb3.com/job/<id>`; hashtagweb3 the canonical form of its `link` (usually an ATS URL, so it converges with ATS rows). These are pure URL rules only: nothing is fetched for LinkedIn or Wellfound.
5. Error isolation in `_run_scrape_job`: each source in its own try/except; failure appends `{source, error}` (string, truncated, no stack) to `errors`. Status: `done` if at least one source succeeded (even with 0 rows) or none were requested; `error` only if all sources failed (preserves old frontend behaviour: it shows the error). Progress counters keep current behaviour.
6. Optional `X-API-Key`: enabled only when env `JOB_SCRAPER_API_KEY` is set; constant-time compare; applies to all three routes. Default off so the frontend and Odoo (which calls `http://127.0.0.1:8420`, no tunnel hop) are unaffected. Justification: `api-jobs.razs.dev` is public via tunnel and CORS is `*`; a shared secret is a cheap guard. The frontend cannot send it (a Vite client would expose it), so enabling it requires a separate decision about the frontend path; documented, not enabled here.
7. `utils/normalize.py` also exports `norm_text(s)` (NFKD, strip combining marks, casefold, punctuation to space, whitespace collapsed) as the single text normalizer; `dedupe_key` and the matching rules in `improve-discovery-filters` (keywords, anti-keywords, seniority, excluded companies) all use it. This change defines no new request parameters; request body stays unchanged (new request fields are owned by `improve-discovery-filters` and `add-ats-sources`).
8. Rows are built by one function in `utils/normalize.py` called from each scraper's final mapping, so legacy and normalized values cannot drift.

## Risks / Trade-offs

- Backward compatibility: all existing frontend-facing request/response tests stay and must keep passing unchanged.
- findweb3 id derivation is URL-shape dependent; fixtures lock it and a missing id yields null (Odoo then dedupes on `canonical_url`/`dedupe_key`).
- `dedupe_key` collides for genuinely different roles with same title/location at one company; accepted, it is a duplicate hint, not an identity.
- Partial results can hide a broken source; mitigated by `errors[]` which Odoo must surface.
