---
name: genapi-deep-test
description: Use when deeply testing the Genapi project with playwright-cli, headed browser observation, API/UI smoke coverage, release verification, or regression checks before shipping.
---

# Genapi Deep Test

## Purpose

Run a broad Genapi verification pass that combines automated tests, a temporary local service, headed Playwright browser testing, API permission checks, UI route smoke tests, and regression probes. Keep the browser open when the user wants to watch.

## Required Skills And Tools

- Use the `playwright-cli` skill for browser work.
- Use `superpowers:systematic-debugging` before fixing any discovered bug.
- Use `superpowers:test-driven-development` before production bug fixes.
- Use `superpowers:verification-before-completion` before claiming success.
- If the user explicitly asks for agent teams, split independent review or implementation work across agents. Do not spawn agents unless explicitly asked.

## Safety Rules

- Do not trigger real upstream image generation during deep tests unless the user explicitly asks. Test validation paths such as empty prompt, `n` bounds, missing image files, and quota behavior instead.
- Do not call real CPA or Sub2API remote services. Use localhost closed ports such as `127.0.0.1:9998` and `127.0.0.1:9999` to verify failure handling.
- Do not close a headed browser session if the user asked to watch it.
- Preserve unrelated dirty git changes. Inspect diffs before final summary.
- Use `uv run --with pytest pytest`, not bare `uv run pytest`, because pytest may not be installed in the project dependency set.

## Baseline Verification

Run these before or after browser testing, depending on whether you need a quick repro first:

```bash
uv run --with pytest pytest
bun test
bun run typecheck
bun run build
```

Run frontend commands from `web/`.

## Temporary Service

Prefer an isolated temp data directory:

```bash
tmpdir="$(mktemp -d /tmp/genapi-pw.XXXXXX)"
GENAPI_BASE_URL=http://127.0.0.1:8765 \
GENAPI_CONFIG_FILE="$tmpdir/config.json" \
GENAPI_USER_DATABASE_URL="sqlite:///$tmpdir/users.db" \
JWT_SECRET=playwright-headed-secret-with-at-least-32-bytes \
.venv/bin/python3 - <<'PY'
from pathlib import Path
import api.support
import uvicorn
from api.app import create_app

api.support.WEB_DIST_DIR = Path("web/out").resolve()
uvicorn.run(create_app(), host="127.0.0.1", port=8765, log_level="warning")
PY
```

Build `web/out` first with `bun run build` if static assets are missing. Check readiness with:

```bash
curl -fsS http://127.0.0.1:8765/version
lsof -nP -iTCP:8765 -sTCP:LISTEN
```

## Headed Browser Setup

Use a named session and headed Chrome:

```bash
playwright-cli -s=genapi-headed open --browser=chrome http://127.0.0.1:8765/login/
playwright-cli -s=genapi-headed resize 1280 900
playwright-cli -s=genapi-headed snapshot
```

If the session already exists, reuse it. Do not close it unless requested:

```bash
playwright-cli -s=genapi-headed close
```

## Setup Accounts

Create or reuse:

- Admin: `admin@example.com` / `AdminPass123!`
- User: `user@example.com` / `UserPass123!`

If setup is fresh, use `/setup/` or POST `/api/setup/admin`. Create the normal user from admin user management with a small image quota and concurrency.

## API Coverage Checklist

Check public endpoints:

- `GET /version`
- `GET /api/setup/status`
- `GET /api/public/settings`
- `GET /api/membership/plans`

Check unauthenticated protected endpoints return `401`:

- `/api/settings`
- `/api/accounts`
- `/api/auth/me`
- `/api/admin/users`
- `/api/admin/update/status`

Check normal user forbidden endpoints return `403`:

- `/api/settings`
- `/api/accounts`
- `/api/admin/users`
- `/api/admin/redeem-codes`
- `/api/admin/promo-codes`
- `/api/admin/update/status`

