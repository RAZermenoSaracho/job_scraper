# Proposal: move-secret-files-to-secrets-dir

## Why

The web3.career API token file lives next to the Odoo configuration
(`/Users/razs/production/odoo_src/web3career_api_token`), although only this
service uses it. Host secrets now follow one layout (documented in razs_server
`docs/SERVER.md`): `~/secrets/<service>/` for secrets used outside Odoo,
`<odoo_src>/secrets/` for the Odoo installation.

## What Changes

- `ecosystem.config.cjs` reads the token from `/Users/razs/secrets/web3career/api_token`.
  Behaviour is unchanged: missing/unreadable file leaves `WEB3CAREER_API_TOKEN`
  unset and the adapter reports itself non-executable in `GET /sources`.

## Impact

- Deploy: merging to `main` deploys (CI runs `pm2 restart ecosystem.config.cjs`),
  so the file is copied with `cp -p` to the new path (directory `0700`, file
  `0600`) **before** the merge. The old file is removed only after
  `GET /sources` shows `web3career` executable.
- Rollback: revert the commit (redeploys the old path); restore the old file from
  the new one with `cp -p` if it was already removed.
- Related: razs_project `add-claude-oauth-token-file` (same layout, `~/secrets/claude/`).
