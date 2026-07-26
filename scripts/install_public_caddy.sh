#!/usr/bin/env bash
set -euo pipefail
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
mode=""
confirmed_domain=""

usage() {
  echo "usage: $0 --mode maintenance|final --confirm-domain ushome.amycat.com" >&2
}

while (($#)); do
  case "$1" in
    --mode) mode="${2:?--mode maintenance|final requires a value}"; shift 2 ;;
    --confirm-domain) confirmed_domain="${2:?--confirm-domain ushome.amycat.com requires a value}"; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done

[[ "$mode" == "maintenance" || "$mode" == "final" ]] || { usage; exit 2; }
[[ "$confirmed_domain" == "ushome.amycat.com" ]] || { usage; exit 2; }
[[ "$EUID" == 0 ]] || { echo "installer must run as root" >&2; exit 2; }

case "$mode" in
  maintenance) source_config="$project_root/deploy/caddy/tradingng-maintenance.caddy" ;;
  final) source_config="$project_root/deploy/caddy/tradingng.caddy" ;;
esac

main_config="/etc/caddy/Caddyfile"
sites_directory="/etc/caddy/sites-enabled"
site_config="/etc/caddy/sites-enabled/tradingng.caddy"
backup_directory="/etc/caddy/backups"
import_line="import /etc/caddy/sites-enabled/*.caddy"
stamp="$(date -u +%Y%m%dT%H%M%S)-$$"
main_backup="$backup_directory/Caddyfile.$stamp"
site_backup="$backup_directory/tradingng.caddy.$stamp"
had_site=0
completed=0

[[ -f "$source_config" && -f "$main_config" ]] || {
  echo "Caddy source or system configuration is missing" >&2
  exit 2
}

install -d -m 0755 "$sites_directory"
install -d -m 0700 "$backup_directory"
install -m 0600 "$main_config" "$main_backup"
if [[ -f "$site_config" ]]; then
  install -m 0600 "$site_config" "$site_backup"
  had_site=1
fi

rollback() {
  if ((completed)); then
    return
  fi
  install -m 0644 "$main_backup" "$main_config"
  if ((had_site)); then
    install -m 0644 "$site_backup" "$site_config"
  else
    unlink "$site_config" 2>/dev/null || true
  fi
  caddy validate --config "$main_config" >/dev/null 2>&1 || true
  systemctl reload caddy >/dev/null 2>&1 || true
}
trap rollback EXIT

import_count="$(grep -Fxc "$import_line" "$main_config" || true)"
if ((import_count > 1)); then
  echo "Caddy sites import occurs more than once" >&2
  exit 2
fi
if ((import_count == 0)); then
  temporary_main="$(mktemp "$backup_directory/Caddyfile.install.XXXXXX")"
  {
    sed -e '$a\' "$main_config"
    printf '%s\n' "$import_line"
  } >"$temporary_main"
  install -m 0644 "$temporary_main" "$main_config"
  unlink "$temporary_main"
fi

install -m 0644 "$source_config" "$site_config"
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
completed=1
trap - EXIT
echo "installed_public_caddy_mode=$mode"
echo "system_caddy_backup=$main_backup"
