#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python -m venv "$project_root/.venv"
"$project_root/.venv/bin/python" -m pip install --upgrade pip
"$project_root/.venv/bin/python" -m pip install \
  -c "$project_root/constraints.txt" \
  -e "$project_root/gateway[dev]"
"$project_root/.venv/bin/python" -m pip install \
  -c "$project_root/constraints.txt" \
  -e "$project_root/TradingAgents[dev]"
"$project_root/.venv/bin/python" -m pip install \
  -c "$project_root/constraints.txt" \
  -e "$project_root/platform[dev]"
