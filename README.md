# MarketQuorum

[简体中文](README.zh-CN.md)

MarketQuorum is an auditable, multi-agent investment research and assessment
platform. It connects a locally authenticated Codex CLI to TradingAgents,
coordinates capacity-aware research jobs, and preserves decisions, evidence,
reports, model settings, reviews, and later performance validation in one
multi-user system.

> MarketQuorum and TradingAgents are research software. Their output is not
> financial, investment, trading, legal, or tax advice. The system does not
> place orders.

## What it provides

- An OpenAI-compatible Chat Completions Gateway backed by the local Codex CLI.
- Web dispatch and monitoring for stock, ETF, and cryptocurrency assessments.
- Trusted instrument classification and asset-specific analyst selection.
- Queue-aware concurrency from 1 to 32 assessments, with per-ticker exclusion.
- CPU, memory, disk, Gateway-capacity, vendor, and circuit-breaker admission
  guards.
- Immutable run history, complete reports, evidence, artifacts, LLM interaction
  metadata, comments, reviews, and performance validation.
- Optional history-assisted assessments that use only same-ticker records whose
  outcome validation completed before the new analysis date.
- Versioned REST APIs, Streamable HTTP/stdio MCP, API credentials, SSE events,
  and signed outbound webhooks.
- OIDC/PKCE browser authentication and role/scope authorization for internal
  multi-user deployments.

## Architecture

```text
Browser / REST clients / MCP clients
                 |
        Caddy + OAuth2 Proxy
                 |
   FastAPI management platform + MySQL
        |          |            |
   Scheduler   Worker pool   Validation
        |          |
        |     isolated Runner
        |          |
        +---- Codex Gateway ---- local Codex app-server
                         |
                 pinned TradingAgents
```

The Gateway binds only to loopback and resolves Codex's current model and
reasoning effort before each request. The platform snapshots those values with
the TradingAgents revision, prompt schema, data vendors, and tool vendors so a
later reviewer can reconstruct how a conclusion was produced.

## Repository layout

| Path | Responsibility |
|---|---|
| `gateway/` | Minimal OpenAI-compatible Codex Gateway and optional audit proxy |
| `platform/` | FastAPI API, scheduler, workers, MCP, persistence, and validation |
| `web/` | React management application |
| `TradingAgents/` | Pinned upstream research engine Git dependency |
| `deploy/` | Docker Compose, Keycloak, OAuth2 Proxy, and Caddy reference deployment |
| `systemd/user/` | Reference user services and 32-instance Worker target |
| `scripts/` | Bootstrap, verification, backup, restore, migration, and diagnostics |
| `integration_tests/` | Cross-component and real-acceptance tests |

## Requirements

- Linux
- Python 3.10+
- Node.js 22+ and npm
- Codex CLI 0.145.0+ with a local ChatGPT login
- Git with submodule support
- Docker/Compose for the identity and development database services
- MySQL 8 for the current production platform configuration

Confirm Codex is available before installation:

```bash
codex --version
codex login status
```

## Clone and bootstrap

```bash
git clone --recurse-submodules git@github.com:hellcatjack/market-quorum.git
cd market-quorum
./scripts/bootstrap.sh
npm --prefix web ci
```

If the repository was cloned without dependencies:

```bash
git submodule update --init --recursive
```

The active `.env`, `.env.platform`, `var/`, and `reports/` paths are ignored.
Never copy a real environment file, assessment report, database, or Gateway
audit into a commit.

## Start the Codex Gateway

The foreground development command is:

```bash
./scripts/run_gateway.sh
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/v1/models
```

The public model alias is always `codex`. Before every completion, the Gateway
reads the effective Codex model and reasoning effort and pins both to the
request. The private app-server is started with
`mcp_servers.playwright.enabled=false` to prevent assessment requests from
accumulating Playwright subprocesses. This override does not edit the user's
Codex configuration and does not affect ordinary Codex sessions.

Codex runs with a read-only filesystem sandbox and `networkAccess=true` for
research. Network content is untrusted; read-only filesystem access does not
make otherwise readable files secret. Keep credentials outside any path the
Codex account can read.

