#!/usr/bin/env bash
# Nginx management for Linux
# Portable nginx в virtual_env/packages/nginx (сборка из официального tarball)

NGINX_VERSION='1.27.4'
NGINX_CONF_NAME='ergo_ms'
NGINX_SERVICE_NAME="$(ergo_service_name nginx)"

# shellcheck source=portable_archive.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/portable_archive.sh"

_nginx_unit_file() {
  local root="$1"
  echo "$root/core/deployment/wrappers/systemd/${NGINX_SERVICE_NAME}.service"
}

_nginx_unit_linked() {
  local root="$1"
  local unit_file
  unit_file="$(_nginx_unit_file "$root")"
  [[ -f "$unit_file" ]] || [[ -L "/etc/systemd/system/${NGINX_SERVICE_NAME}.service" ]]
}

_nginx_packages_dir() {
  local root="$1"
  echo "$root/virtual_env/packages/nginx"
}

_nginx_binary() {
  local root="$1"
  echo "$(_nginx_packages_dir "$root")/sbin/nginx"
}

_nginx_main_conf() {
  local root="$1"
  echo "$(_nginx_packages_dir "$root")/conf/nginx.conf"
}

_nginx_site_conf() {
  local root="$1"
  echo "$(_nginx_packages_dir "$root")/conf/${NGINX_CONF_NAME}.conf"
}

_nginx_read_env() {
  local root="$1"
  local env_file="$root/.env"
  [[ -f "$env_file" ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    if [[ "$line" =~ ^([A-Z_][A-Z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
      value="${value#\"}"
      value="${value%\"}"
      value="${value#\'}"
      value="${value%\'}"
      case "$key" in
        NGINX_ENABLED|NGINX_SERVER_NAME|NGINX_PUBLIC_HOST|NGINX_LISTEN_HOST|NGINX_LISTEN_PORT|NGINX_USE_HTTPS|NGINX_HOST_POLICY|NGINX_ALT_HOSTS|ERGO_SSL_CERT|ERGO_SSL_KEY|ERGO_TLS_EMAIL|ERGO_TLS_DOMAINS|ERGO_TLS_WEBROOT|ERGO_TLS_STAGING)
          export "$key=$value"
          ;;
      esac
    fi
  done < "$env_file"
}

_nginx_truthy() {
  local value
  value="$(echo "${1:-}" | tr '[:upper:]' '[:lower:]')"
  [[ "$value" == "1" || "$value" == "true" || "$value" == "yes" ]]
}

_nginx_should_use_ssl() {
  local explicit="${1:-false}"
  local port="${2:-${NGINX_LISTEN_PORT:-80}}"
  if [[ "$explicit" == "true" ]]; then
    return 0
  fi
  if [[ "$explicit" == "false" ]]; then
    return 1
  fi
  if _nginx_truthy "${NGINX_USE_HTTPS:-}"; then
    return 0
  fi
  [[ "$port" == "443" ]]
}

_nginx_warn_insecure_certs() {
  local root="$1"
  local cert="${ERGO_SSL_CERT:-}"
  local key="${ERGO_SSL_KEY:-}"
  if [[ -z "$cert" || -z "$key" ]]; then
    ergoms_console_from_root "$root" nginx_ssl_vars_missing yellow --stderr
    return 0
  fi
  if [[ "$cert" == *snakeoil* || "$key" == *snakeoil* ]]; then
    ergoms_console_from_root "$root" nginx_ssl_self_signed yellow --stderr
    return 0
  fi
  if [[ ! -f "$cert" ]]; then
    ergoms_console_from_root "$root" nginx_ssl_cert_missing yellow --stderr "path=$cert"
  fi
  if [[ ! -f "$key" ]]; then
    ergoms_console_from_root "$root" nginx_ssl_key_missing yellow --stderr "path=$key"
  fi
}

_nginx_sudo() {
  if [[ $(id -u) -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

_nginx_is_installed() {
  local root="$1"
  [[ -x "$(_nginx_binary "$root")" ]]
}

_nginx_wait_for_apt_locks() {
  local root="$1"
  local timeout="${2:-180}"
  local waited=0
  local lock_paths=(
    /var/lib/dpkg/lock-frontend
    /var/lib/dpkg/lock
    /var/cache/apt/archives/lock
    /var/lib/apt/lists/lock
  )

  if ! command -v fuser >/dev/null 2>&1; then
    return 0
  fi

  while (( waited < timeout )); do
    local busy=0
    local lock
    for lock in "${lock_paths[@]}"; do
      if [[ -e "$lock" ]] && fuser "$lock" >/dev/null 2>&1; then
        busy=1
        break
      fi
    done
    if (( busy == 0 )); then
      return 0
    fi
    if (( waited == 0 )); then
      ergoms_console_from_root "$root" nginx_apt_lock_wait yellow
    fi
    sleep 3
    waited=$((waited + 3))
  done

  ergoms_console_from_root "$root" nginx_apt_lock_timeout red --stderr "timeout=$timeout"
  ergoms_console_from_root "$root" nginx_apt_lock_hint yellow --stderr
  ergoms_console_from_root "$root" nginx_apt_lock_retry_cmd yellow --stderr
  ergoms_console_from_root "$root" nginx_apt_lock_check yellow --stderr
  return 1
}
