## 1. Fixtures
- [x] 1.1 Capture one trimmed real response per vendor into `tests/fixtures/ats/{greenhouse,lever,ashby}.json` (one-time manual fetch; tests never fetch) (NOTE: greenhouse.json is hand-written from the documented response shape — the single permitted live capture was lost to a tooling error; lever.json and ashby.json are trimmed real captures)

## 2. Scraper
- [x] 2.1 `scrapers/ats_scraper.py`: parse `ats:token`, fetch with timeout, retry/backoff, User-Agent
- [x] 2.2 Map each vendor to the normalized row via `utils/normalize.py` (depends on `normalize-job-results`) and legacy keys
- [x] 2.3 Keyword hit requirement + existing filters + scoring via shared `term_hits` (depends on `improve-discovery-filters`; no local matcher)

## 3. App
- [x] 3.0 Register `greenhouse`, `lever`, `ashby` in the `scrapers/__init__.py` registry (category `ats`, `requires_config` boards, `executable` computed by the registry, availability `available` once this adapter exists); appear in `GET /sources`
- [x] 3.1 Add `boards` to `JobsRequest` with validation (422); wire into `_build_scrapers`
- [x] 3.2 Report per-board failures into `errors[]`

## 4. Tests (pytest, no network)
- [x] 4.1 Mapping per vendor from fixtures (ids, URLs, dates, work_mode)
- [x] 4.2 Retry on 429 with Retry-After (sleep patched), 404 no retry, timeout isolation
- [x] 4.3 `boards` validation and omitted-field behaviour in `tests/test_app.py`
- [x] 4.4 Update CLAUDE.md/AGENTS.md/README