Gateway turn deadlines are controlled by
`CODEX_GATEWAY_REQUEST_TIMEOUT_SECONDS`. An unset value or `0` allows a healthy
turn to run without a wall-clock deadline; a positive integer enables an
explicit emergency limit. `/internal/status` reports the oldest active turn and
the longest time since progress so operators can alert on inactivity without
interrupting legitimate high-reasoning work. Correlated turn lifecycle and
redacted, bounded app-server diagnostics are available in the Gateway journal:

```bash
curl http://127.0.0.1:8000/internal/status
journalctl --user-unit tradingng-codex-gateway.service --follow
```

## Connect TradingAgents

Copy the local Gateway example into the ignored active environment file:

```bash
cp .env.tradingagents.example .env
```

The required values are:

```dotenv
TRADINGAGENTS_LLM_PROVIDER=openai_compatible
TRADINGAGENTS_DEEP_THINK_LLM=codex
TRADINGAGENTS_QUICK_THINK_LLM=codex
TRADINGAGENTS_LLM_BACKEND_URL=http://127.0.0.1:8000/v1
OPENAI_COMPATIBLE_API_KEY=local
```

`OPENAI_COMPATIBLE_API_KEY=local` is only a placeholder required by the client
library; it is not an OpenAI or Codex credential. Start the CLI with:

```bash
.venv/bin/tradingagents
```

## Run the management platform locally

Start the disposable PostgreSQL development service and apply migrations:

```bash
docker compose -f deploy/compose.dev.yml up -d postgres
docker compose -f deploy/compose.dev.yml exec postgres \
  createdb -U tradingng tradingng_test
export TRADINGNG_DATABASE_URL=postgresql+psycopg://tradingng:tradingng@127.0.0.1:5432/tradingng
.venv/bin/alembic -c platform/alembic.ini upgrade head
```

Run the components in separate terminals with
`PYTHONPATH=platform/src:TradingAgents`:

```bash
.venv/bin/tradingng-platform-api
.venv/bin/tradingng-platform-scheduler
TRADINGNG_WORKER_INSTANCE=1 .venv/bin/tradingng-platform-worker
.venv/bin/tradingng-platform-validation
```

Run the Web client:

```bash
.venv/bin/python scripts/export_openapi.py
npm --prefix web run api:generate
npm --prefix web run dev
```

The API listens on `127.0.0.1:8010`. Liveness and readiness are available at
`/health/live` and `/health/ready`; authenticated business routes use
`/api/v1`.

## Scheduling and concurrency

New installations default to two concurrent assessments. An administrator can
set the active limit from 1 to 32 on the System page. A fixed pool of 32
lightweight Workers makes every permitted value executable; idle Workers do not
start Codex requests.

The saved active limit is not the only admission condition. New work pauses
when the Gateway activity threshold is reached, CPU remains above its limit,
available memory or disk is too low, a data vendor circuit is open, or the same
canonical ticker is already active. Existing work is not cancelled when a
guard closes admission.

## Independent and history-assisted assessments

New jobs default to `independent`, so an earlier conclusion cannot influence
the new research. The Web form, REST API, and MCP tools can explicitly request
`memory_mode=historical`. At admission time, the scheduler selects at most five
prior assessments of the same ticker. It uses only the highest matured
validation horizon from each prior run and requires its validation exit session
to be strictly earlier than the new analysis date, preventing look-ahead.

Source run and validation IDs, returns, alpha, and content hashes are pinned in
the immutable run snapshot and materialized into a TradingAgents memory file
private to that job. Concurrent jobs never share memory files. Run details show
the historical sources in a collapsed traceability view with links to the
source runs. Existing jobs and legacy snapshots without memory metadata remain
independent. This integration lives entirely in the platform layer and does not
modify the TradingAgents submodule.

## REST, MCP, events, and webhooks

REST and Web use the same application services and immutable records. The MCP
server implements stateless Streamable HTTP at `/mcp`; local authenticated
clients can use the stdio transport:

