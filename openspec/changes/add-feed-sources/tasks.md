DEFERRED: do not start until requested.

## 1. Prerequisites
- [ ] 1.1 `normalize-job-results` and `improve-discovery-filters` (for `sources`) implemented
- [ ] 1.2 Re-verify RemoteOK, Remotive and HN/Algolia terms and limits

## 2. Implementation
- [ ] 2.1 `scrapers/feeds.py`: three fetchers, mapping, attribution
- [ ] 2.2 In-memory cadence caps with `errors[]` on capped calls

## 3. Tests (pytest, fixtures, no network)
- [ ] 3.1 Mapping per feed; HN parseable and unparseable comments
- [ ] 3.2 Cadence caps with a patched clock
- [ ] 3.3 Update CLAUDE.md/AGENTS.md/README
