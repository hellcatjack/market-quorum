#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$project_root"
[[ -f .env.platform ]] || { echo ".env.platform is required" >&2; exit 2; }
set -a
source .env.platform
set +a

./scripts/verify_platform.sh
export TRADINGNG_RUN_REAL_DEEP=1
export PYTHONPATH="platform/src:gateway/src:TradingAgents${PYTHONPATH:+:$PYTHONPATH}"
.venv/bin/pytest integration_tests/test_platform_real_deep.py -q -m real
./scripts/backup_platform.sh
./scripts/backup_platform.sh --verify-only