Check admin endpoints render or return `200`:

- `/api/settings`
- `/api/storage/info`
- `/api/accounts`
- `/api/accounts/export`, with `Cache-Control: no-store`
- `/api/cpa/pools`
- `/api/sub2api/servers`
- `/api/images`
- `/api/logs`
- `/api/admin/auth-settings`
- `/api/admin/users`
- `/api/admin/membership-plans`
- `/api/admin/redeem-codes`
- `/api/admin/promo-codes`
- `/api/admin/update/status`
- `/api/register`

Check removed legacy routes stay `404`:

- `/api`
- `/v1/models`
- `/auth/login`

## CRUD And Validation Coverage

Admin users:

- Create a temporary user.
- Login as that user.
- Disable it and verify login rejection.
- Re-enable and update quota/concurrency.
- Delete it and verify login rejection.
- Verify self-disable, self-demote, and self-delete are rejected.

Membership:

- Create a plan.
- Patch enabled/sort fields.
- Delete the plan.

Redeem codes:

- Generate image quota codes.
- Redeem one as normal user.
- Verify reuse is rejected.
- Verify redeem history has a record.
- Disable and delete an unused code.

Promo codes:

- Create one-use promo code.
- Temporarily set auth settings to allow registration without email verification.
- Register once with promo and verify quota.
- Verify second use is rejected.
- Restore auth settings in a `finally` style cleanup.

CPA/Sub2API:

- Empty form/API validation should return `400`.
- Create entries with localhost closed ports.
- Confirm secrets are not returned in JSON.
- Remote list failures should be contained as `502` and redacted.
- Delete test entries.

Accounts:

- Empty create/delete/update payloads should return `400`.
- Avoid importing real tokens unless explicitly requested.

Image API:

- Empty generation prompt should return `422`.
- Generation `n > 4` should return `422`.
- Edit missing file should return `400`.
- Edit `n > 4` should return `400`.

Static routes:

- `HEAD /login/`
- `HEAD /setup/`
- `HEAD /register/`
- `HEAD /admin/users/`
- `HEAD /image/`

These should return `200` for SPA/static routes. `405` indicates prefetch console errors.

## UI Route Coverage

Admin routes:

- `/admin/accounts/`
- `/admin/users/`
- `/admin/redeem-codes/`
- `/admin/promo-codes/`
- `/admin/membership-plans/`
- `/admin/images/`
- `/admin/logs/`
- `/admin/settings/`
- `/admin/register-machine/`
- Legacy admin-compatible routes: `/accounts/`, `/settings/`, `/logs/`, `/image-manager/`

User routes:

- `/image/`
- `/membership/`
- `/redeem/`
- Confirm user navigation to `/admin/users/` redirects away from admin area.

Mobile:

```bash
playwright-cli -s=genapi-headed resize 390 844
playwright-cli -s=genapi-headed goto http://127.0.0.1:8765/image/
playwright-cli -s=genapi-headed resize 1280 900
```

## Console And Network Review

Use:

```bash
playwright-cli -s=genapi-headed console
playwright-cli -s=genapi-headed network
```

Expected findings are only those deliberately triggered by validation tests, such as a contained `502` from closed-port CPA/Sub2API checks. Unexpected `405`, naked `500`, auth redirect loops, page errors, or leaked secrets require investigation.

## Bug Fix Loop

When a bug is found:

1. Reproduce it with the smallest browser/API action.
2. Identify root cause before changing code.
3. Add a failing regression test.
4. Implement the minimal fix.
5. Run the focused regression test.
6. Restart the temp service if browser verification needs new backend code.
7. Re-run the relevant headed browser probe.
8. Run full verification before final summary.

## Final Report

Include:

- Counts from automated tests and build.
- Browser session state and whether it remains open.
- Key UI/API areas covered.
- Bugs found and fixed, with file references.
- Any remaining risks, skipped real-upstream actions, or intentional console errors.
