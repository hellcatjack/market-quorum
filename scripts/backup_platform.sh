#!/usr/bin/env bash
set -euo pipefail
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
backup_root="$project_root/var/backups"
verify_only=0

while (($#)); do
  case "$1" in
    --verify-only) verify_only=1; shift ;;
    --backup-root) backup_root="${2:?--backup-root requires a value}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

expected_root="$(realpath -m "$project_root/var/backups")"
resolved_root="$(realpath -m "$backup_root")"
case "$resolved_root" in
  "$expected_root"|"$expected_root"/*) ;;
  *) echo "backup root must be beneath $expected_root" >&2; exit 2 ;;
esac
if [[ -L "$backup_root" ]]; then
  echo "backup root must not be a symbolic link" >&2
  exit 2
fi
mkdir -p -- "$resolved_root"

verify_archive() {
  local archive="$1"
  [[ -f "$archive" && ! -L "$archive" ]] || { echo "backup archive is invalid" >&2; return 2; }
  local archive_path
  archive_path="$(realpath "$archive")"
  case "$archive_path" in "$resolved_root"/*) ;; *) echo "archive escapes backup root" >&2; return 2 ;; esac
  local verify_dir
  verify_dir="$(mktemp -d "$resolved_root/.verify.XXXXXX")"
  trap 'rm -rf -- "$verify_dir"' RETURN
  zstd -q -d -c -- "$archive_path" | tar -C "$verify_dir" -xf -
  [[ -f "$verify_dir/manifest.json" && -f "$verify_dir/SHA256SUMS" ]] || {
    echo "backup manifest is missing" >&2; return 2;
  }
  (cd "$verify_dir" && sha256sum -c SHA256SUMS)
  echo "verified=$archive_path"
}

if ((verify_only)); then
  latest=$(find "$resolved_root" -maxdepth 1 -type f -name 'tradingng-*.tar.zst' \
    -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d ' ' -f 2-)
  [[ -n "$latest" ]] || { echo "no backup archive found" >&2; exit 2; }
  verify_archive "$latest"
  exit 0
fi

data_dir="${TRADINGNG_DATA_DIR:-$project_root/var}"
artifact_root="$(realpath -m "$data_dir/artifacts")"
[[ "$artifact_root" == "$(realpath -m "$project_root/var")"/* ]] || {
  echo "artifact root must be beneath project var" >&2; exit 2;
}

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
cutoff="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
staging="$(mktemp -d "$resolved_root/.staging.XXXXXX")"
trap 'rm -rf -- "$staging"' EXIT

settings_json="$(
  cd "$project_root"
  PYTHONPATH=platform/src .venv/bin/python - <<'PY'
import json

from sqlalchemy.engine import make_url
from tradingng_platform.config import Settings

settings = Settings()
url = make_url(settings.database_url)
print(json.dumps({
    "database_dialect": settings.database_dialect,
    "database": url.database,
    "username": url.username,
    "host": url.host,
    "port": url.port,
}))
PY
)"
database_dialect="$(jq -er '.database_dialect' <<<"$settings_json")"
database_name="$(jq -er '.database' <<<"$settings_json")"
database_user="$(jq -er '.username' <<<"$settings_json")"
database_host="$(jq -er '.host' <<<"$settings_json")"
database_port="$(jq -er '.port' <<<"$settings_json")"

case "$database_dialect" in
  postgresql)
    compose=(docker compose --env-file "$project_root/.env.platform" -f "$project_root/deploy/compose.prod.yml")
    "${compose[@]}" exec -T postgres pg_dump -U "$database_user" -d "$database_name" \
      --format=custom >"$staging/database.dump"
    "${compose[@]}" exec -T postgres psql -U "$database_user" -d "$database_name" \
      -At -F $'\t' -c \
      "SELECT storage_key, sha256 FROM artifacts WHERE deleted_at IS NULL AND created_at <= '$cutoff'::timestamptz ORDER BY storage_key" \
      >"$staging/artifact-files.tsv"
    ;;
  mysql)
    [[ -f "$project_root/.env" ]] || { echo "MySQL environment is missing" >&2; exit 2; }
    set -a
    # shellcheck disable=SC1091
    source "$project_root/.env"
    set +a
    MYSQL_PWD="$DB_PASSWORD" mysqldump --single-transaction --routines --triggers \
      --no-tablespaces --host="$database_host" --port="$database_port" \
      --user="$database_user" --default-character-set="$DB_CHARSET" \
      "$database_name" >"$staging/database.dump"
    mysql_cutoff="${cutoff/T/ }"
    mysql_cutoff="${mysql_cutoff%Z}"
    MYSQL_PWD="$DB_PASSWORD" mysql --batch --skip-column-names \
      --host="$database_host" --port="$database_port" --user="$database_user" \
      --default-character-set="$DB_CHARSET" "$database_name" \
      --execute="SELECT storage_key, sha256 FROM artifacts WHERE deleted_at IS NULL AND created_at <= '$mysql_cutoff' ORDER BY storage_key" \
      >"$staging/artifact-files.tsv"
    ;;
  *)
    echo "unsupported database dialect" >&2
    exit 2
    ;;
esac

: >"$staging/artifact-paths.txt"
while IFS=$'\t' read -r storage_key expected_sha; do
  [[ -n "$storage_key" ]] || continue
  case "$storage_key" in /*|*..*) echo "unsafe artifact key" >&2; exit 2 ;; esac
  artifact_path="$(realpath -m "$artifact_root/$storage_key")"
  [[ "$artifact_path" == "$artifact_root"/* && -f "$artifact_path" ]] || {
    echo "artifact is missing or outside the store" >&2; exit 2;
  }
  [[ "$(sha256sum "$artifact_path" | awk '{print $1}')" == "$expected_sha" ]] || {
    echo "artifact hash mismatch" >&2; exit 2;
  }
  printf '%s\n' "$storage_key" >>"$staging/artifact-paths.txt"
done <"$staging/artifact-files.tsv"

tar -C "$artifact_root" -T "$staging/artifact-paths.txt" -cf - | zstd -q -T0 -o "$staging/artifacts.tar.zst"
jq -n --arg cutoff "$cutoff" --arg database "$database_name" \
  --arg database_dialect "$database_dialect" --arg database_dump "database.dump" \
  --arg artifacts "artifacts.tar.zst" --arg artifact_index "artifact-files.tsv" \
  '{version:2,database_dialect:$database_dialect,database:$database,cutoff_utc:$cutoff,database_dump:$database_dump,artifact_archive:$artifacts,artifact_index:$artifact_index}' \
  >"$staging/manifest.json"
(cd "$staging" && sha256sum database.dump artifacts.tar.zst artifact-files.tsv manifest.json >SHA256SUMS)

archive="$resolved_root/tradingng-$stamp.tar.zst"
tar -C "$staging" -cf - database.dump artifacts.tar.zst artifact-files.tsv artifact-paths.txt manifest.json SHA256SUMS \
  | zstd -q -T0 -o "$archive"
verify_archive "$archive"
echo "created=$archive"
