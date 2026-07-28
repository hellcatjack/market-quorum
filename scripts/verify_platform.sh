#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$project_root"

mysql_test_name=""
mysql_env_file="${TRADINGNG_MYSQL_ENV_FILE:-$project_root/.env}"
platform_env_file="${TRADINGNG_PLATFORM_ENV_FILE:-$project_root/.env.platform}"

cleanup_mysql_test_database() {
  if [[ -n "$mysql_test_name" ]]; then
    PYTHONPATH=platform/src:scripts .venv/bin/python scripts/mysql_database.py \
      --env-file "$mysql_env_file" drop-test --name "$mysql_test_name" --confirm-drop "$mysql_test_name"
    mysql_test_name=""
  fi
}
trap cleanup_mysql_test_database EXIT

test_database_password="${TRADINGNG_POSTGRES_PASSWORD:-}"
if [[ -z "$test_database_password" && -f "$platform_env_file" ]]; then
  test_database_password="$(PLATFORM_ENV_FILE="$platform_env_file" .venv/bin/python - <<'PY'
import os

from dotenv import dotenv_values

print(dotenv_values(os.environ["PLATFORM_ENV_FILE"]).get("TRADINGNG_POSTGRES_PASSWORD") or "")
PY
)"
fi
test_database_password="${test_database_password:-tradingng}"
export TRADINGNG_TEST_DATABASE_URL="${TRADINGNG_TEST_DATABASE_URL:-postgresql+psycopg://tradingng:${test_database_password}@127.0.0.1:5432/tradingng_test}"
export PYTHONPATH="platform/src:gateway/src:TradingAgents:scripts${PYTHONPATH:+:$PYTHONPATH}"

systemctl --user is-active --quiet tradingng-codex-gateway.service
if systemctl --user list-unit-files 'tradingng-platform-api.service' --no-legend 2>/dev/null | grep -q tradingng; then
  systemctl --user is-active --quiet tradingng-platform-alpha-broker.service
  systemctl --user is-active --quiet tradingng-platform-api.service
  systemctl --user is-active --quiet tradingng-platform-scheduler.service
  systemctl --user is-active --quiet tradingng-platform-validation.service
fi

.venv/bin/pytest TradingAgents/tests/test_platform_events.py -q
.venv/bin/pytest gateway/tests -q
.venv/bin/pytest platform/tests/unit platform/tests/integration platform/tests/operations -q
.venv/bin/pytest integration_tests -q

[[ -f "$mysql_env_file" ]] || { echo "MySQL environment file is missing" >&2; exit 2; }
mysql_test_name="tradingng_test_$(openssl rand -hex 6)"
PYTHONPATH=platform/src:scripts .venv/bin/python scripts/mysql_database.py \
  --env-file "$mysql_env_file" create-test --name "$mysql_test_name"
mysql_test_url="$(PYTHONPATH=platform/src:scripts .venv/bin/python scripts/mysql_database.py \
  --env-file "$mysql_env_file" url --name "$mysql_test_name")"
TRADINGNG_TEST_DATABASE_URL="$mysql_test_url" PYTHONPATH=platform/src \
  .venv/bin/pytest platform/tests/integration -q
cleanup_mysql_test_database
.venv/bin/ruff check TradingAgents/tradingagents TradingAgents/tests gateway/src gateway/tests \
  platform/src platform/tests integration_tests scripts
.venv/bin/ruff format --check gateway/src gateway/tests platform/src platform/tests \
  integration_tests scripts

npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web run test -- --run
npm --prefix web run build
npm --prefix web audit --omit=dev

systemd-analyze --user verify systemd/user/*.service systemd/user/*.target
caddy validate --adapter caddyfile --config deploy/caddy/tradingng.caddy
caddy validate --adapter caddyfile --config deploy/caddy/tradingng-maintenance.caddy
TRADINGNG_POSTGRES_PASSWORD=config-check \
KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME=config-check \
KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD=config-check \
OAUTH2_PROXY_CLIENT_SECRET=config-check \
OAUTH2_PROXY_COOKIE_SECRET=01234567890123456789012345678901 \
TRADINGNG_API_CLIENT_SECRET=config-check \
TRADINGNG_MCP_CLIENT_SECRET=config-check \
TRADINGNG_INITIAL_ADMIN_USERNAME=config-check \
TRADINGNG_INITIAL_ADMIN_PASSWORD=config-check \
  docker compose -f deploy/compose.prod.yml config --quiet

if rg -n 'tradingng\.internal' .env.platform.example deploy/compose.prod.yml \
  deploy/oauth2-proxy.cfg deploy/keycloak/tradingng-realm.json deploy/caddy; then
  echo "active deployment configuration contains the retired private origin" >&2
  exit 2
fi

if [[ -f "$platform_env_file" ]]; then
  live_compose=(docker compose --env-file "$platform_env_file" -f deploy/compose.prod.yml)
  if "${live_compose[@]}" ps --services --status running 2>/dev/null | grep -qx keycloak; then
    .venv/bin/python scripts/sync_keycloak_public_urls.py --check \
      --env-file "$platform_env_file"
  fi
fi

configured_database_url="$(.venv/bin/python - <<'PY'
from tradingng_platform.config import Settings

print(Settings().database_url)
PY
)"
configured_artifact_root="$(.venv/bin/python - <<'PY'
from tradingng_platform.config import Settings

print(Settings().artifact_dir)
PY
)"
if [[ -d "$configured_artifact_root" ]]; then
  TRADINGNG_VERIFY_DATABASE_URL="$configured_database_url" \
    .venv/bin/python scripts/verify_artifacts.py \
    --artifact-root "$configured_artifact_root" \
    --database-url-env TRADINGNG_VERIFY_DATABASE_URL
fi

git diff --check
echo "TradingNG offline verification passed"
