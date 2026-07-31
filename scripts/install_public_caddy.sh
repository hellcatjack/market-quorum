#!/usr/bin/env bash
set -euo pipefail
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
mode=""
confirmed_domain=""
rotate_lan_api_key=0
temporary_secret=""

usage() {
  echo "usage: $0 --mode maintenance|final --confirm-domain ushome.amycat.com [--rotate-lan-api-key]" >&2
}

while (($#)); do
  case "$1" in
    --mode) mode="${2:?--mode maintenance|final requires a value}"; shift 2 ;;
    --confirm-domain) confirmed_domain="${2:?--confirm-domain ushome.amycat.com requires a value}"; shift 2 ;;
    --rotate-lan-api-key) rotate_lan_api_key=1; shift ;;
    *) usage; exit 2 ;;
  esac
done

[[ "$mode" == "maintenance" || "$mode" == "final" ]] || { usage; exit 2; }
[[ "$confirmed_domain" == "ushome.amycat.com" ]] || { usage; exit 2; }
[[ "$EUID" == 0 ]] || { echo "installer must run as root" >&2; exit 2; }
if ((rotate_lan_api_key)) && [[ "$mode" != "final" ]]; then
  echo "--rotate-lan-api-key requires --mode final" >&2
  exit 2
fi

case "$mode" in
  maintenance) source_config="$project_root/deploy/caddy/tradingng-maintenance.caddy" ;;
  final) source_config="$project_root/deploy/caddy/tradingng.caddy" ;;
esac

lan_env="$project_root/.env.gateway-lan"
dropin_source="$project_root/deploy/systemd/caddy-lan-openai.conf"
dropin_directory="/etc/systemd/system/caddy.service.d"
dropin_path="/etc/systemd/system/caddy.service.d/tradingng-lan-openai.conf"
main_config="/etc/caddy/Caddyfile"
sites_directory="/etc/caddy/sites-enabled"
site_config="/etc/caddy/sites-enabled/tradingng.caddy"
backup_directory="/etc/caddy/backups"
import_line="import /etc/caddy/sites-enabled/*.caddy"
stamp="$(date -u +%Y%m%dT%H%M%S)-$$"
main_backup="$backup_directory/Caddyfile.$stamp"
site_backup="$backup_directory/tradingng.caddy.$stamp"
dropin_backup="$backup_directory/tradingng-lan-openai.conf.$stamp"
had_site=0
had_dropin=0
dropin_changed=0
had_lan_env=0
old_lan_env_line=""
secret_changed=0
lan_key_state="not_required"
lan_key=""
lan_key_line=""
completed=0

[[ -f "$source_config" && -f "$main_config" ]] || {
  echo "Caddy source or system configuration is missing" >&2
  exit 2
}
if [[ "$mode" == "final" && ! -f "$dropin_source" ]]; then
  echo "Caddy LAN API drop-in is missing" >&2
  exit 2
fi

write_lan_env_line() {
  local line="$1"
  temporary_secret="$(mktemp "$project_root/.env.gateway-lan.XXXXXX")"
  printf '%s\n' "$line" >"$temporary_secret"
  chmod 0600 "$temporary_secret"
  mv -f -- "$temporary_secret" "$lan_env"
  temporary_secret=""
}

install -d -m 0755 "$sites_directory"
install -d -m 0700 "$backup_directory"
install -m 0600 "$main_config" "$main_backup"
if [[ -f "$site_config" ]]; then
  install -m 0600 "$site_config" "$site_backup"
  had_site=1
fi
if [[ -f "$dropin_path" ]]; then
  install -m 0600 "$dropin_path" "$dropin_backup"
  had_dropin=1
fi
if [[ "$mode" == "final" && -f "$lan_env" ]]; then
  old_lan_env_line="$(<"$lan_env")"
  had_lan_env=1
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
  if [[ -n "$temporary_secret" ]]; then
    unlink "$temporary_secret" 2>/dev/null || true
    temporary_secret=""
  fi
  if ((secret_changed)); then
    if ((had_lan_env)); then
      write_lan_env_line "$old_lan_env_line"
    else
      unlink "$lan_env" 2>/dev/null || true
    fi
  fi
  if ((dropin_changed)); then
    if ((had_dropin)); then
      install -d -m 0755 "$dropin_directory"
      install -m 0644 "$dropin_backup" "$dropin_path"
    else
      unlink "$dropin_path" 2>/dev/null || true
    fi
    systemctl daemon-reload >/dev/null 2>&1 || true
  fi
  caddy validate --config "$main_config" >/dev/null 2>&1 || true
  systemctl restart caddy >/dev/null 2>&1 || true
}
trap rollback EXIT

if [[ "$mode" == "final" ]]; then
  if ((rotate_lan_api_key)) || [[ ! -f "$lan_env" ]]; then
    lan_key="$(openssl rand -hex 32)"
    secret_changed=1
    write_lan_env_line "CODEX_GATEWAY_LAN_API_KEY=$lan_key"
    if ((had_lan_env)); then
      lan_key_state="rotated"
    else
      lan_key_state="generated"
    fi
  else
    lan_key_state="reused"
  fi
  [[ "$(stat -c '%a' "$lan_env")" == "600" ]] || {
    echo ".env.gateway-lan must have mode 0600" >&2
    exit 2
  }
  lan_key_line="$(grep -E '^CODEX_GATEWAY_LAN_API_KEY=[0-9a-f]{64}$' "$lan_env" || true)"
  [[ -n "$lan_key_line" && "$(wc -l <"$lan_env")" -eq 1 ]] || {
    echo ".env.gateway-lan is invalid" >&2
    exit 2
  }
  lan_key="${lan_key_line#*=}"
fi

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
if [[ "$mode" == "final" ]]; then
  install -d -m 0755 "$dropin_directory"
  install -m 0644 "$dropin_source" "$dropin_path"
else
  unlink "$dropin_path" 2>/dev/null || true
fi
dropin_changed=1
systemctl daemon-reload
if [[ "$mode" == "final" ]]; then
  CODEX_GATEWAY_LAN_API_KEY="$lan_key" caddy validate --config /etc/caddy/Caddyfile
else
  caddy validate --config /etc/caddy/Caddyfile
fi
systemctl restart caddy
completed=1
trap - EXIT
unset lan_key lan_key_line old_lan_env_line
echo "installed_public_caddy_mode=$mode"
echo "system_caddy_backup=$main_backup"
echo "lan_api_key_state=$lan_key_state"
