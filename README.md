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

### LAN OpenAI-compatible API

The system Caddy exposes only `GET /openai/v1/models` and
`POST /openai/v1/chat/completions` to the physical LAN
`192.168.1.0/24`. Gateway itself remains on loopback. Install the final Caddy
configuration as root; when needed, the installer creates a 256-bit key in the
ignored, mode-`0600` `.env.gateway-lan` file:

```bash
sudo /app/devs/TradingNG/scripts/install_public_caddy.sh \
  --mode final --confirm-domain ushome.amycat.com
```

Configure LAN clients with:

```dotenv
OPENAI_BASE_URL=https://ushome.amycat.com/openai/v1
OPENAI_API_KEY=<value securely retrieved from .env.gateway-lan>
```

Discover the current physical models and their supported reasoning efforts,
then select a pair with an ordinary OpenAI client:

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://ushome.amycat.com/openai/v1",
    api_key="<LAN Gateway key>",
)
models = client.models.list()
completion = client.chat.completions.create(
    model="gpt-5.6-sol",
    reasoning_effort="high",
    messages=[{"role": "user", "content": "Analyze this data."}],
)
```

`/models` reports each physical model's `supported_reasoning_efforts` and
`default_reasoning_effort`. Omit `reasoning_effort` to use that physical
model's catalog default. Use `model="codex"` without `reasoning_effort` to
inherit the latest local Codex model and effort. `codex-fast` and `codex-slow`
are private TradingNG routes, not LAN model choices.

Retrieve the key only when distributing it through an approved internal secret
channel:

```bash
sudo sed -n 's/^CODEX_GATEWAY_LAN_API_KEY=//p' \
  /app/devs/TradingNG/.env.gateway-lan
```

Rotate the key and immediately invalidate the old value with:

```bash
sudo /app/devs/TradingNG/scripts/install_public_caddy.sh \
  --mode final --confirm-domain ushome.amycat.com --rotate-lan-api-key
