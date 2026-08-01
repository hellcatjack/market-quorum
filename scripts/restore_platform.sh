#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
backup_root="$(realpath -m "$project_root/var/backups")"
archive=""
confirmation=""

while (($#)); do
  case "$1" in
    --archive) archive="${2:?--archive requires a value}"; shift 2 ;;
    --confirm-restore) confirmation="${2:?--confirm-restore requires RESTORE}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$archive" ]] || { echo "--archive is required" >&2; exit 2; }
[[ "$confirmation" == "RESTORE" ]] || { echo "--confirm-restore RESTORE is required" >&2; exit 2; }
[[ -f "$archive" && ! -L "$archive" ]] || { echo "archive is invalid" >&2; exit 2; }
archive="$(realpath "$archive")"
case "$archive" in "$backup_root"/*) ;; *) echo "archive must be beneath $backup_root" >&2; exit 2 ;; esac

restore_id="$(date -u +%Y%m%dT%H%M%SZ)"
new_database="tradingng_restore_${restore_id//[^0-9A-Za-z]/_}"
staging="$(mktemp -d "$project_root/var/.restore.XXXXXX")"
candidate_data="$project_root/var/restore-$restore_id"
trap 'rm -rf -- "$staging"' EXIT

zstd -q -d -c -- "$archive" | tar -C "$staging" -xf -
[[ -f "$staging/manifest.json" && -f "$staging/SHA256SUMS" ]] || {
  echo "archive manifest is missing" >&2; exit 2;
}
(cd "$staging" && sha256sum -c SHA256SUMS)

manifest_version="$(jq -er '.version' "$staging/manifest.json")"
database_dialect="$(jq -er '.database_dialect' "$staging/manifest.json")"
database_dump="$(jq -er '.database_dump' "$staging/manifest.json")"
artifact_archive="$(jq -er '.artifact_archive' "$staging/manifest.json")"
[[ "$manifest_version" == 2 ]] || { echo "unsupported backup manifest" >&2; exit 2; }
[[ "$database_dump" == "database.dump" && "$artifact_archive" == "artifacts.tar.zst" ]] || {
  echo "backup manifest contains unexpected archive names" >&2; exit 2;
}

case "$database_dialect" in
  postgresql)
    compose=(docker compose --env-file "$project_root/.env.platform" -f "$project_root/deploy/compose.prod.yml")
    database_exists="$("${compose[@]}" exec -T postgres psql -U tradingng -d postgres -At -c \
      "SELECT 1 FROM pg_database WHERE datname = '$new_database'")"
    [[ -z "$database_exists" ]] || { echo "restore database already exists" >&2; exit 2; }
    "${compose[@]}" exec -T postgres createdb -U tradingng "$new_database"
    "${compose[@]}" exec -T postgres pg_restore -U tradingng -d "$new_database" \
      --clean --if-exists <"$staging/$database_dump"
    ;;
  mysql)
    [[ -f "$project_root/.env" ]] || { echo "MySQL environment is missing" >&2; exit 2; }
    set -a
    # shellcheck disable=SC1091
    source "$project_root/.env"
    set +a
    mysql_host="${DB_HOST%:*}"
    mysql_port="${DB_HOST##*:}"
    if [[ "$mysql_host" == "$mysql_port" ]]; then
      mysql_host="$DB_HOST"
      mysql_port=3306
    fi
    database_exists="$(MYSQL_PWD="$DB_PASSWORD" mysql --batch --skip-column-names \
      --host="$mysql_host" --port="$mysql_port" --user="$DB_USER" \
      --execute="SELECT 1 FROM information_schema.schemata WHERE schema_name = '$new_database'")"
    [[ -z "$database_exists" ]] || { echo "restore database already exists" >&2; exit 2; }
    MYSQL_PWD="$DB_PASSWORD" mysql --host="$mysql_host" --port="$mysql_port" \
      --user="$DB_USER" --execute="CREATE DATABASE \`$new_database\` CHARACTER SET $DB_CHARSET COLLATE $DB_COLLATE"
    MYSQL_PWD="$DB_PASSWORD" mysql --host="$mysql_host" --port="$mysql_port" \
      --user="$DB_USER" --default-character-set="$DB_CHARSET" "$new_database" \
      <"$staging/$database_dump"
    ;;
  *)
    echo "unsupported database dialect" >&2
    exit 2
    ;;
esac

mkdir -p -- "$candidate_data/artifacts"
zstd -q -d -c -- "$staging/$artifact_archive" | tar -C "$candidate_data/artifacts" -xf -

new_database_url="$(
  cd "$project_root"
  DATABASE_DIALECT="$database_dialect" DATABASE_NAME="$new_database" \
    TRADINGNG_DATABASE_URL="" PYTHONPATH=platform/src .venv/bin/python - <<'PY'
import os

from dotenv import dotenv_values
from sqlalchemy.engine import URL, make_url
from tradingng_platform.config import Settings

if os.environ["DATABASE_DIALECT"] == "mysql":
    url = make_url(Settings().database_url).set(database=os.environ["DATABASE_NAME"])
else:
    password = dotenv_values(".env.platform").get("TRADINGNG_POSTGRES_PASSWORD")
    if not password:
        raise SystemExit("PostgreSQL restore credentials are missing")
    url = URL.create(
        "postgresql+psycopg",
        username="tradingng",
        password=password,
        host="127.0.0.1",
        port=5432,
        database=os.environ["DATABASE_NAME"],
    )
print(url.render_as_string(hide_password=False))
PY
)"
TRADINGNG_DATABASE_URL="$new_database_url" .venv/bin/alembic -c platform/alembic.ini upgrade head
TRADINGNG_VERIFY_DATABASE_URL="$new_database_url" PYTHONPATH=platform/src \
  .venv/bin/python scripts/verify_artifacts.py \
  --artifact-root "$candidate_data/artifacts" --database-url-env TRADINGNG_VERIFY_DATABASE_URL

systemctl --user stop tradingng-platform-validation.service \
  tradingng-platform-workers.target \
  tradingng-platform-data-readiness.service \
  tradingng-platform-scheduler.service tradingng-platform-api.service || true
env_candidate="$staging/.env.platform.candidate"
awk '!/^TRADINGNG_DATABASE_URL=/ && !/^TRADINGNG_DATA_DIR=/' "$project_root/.env.platform" >"$env_candidate"
printf 'TRADINGNG_DATABASE_URL=%s\nTRADINGNG_DATA_DIR=%s\n' "$new_database_url" "$candidate_data" >>"$env_candidate"
chmod 600 "$env_candidate"
cp -p "$project_root/.env.platform" "$project_root/.env.platform.pre-restore-$restore_id"
mv -f -- "$env_candidate" "$project_root/.env.platform"
systemctl --user start tradingng-platform-api.service \
  tradingng-platform-data-readiness.service tradingng-platform-scheduler.service \
  tradingng-platform-workers.target \
  tradingng-platform-validation.service
echo "restored_database=$new_database"
echo "previous_environment=$project_root/.env.platform.pre-restore-$restore_id"
