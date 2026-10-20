## 1. Pure helpers
- [x] 1.1 Add `utils/normalize.py`: `canonical_url`, `dedupe_key`, `infer_work_mode`, `html_to_text`, `norm_text`, `build_row(legacy, source, ...)`; canonical forms for linkedin, wellfound, web3career, findweb3, hashtagweb3 (plus ATS)
- [x] 1.2 Add `tests/test_normalize.py`: tracking strip, host/port/fragment, ATS canonical forms, linkedin/wellfound/web3 board forms (paste-URL cases), idempotence, dedupe equivalence, description text, work-mode cases

## 2. Existing scrapers
- [x] 2.1 Emit normalized fields in `scrapers/web3career_jobscraper.py` (id from URL, `posted_at` from JSON-LD when present); extend its tests with a fixture
- [x] 2.2 Emit normalized fields in `scrapers/fw3_jobscraper.py` (id from URL slug); extend its tests
- [x] 2.3 Assert legacy keys unchanged in both tests (existing frontend backward-compat tests stay)
- [x] 2.4 Hardening of the findweb3 and web3career scrapers and the web3career API/hashtagweb3 adapters are owned by `add-web3-job-boards`, not here

## 3. App
- [x] 3.1 Per-source try/except in `_run_scrape_job`; `errors[]`, `schema_version` in result; `error` only if all failed
- [x] 3.2 Optional `X-API-Key` dependency (env-gated, constant-time compare)
- [x] 3.3 `tests/test_app.py`: one-source-fails, all-fail, envelope keys, legacy request unchanged, key on/off

## 4. Docs
- [x] 4.1 Update CLAUDE.md, AGENTS.md, README (port 8420, both sources, new fields, `schema_version`)
- [x] 4.2 Run `pytest`; confirm no frontend change needed