```

Public, VPN, Docker, loopback, missing-key, and wrong-key requests are denied.
The key protects this local Codex Gateway; it must never be committed, pasted
into logs, copied to `.env.platform`, or used as an OpenAI account credential.
Caddy removes the LAN credential and all private TradingNG route headers before
proxying. TradingNG keeps using the keyless loopback API, although LAN and
assessment requests intentionally share Codex concurrency and account quota.

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

### Alpha Vantage research providers

When an Alpha Vantage research key is configured, newly admitted assessments use
Alpha Vantage exclusively for core prices, technical indicators, fundamentals,
news, and the deterministic verified-market snapshot. A rate limit delays and
retries the same Alpha request; it never falls back to Yahoo. Installations with
no Alpha research key may still use their explicit fallback configuration. Macro
data remains on FRED and prediction markets remain on Polymarket. TradingNG
freezes this external overlay into every immutable run snapshot without modifying
TradingAgents:

```dotenv
ALPHA_VANTAGE_API_KEY=replace-with-secret
TRADINGNG_RESEARCH_DATA_VENDOR_CHAIN=alpha_vantage,yfinance
```

The loopback Alpha Broker reads `ALPHA_VANTAGE_API_KEY`; TradingAgents research
Workers no longer contact the provider directly. Restart the Broker first and
then the scheduler and Workers after changing it. Only subsequently admitted
runs are affected, and run details retain the configured vendor snapshot.

### Official instrument names

For SEC-covered securities, `Instrument.name` is the current registered name
verified against SEC EDGAR's ticker index and company submissions API. The
platform preserves the SEC spelling and capitalization together with the CIK,
source URL, verification time, and refresh schedule. It does not translate,
expand, or replace the registered name with an Alpha Vantage, Yahoo, or other
vendor label.

When SEC cannot uniquely match a ticker and exchange, the UI safely displays the
ticker alone and the system status page reports the unresolved or conflicting
identity. Name resolution never blocks an assessment and does not consume Alpha
Vantage quota. Operators can run the idempotent backfill after deployment:

```bash
.venv/bin/tradingng-platform-name-backfill
```

Automated SEC requests require `TRADINGNG_SEC_USER_AGENT`; keep a deployment
identity in the private environment rather than committing personal contact
details.

### Outcome-validation providers

New validation jobs use `validation.v2`. Exact entry, exit, and maturity times
are frozen from the market calendar; price returns and total returns including
cash distributions are stored separately with provider, request fingerprint,
adapter, normalization, and data-quality provenance. Existing rows remain
`validation.v1` and are never silently recalculated.

Outcome validation has a separate provider configuration from assessment
research. An Alpha Vantage plan that supports `TIME_SERIES_DAILY_ADJUSTED` can
be enabled in `.env.platform`:

```dotenv
TRADINGNG_VALIDATION_PRICE_PROVIDERS=alphavantage,yfinance
TRADINGNG_ALPHA_VANTAGE_API_KEY=replace-with-secret
TRADINGNG_ALPHA_VANTAGE_REQUESTS_PER_MINUTE=75
TRADINGNG_ALPHA_VANTAGE_RETRY_ATTEMPTS=6
TRADINGNG_ALPHA_VANTAGE_RETRY_BASE_SECONDS=5
TRADINGNG_ALPHA_VANTAGE_RETRY_MAX_SECONDS=60
TRADINGNG_VALIDATION_PROVIDER_TIMEOUT_SECONDS=15
TRADINGNG_ALPHA_VANTAGE_BROKER_UTILIZATION=1.0
TRADINGNG_ALPHA_VANTAGE_BROKER_MAX_IN_FLIGHT=3
TRADINGNG_ALPHA_VANTAGE_BROKER_ADMISSION_QUEUE_LIMIT=6
TRADINGNG_ALPHA_VANTAGE_AUTO_RETRY_ATTEMPTS=2
```

The Alpha Vantage adapter reads as-traded OHLC, split coefficients, and cash
distributions before entering the common `prices.v1` normalization boundary.
With a validation key present, both validation generations use this adapter
exclusively. Research and validation share the same per-key global Broker. It
enforces a safe rate and in-flight cap, priority queue, identical-request
coalescing, and cache. A rate limit pauses the key globally and recovery uses one
probe request; Yahoo is never used as fallback. The System page shows safe RPM,
in-flight requests, queue age, recovery time, and cache counters. After bounded
request retries, rate and transient failures create at most two linked automatic
assessment attempts. The API key is never stored in logs, artifacts, snapshots,
or request fingerprints. Explicit validation retries are available through
`POST /api/v1/validations/{validation_id}/retry` and the MCP
`retry_validation` tool.

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

## Point-in-time report integrity

Historical assessments are checked against the information that was available
on their analysis date. The external platform layer date-bounds prices and news,
uses FRED vintages, blocks unsupported current-only snapshots, and filters
financial statements by their verified publication date. SEC submissions are
the primary publication source; Alpha Vantage `EARNINGS` dates are a
metadata-only fallback. This policy is fail-closed and does not change any file
under `TradingAgents/`.

Every succeeded run has one of four UI/API states:

- `safe`: the current policy found no known look-ahead exposure.
- `at_risk`: sealed evidence confirms that later information reached the run.
- `unknown`: evidence exists but cannot prove the data was available in time.
- `unassessed`: no current-policy audit has been persisted yet.

Only `safe` runs enter history-assisted memory and trusted accuracy aggregates.
The ledger keeps every original report and validation visible, but shows the
number of excluded at-risk and unknown/unassessed samples separately. An Admin
with `assessments:admin` and `assessments:submit` may create an independent
clean reassessment; it creates a new run linked to the original and never
overwrites the old Decision, Validation, evidence, or artifact.

Configure an installation identity for SEC requests without committing a
private address:

```dotenv
TRADINGNG_SEC_USER_AGENT=MarketQuorum/0.1 (+https://ushome.amycat.com)
```

After the additive migration, audit sealed historical runs in bounded,
restart-safe batches:

```bash
.venv/bin/alembic -c platform/alembic.ini upgrade head
.venv/bin/tradingng-platform-integrity-audit --limit 25
# Repeat until the command reports audited=0.
```

Use `--run-id UUID` for one run. Each completed run commits independently, so an
interrupted batch can be rerun safely. Operators should monitor the Alpha broker
queue and SEC health between batches. REST exposes the run verdict, summary and
clean action; MCP exposes the matching integrity resource and clean-reassessment
tool.

Rollback must stop new consumers first and may deploy the previous API/Worker
build while leaving the additive tables intact. Once integrity rows exist, do
not run the Alembic downgrade: preserving audit artifacts and verdicts is safer
than destructively removing production evidence.

## User administration and permission boundaries

Human accounts have exactly one formal realm role. Effective permissions are
recomputed on every request from both that role and the token scopes:

| Capability | Admin | User |
|---|---:|---:|
| Read, submit, cancel, and review assessments | Yes | Yes |
| Read/write validations and read artifacts | Yes | Yes |
| Read full system diagnostics | Yes | No |
| Change scheduler/model policy | Yes | No |
| Create and administer users | Yes | No |

Administrators use `/users` to search and page through accounts, inspect active
sessions, create users, edit profiles and roles, enable or disable access, reset
passwords, and force sign-out. Creation and reset generate a high-entropy
temporary password that is shown once and cleared from browser state when the
dialog closes. The user must change it at first sign-in. MarketQuorum never
stores password plaintext. Accounts are disabled rather than permanently
deleted so assessment ownership, reviews, and audit history remain attributable.
The signed-in administrator cannot remove their own access, and the last enabled
administrator cannot be disabled or demoted.

Keycloak remains authoritative for usernames, profiles, enabled state, formal
roles, credentials, and sessions. The platform uses the dedicated
`tradingng-user-admin` service account with least-privilege realm-management
roles (`query-users`, `view-users`, `manage-users`, and `view-realm`); the last
permission is required to resolve the `Admin` and `User` realm roles before
assignment. Runtime code does not use Keycloak bootstrap credentials. Configure
only the private, ignored `.env.platform` file:

```dotenv
TRADINGNG_KEYCLOAK_ADMIN_URL=http://127.0.0.1:18081
TRADINGNG_KEYCLOAK_ADMIN_REALM=tradingng
TRADINGNG_KEYCLOAK_ADMIN_CLIENT_ID=tradingng-user-admin
TRADINGNG_KEYCLOAK_ADMIN_CLIENT_SECRET=replace-with-secret
```

Browser sign-out clears the OAuth2 Proxy application session and immediately
starts a fresh OIDC authorization with `prompt=login`, so the browser displays
the login form even when a Keycloak SSO cookie remains. The redirect target is
fixed and same-origin, and no ID token is carried in the browser redirect.
Provider backend logout remains a best-effort operation.

Reconciliation is idempotent. Check drift before applying it and confirm a
second check is clean:

```bash
.venv/bin/python scripts/sync_keycloak_user_management.py --env-file .env.platform --check
.venv/bin/python scripts/sync_keycloak_user_management.py --env-file .env.platform --apply
.venv/bin/python scripts/sync_keycloak_user_management.py --env-file .env.platform --check
```

Ordinary users read only `/api/v1/assessments/admission-summary`, which exposes
safe queue/admission information. `/api/v1/system/*`, scheduling/model policy,
and `/api/v1/admin/users*` remain Admin-only. User administration is available
through versioned REST endpoints but deliberately has no MCP tools, preventing
temporary credentials from entering model context. This integration is entirely
outside `TradingAgents/`.

On application rollback, retain the Keycloak `User` role, all accounts, disabled
states, the additive identity-sync column, and audit events. Do not delete users,
roll passwords back, or regrant `system:read` to legacy roles merely to match an
older application build.

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
systemctl --user enable --now tradingng-platform-alpha-broker.service
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