```bash
export TRADINGNG_MCP_TOKEN='short-lived-oidc-service-token'
.venv/bin/python scripts/inspect_mcp.py \
  --url http://127.0.0.1:8010/mcp \
  --token-env TRADINGNG_MCP_TOKEN
TRADINGNG_MCP_TOKEN="$TRADINGNG_MCP_TOKEN" \
  .venv/bin/tradingng-platform-mcp-stdio
```

MCP submission and control tools return immediately; clients follow queued
work through status tools, resources, REST, or SSE. Webhook secrets are
encrypted at rest, webhook targets are protected against DNS rebinding/SSRF,
and delivery retries do not change assessment state.

## Deployment reference

Files under `deploy/` and `systemd/user/` describe the original deployment and
contain an example public domain and the checkout path `/app/devs/TradingNG`.
They contain no live credentials. Replace the domain, paths, certificate
assumptions, and every empty/example secret before using them elsewhere.

Build and verify before enabling services:

```bash
npm --prefix web run build
PYTHONPATH=platform/src .venv/bin/alembic -c platform/alembic.ini upgrade head
./scripts/verify_platform.sh
```

The reference user-service lifecycle is:

```bash
systemctl --user disable --now tradingng-platform-caddy.service
systemctl --user link "$PWD"/systemd/user/tradingng-platform-*.service
systemctl --user link "$PWD"/systemd/user/tradingng-platform-workers.target
systemctl --user daemon-reload
systemctl --user enable --now tradingng-platform-containers.service
systemctl --user enable --now tradingng-platform-api.service
systemctl --user enable --now tradingng-platform-scheduler.service
systemctl --user enable --now tradingng-platform-workers.target
systemctl --user enable --now tradingng-platform-validation.service
```

The Gateway remains a separate loopback-only service and is not routed by
public Caddy. Backups and restores are explicit:

Before reloading or restarting the Gateway, wait until no assessment Runner is
present and two consecutive status snapshots report `active_completions: 0`.
The reference unit uses `TimeoutStopSec=infinity` so an ordinary systemd stop
can wait for an in-flight request instead of killing it at the default stop
deadline. Do not activate new Gateway code while either activity check is
nonzero.

```bash
ps -eo pid,cmd | rg 'tradingng_platform.runner.cli' | rg -v 'rg '
curl http://127.0.0.1:8000/internal/status
curl http://127.0.0.1:8000/internal/status
```

```bash
./scripts/backup_platform.sh
./scripts/backup_platform.sh --verify-only
./scripts/restore_platform.sh \
  --archive "$PWD/var/backups/tradingng-YYYYMMDDTHHMMSSZ.tar.zst" \
  --confirm-restore RESTORE
```

## Verification

The offline suite uses fake Codex responses and synthetic data; it does not
consume Codex quota:

```bash
PYTHONPATH=platform/src:gateway/src:TradingAgents .venv/bin/pytest \
  TradingAgents/tests/test_platform_events.py \
  gateway/tests platform/tests/unit integration_tests -q
.venv/bin/ruff check gateway/src gateway/tests platform/src platform/tests scripts
.venv/bin/ruff format --check gateway/src gateway/tests platform/src platform/tests scripts
npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web run test -- --run
npm --prefix web run build
systemd-analyze --user verify systemd/user/*.service systemd/user/*.target
```

Run `./scripts/verify_platform.sh` for the complete database/deployment gate.
The explicit real-Codex check consumes account allowance:

```bash
.venv/bin/python scripts/smoke_gateway.py
```

## Diagnostic audits

The optional loopback audit proxy records full request and response payloads
for a deliberately selected run. Store its output only beneath ignored
`reports/` paths. Those files can contain proprietary prompts, tool arguments,
market data, and user information and must never be published.

## Security and contribution

See [SECURITY.md](SECURITY.md) for private vulnerability reporting and
[CONTRIBUTING.md](CONTRIBUTING.md) for tests and privacy requirements. Public
examples contain placeholders only. Never commit authentication files, tokens,
cookies, private keys, databases, backups, or assessment artifacts.

## License

MarketQuorum is released under the [MIT License](LICENSE). Third-party
components retain their own licenses; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Upstream attribution

MarketQuorum is built around
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents).
The pinned dependency retains its upstream Apache License 2.0 and attribution;
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
