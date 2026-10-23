Depends on `normalize-job-results` and `improve-discovery-filters` (registry, `GET /sources`).

## 1. Research and fixtures
- [x] 1.1 Read https://docs.bondex.app/api-reference: confirm response schema, paging (if any), rate limits, filter param names; record in design
- [x] 1.2 Capture trimmed fixtures (web3career API response with token removed, hashtagweb3 feed slice, findweb3 listing/detail pages) under `tests/fixtures/web3/`; tests never fetch (NOTE: web3career API and hashtagweb3 fixtures are sanitized/trimmed live captures; findweb3 listing/detail pages were NOT captured live (no live run against findweb3 allowed) — tests use inline synthetic markup in `tests/test_fw3_scraper.py`)
- [ ] 1.3 BLOCKED: manual owner action (web3.career/legal returns 403 to tooling; not decidable by the implementation). Owner reads https://web3.career/legal manually (403 to tooling) before the legacy scraper is kept longer than the cut-over

## 2. Web3 Career API adapter
- [x] 2.1 `scrapers/web3career_api.py`: token from env, `limit` <=100, paging if documented, retries/429, unmodified `apply_url`, normalized row
- [x] 2.2 Registry entry with `requires_config` token; `planned` + reason when unset
- [ ] 2.3 DEFERRED (partly done): the flag-gated cut-over is implemented (`WEB3CAREER_LEGACY_SCRAPER`, default on; `scrapers.adapter_for`), but removing the legacy HTML scraper and the flag must wait until the API adapter is verified live after deployment (removing it now would break the frontend default path when the token/process is not yet cut over). Flag-gated cut-over from the legacy HTML scraper; then remove the legacy scraper and flag

## 3. Hashtag Web3 feed adapter
- [x] 3.1 `scrapers/hashtagweb3_feed.py`: single fetch per run, ETag/Last-Modified cache, Retry-After/backoff, client-side filters, normalized row from `link`
- [x] 3.2 Registry entry; note personal non-commercial use in README

## 4. Findweb3 hardening
- [x] 4.1 Remove anti-detection options; avoid runtime chromedriver download where possible
- [x] 4.2 Replace hashed-class selectors with resilient extraction; retries, 429 handling
- [x] 4.3 Update `tests/test_fw3_scraper.py` for new selectors and options; keep legacy-key assertions

## 5. Catalog entries
- [x] 5.1 Registry entries (no adapter, `executable=false`) for linkedin, wellfound, bitcoinerjobs, laborx (manual) and cyfrin (defunct) with `ingestion` notes; LinkedIn has no adapter and no browser extraction

## 6. Tests (pytest, no network)
- [x] 6.1 Per-adapter mapping, stable id and sha1 fallback, canonical URL, `apply_url` passthrough
- [x] 6.2 Pagination, dedupe against ATS rows, partial failure, 429/Retry-After, token-missing 422
- [x] 6.3 `/sources` shows catalog-only entries non-executable; `sources` naming them -> 422
- [x] 6.4 Update CLAUDE.md/AGENTS.md/README; run `pytest`
