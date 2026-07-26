# Contributing to MarketQuorum

Contributions should keep the Gateway, management platform, Web client, and
TradingAgents boundary independently understandable and testable.

## Development workflow

1. Create a focused branch from `main`.
2. Add or update tests before changing behavior.
3. Regenerate `web/src/api/schema.d.ts` after an API contract change.
4. Run the relevant focused tests, then the complete offline gate.
5. Keep commits small and describe the observable change in imperative form.

Useful commands:

```bash
PYTHONPATH=platform/src:gateway/src:TradingAgents .venv/bin/pytest \
  gateway/tests platform/tests/unit integration_tests -q
.venv/bin/ruff check gateway/src gateway/tests platform/src platform/tests scripts
.venv/bin/ruff format --check gateway/src gateway/tests platform/src platform/tests scripts
npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web run test -- --run
npm --prefix web run build
```

Database integration tests truncate their configured database. Point
`TRADINGNG_TEST_DATABASE_URL` only at a disposable test database.

## Generated contracts

After changing FastAPI routes or Pydantic contracts, run:

```bash
PYTHONPATH=platform/src .venv/bin/python scripts/export_openapi.py
npm --prefix web run api:generate
```

Commit the resulting `web/src/api/schema.d.ts` update with the API change.

## Privacy and repository hygiene

Never commit:

- `.env`, `.env.platform`, credentials, cookies, private keys, or bearer tokens;
- `reports/`, `var/`, databases, backups, Gateway audits, or assessment artifacts;
- Codex authentication/configuration directories or browser profiles;
- proprietary prompts, private market data, or user-entered portfolio details.

Use synthetic values in tests. A placeholder should be obviously fake and must
not share a prefix or length pattern with a live credential. Before submitting,
inspect `git diff --cached` and run a secret scanner with redacted output.

## TradingAgents boundary

Do not edit the pinned TradingAgents source as part of an unrelated platform
change. Keep integration behavior in the Gateway or MarketQuorum platform when
possible. Changes that require a new TradingAgents commit must be isolated,
tested, attributed to the upstream project, and explained in the pull request.
