#!/usr/bin/env bash
# Redis management for Linux
# Portable Redis в virtual_env/packages/redis (сборка из исходников)

REDIS_SERVICE_NAME="$(ergo_service_name redis)"
REDIS_UNIT_PATH="/etc/systemd/system/${REDIS_SERVICE_NAME}.service"

_sync_redis_service_name() {
  local root="${1:-}"
  REDIS_SERVICE_NAME="$(ergo_service_name redis "$root")"
  REDIS_UNIT_PATH="/etc/systemd/system/${REDIS_SERVICE_NAME}.service"
}

_redis_dir() {
  local root="$1"
  echo "$root/virtual_env/packages/redis"
}

_redis_server() {
  local root="$1"
  local dir
  dir="$(_redis_dir "$root")"
  if [[ -x "$dir/bin/redis-server" ]]; then
    echo "$dir/bin/redis-server"
    return 0
  fi
  echo "$dir/bin/redis-server"
}

_redis_cli() {
  local root="$1"
  echo "$(_redis_dir "$root")/bin/redis-cli"
}

_redis_conf() {
  local root="$1"
  echo "$(_redis_dir "$root")/conf/redis.conf"
}

_redis_log_path() {
  local root="$1"
  local py="$root/virtual_env/python/bin/python"
  local script="$root/core/deployment/scripts/log_env.py"
  if [[ -x "$py" && -f "$script" ]]; then
    local path
    path="$("$py" "$script" path REDIS "$root" 2>/dev/null || true)"
    [[ -n "$path" ]] && echo "$path" && return 0
  fi
  echo "$root/logs/redis.log"
}

_redis_pidfile() {
  local root="$1"
  echo "$(_redis_dir "$root")/run/redis.pid"
}

_redis_read_port() {
  local root="$1"
  local conf port
  conf="$(_redis_conf "$root")"
  port="${REDIS_PORT:-6379}"
  if [[ -f "$conf" ]]; then
    local parsed
    parsed="$(grep -E '^port[[:space:]]+' "$conf" | awk '{print $2}' | tail -n1)"
    [[ -n "$parsed" ]] && port="$parsed"
  fi
  echo "$port"
}

_redis_read_password() {
  local root="$1"
  local conf line
  conf="$(_redis_conf "$root")"
  [[ -f "$conf" ]] || return 0
  line="$(grep -E '^requirepass[[:space:]]+' "$conf" | tail -n1 || true)"
  [[ -n "$line" ]] || return 0
  # requirepass secret  |  requirepass "secret"
  line="${line#requirepass}"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%\"}"
  line="${line#\"}"
  printf '%s' "$line"
}

_redis_cli_shutdown() {
  local root="$1"
  local cli port pass
  cli="$(_redis_cli "$root")"
  port="$(_redis_read_port "$root")"
  pass="$(_redis_read_password "$root")"
  # -c у redis-cli — cluster mode, не путь к конфигу (как на Windows).
  if [[ -n "$pass" ]]; then
    "$cli" -h 127.0.0.1 -p "$port" -a "$pass" --no-auth-warning shutdown
  else
    "$cli" -h 127.0.0.1 -p "$port" shutdown
  fi
}

_redis_remove_stale_pidfile() {
  local root="$1"
  local pidfile pid
  pidfile="$(_redis_pidfile "$root")"
  [[ -f "$pidfile" ]] || return 0
  pid="$(tr -d '[:space:]' < "$pidfile")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  rm -f "$pidfile"
}

_redis_python() {
  local root="$1"
  local py="$root/virtual_env/python/bin/python"
  if [[ -x "$py" ]]; then
    echo "$py"
    return 0
  fi
  command -v python3
}

_redis_read_env() {
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
        REDIS_ENABLED|REDIS_HOST|REDIS_PORT|REDIS_DB_CACHE|REDIS_DB_CHANNEL)
          export "$key=$value"
          ;;
      esac
    fi
  done < "$env_file"
}

_redis_is_installed() {
  local root="$1"
  [[ -x "$(_redis_server "$root")" ]] && [[ -f "$(_redis_conf "$root")" ]]
}

_redis_run_install_script() {
  local root="$1"
  local port="${2:-6379}"
  local py
  py="$(_redis_python "$root")"
  if [[ -z "$py" ]]; then
    write_ergoms_message python_not_found_setup red --stderr
    return 1
  fi
  "$py" "$root/core/deployment/scripts/install_redis.py" --root "$root" --port "$port"
  restore_project_ownership "$root" "$(_redis_dir "$root")"
}

_redis_ping() {
  local root="$1"
  local py cli conf port
  py="$(_redis_python "$root")"
  if [[ -n "$py" && -x "$py" ]]; then
    "$py" "$root/core/deployment/scripts/install_redis.py" --root "$root" --ping-only >/dev/null 2>&1
    return $?
  fi
  cli="$(_redis_cli "$root")"
  conf="$(_redis_conf "$root")"
  port="$(_redis_read_port "$root")"
  [[ -x "$cli" ]] || return 1

  if "$cli" -h 127.0.0.1 -p "$port" ping 2>/dev/null | grep -q PONG; then
    return 0
  fi
  if [[ -f "$conf" ]] && "$cli" -h 127.0.0.1 -p "$port" ping 2>/dev/null | grep -q PONG; then
    return 0
  fi
  return 1
}

_redis_wait_for_ping() {
  local root="$1"
  local attempts="${2:-40}"
  local i
  for ((i = 1; i <= attempts; i++)); do
    if _redis_ping "$root"; then
      return 0
    fi
    sleep 0.1
  done
  return 1
}

_redis_unit_content() {
  local root="$1"
  local server conf
  server="$(_redis_server "$root")"
  conf="$(_redis_conf "$root")"
  cat <<UNIT
[Unit]
Description=Ergo MS Redis (portable packages)
After=network.target

[Service]
Type=forking
EnvironmentFile=-__ERGO_MS_ENV__
ExecStart=$server $conf
ExecStop=/bin/kill -s TERM \$MAINPID
PIDFile=$(_redis_dir "$root")/run/redis.pid
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
}

_redis_use_systemd() {
  command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]
}

redis_install() {
  local root="$1"
  local listen_port="${2:-}"
  local as_service="${3:-false}"

  _redis_read_env "$root"
  [[ -n "$listen_port" ]] || listen_port="${REDIS_PORT:-6379}"

  echo ""
  write_ergoms_message heading_install_run cyan "" "name=Redis"
  echo ""

  if ! _redis_run_install_script "$root" "$listen_port"; then
    return 1
  fi

  if [[ "$as_service" == "true" ]] || _redis_use_systemd; then
    redis_install_service "$root"
  else
    redis_start "$root"
  fi

  echo ""
  write_ergoms_message ok_installed green "" "name=Redis"
  write_ergoms_message label_listening cyan "" "addr=127.0.0.1:${listen_port}"
  write_ergoms_message label_path cyan "" "path=$(_redis_dir "$root")"
  write_ergoms_message label_config cyan "" "path=$(_redis_conf "$root")"
  if [[ "${REDIS_ENABLED:-}" != "true" ]]; then
    write_ergoms_message redis_hint_enable_env yellow
  fi
}

redis_install_service() {
  local root="$1"
  _sync_redis_service_name "$root"
  if ! _redis_is_installed "$root"; then
    write_ergoms_message error_not_installed_run red --stderr "name=Redis" "cmd=ergoms install-redis"
    return 1
  fi

  redis_stop "$root" quiet 2>/dev/null || true

  local content
  content="$(_redis_unit_content "$root")"
  install_unit "$REDIS_SERVICE_NAME" "$content" "$root"
  enable_and_start "$REDIS_SERVICE_NAME.service"
  write_ergoms_message ok_systemd_service_installed_running green "" "name=Redis"
}

redis_start() {
  local root="$1"
  if ! _redis_is_installed "$root"; then
    write_ergoms_message error_not_installed_run red --stderr "name=Redis" "cmd=ergoms install-redis"
    return 1
  fi

  if systemctl is-active --quiet "$REDIS_SERVICE_NAME.service" 2>/dev/null; then
    write_ergoms_message ok_service_already_running green "" "name=Redis"
    return 0
  fi

  if [[ -f "$REDIS_UNIT_PATH" ]]; then
    if [[ $(id -u) -eq 0 ]]; then
      systemctl start "$REDIS_SERVICE_NAME.service"
    else
      sudo systemctl start "$REDIS_SERVICE_NAME.service"
    fi
    write_ergoms_message ok_service_started green "" "name=Redis"
    return 0
  fi

  if systemctl is-active --quiet redis.service 2>/dev/null \
    || systemctl is-active --quiet redis-server.service 2>/dev/null; then
    echo "[WARNING] System Redis service is active and may use port $(_redis_read_port "$root")." >&2
    echo "[WARNING] Stop it first: sudo systemctl disable --now redis.service redis-server.service" >&2
  fi

  local py
  py="$(_redis_python "$root")"
  if [[ -z "$py" ]]; then
    write_ergoms_message python_not_found_setup red --stderr
    return 1
  fi
  "$py" "$root/core/deployment/scripts/redis_dev.py" --root "$root" --start
}

_redis_is_running() {
  local root="${1:-}"

  if systemctl is-active --quiet "$REDIS_SERVICE_NAME.service" 2>/dev/null; then
    return 0
  fi
  if [[ -n "$root" ]] && _redis_is_installed "$root"; then
    local pidfile pid redis_dir
    pidfile="$(_redis_pidfile "$root")"
    if [[ -f "$pidfile" ]]; then
      pid="$(tr -d '[:space:]' < "$pidfile")"
      if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        return 0
      fi
    fi
    redis_dir="$(_redis_dir "$root")"
    if pgrep -f "$redis_dir/bin/redis-server" >/dev/null 2>&1; then
      return 0
    fi
  fi
  return 1
}

redis_stop() {
  local root="${1:-}"
  local quiet="${2:-}"

  if ! _redis_is_running "$root"; then
    [[ -z "$quiet" ]] && write_ergoms_message skip_was_not_running gray "" "name=Redis"
    return 0
  fi

  if systemctl is-active --quiet "$REDIS_SERVICE_NAME.service" 2>/dev/null; then
    write_ergoms_message arrow_stopping_service cyan "" "name=Redis"
    if [[ $(id -u) -eq 0 ]]; then
      systemctl stop "$REDIS_SERVICE_NAME.service"
    else
      sudo systemctl stop "$REDIS_SERVICE_NAME.service"
    fi
    if _redis_is_running "$root"; then
      write_ergoms_message error_stop_service_failed red --stderr "name=Redis"
      return 1
    fi
    write_ergoms_message ok_service_stopped green "" "name=Redis"
    return 0
  fi

  if [[ -n "$root" ]] && _redis_is_installed "$root"; then
    local cli pidfile pid i
    cli="$(_redis_cli "$root")"
    pidfile="$(_redis_pidfile "$root")"
    if [[ -x "$cli" ]]; then
      write_ergoms_message redis_arrow_shutdown cyan
      _redis_cli_shutdown "$root" >/dev/null 2>&1 || true
      for ((i = 1; i <= 10; i++)); do
        if [[ ! -f "$pidfile" ]]; then
          break
        fi
        pid="$(tr -d '[:space:]' < "$pidfile")"
        if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
          rm -f "$pidfile"
          break
        fi
        sleep 1
      done
    fi
    if [[ -f "$pidfile" ]]; then
      pid="$(tr -d '[:space:]' < "$pidfile")"
      if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        sleep 1
      fi
      rm -f "$pidfile"
    fi
  fi

  if _redis_is_running "$root"; then
    write_ergoms_message error_stop_failed red --stderr "name=Redis"
    return 1
  fi
  write_ergoms_message ok_stopped green "" "name=Redis"
}

redis_restart() {
  local root="$1"
  if [[ -f "$REDIS_UNIT_PATH" ]]; then
    if [[ $(id -u) -eq 0 ]]; then
      systemctl restart "$REDIS_SERVICE_NAME.service"
    else
      sudo systemctl restart "$REDIS_SERVICE_NAME.service"
    fi
    write_ergoms_message ok_service_restarted green "" "name=Redis"
    return 0
  fi
  redis_stop "$root"
  redis_start "$root"
}

redis_status() {
  local root="$1"
  if ! _redis_is_installed "$root"; then
    write_ergoms_message component_not_installed gray "" "name=Redis"
    write_ergoms_message label_expected_path gray "" "path=$(_redis_dir "$root")"
    return 0
  fi

  echo ""
  write_ergoms_message heading_status cyan "" "name=Redis"

  if [[ -f "$REDIS_UNIT_PATH" ]]; then
    if systemctl is-active --quiet "$REDIS_SERVICE_NAME.service" 2>/dev/null; then
      write_ergoms_message label_service_running green "" "name=$REDIS_SERVICE_NAME"
    else
      write_ergoms_message label_service_not_running_full yellow "" "name=$REDIS_SERVICE_NAME"
    fi
  elif pgrep -x redis-server >/dev/null 2>&1; then
    write_ergoms_message status_running_pid_process green "" "pid=$(pgrep -x redis-server | head -n1)"
  else
    write_ergoms_message status_process_not_running red
  fi

  write_ergoms_message label_path_indent2 cyan "" "path=$(_redis_dir "$root")"
  write_ergoms_message label_config_indent2 cyan "" "path=$(_redis_conf "$root")"

  if _redis_ping "$root"; then
    echo "  Ping: PONG"
  else
    write_ergoms_message ping_failed_server_down yellow
  fi
}

redis_test() {
  local root="$1"
  if _redis_ping "$root"; then
    echo "[OK] PONG"
    return 0
  fi
  write_ergoms_message redis_error_ping red --stderr
  return 1
}

redis_uninstall() {
  local root="$1"
  local purge="${2:-false}"

  write_ergoms_message heading_remove cyan "" "name=Redis"
  redis_stop "$root"

  if [[ -f "$REDIS_UNIT_PATH" ]]; then
    if [[ $(id -u) -eq 0 ]]; then
      systemctl disable --now "$REDIS_SERVICE_NAME.service" 2>/dev/null || true
      rm -f "$REDIS_UNIT_PATH"
      systemctl daemon-reload
    else
      sudo systemctl disable --now "$REDIS_SERVICE_NAME.service" 2>/dev/null || true
      sudo rm -f "$REDIS_UNIT_PATH"
      sudo systemctl daemon-reload
    fi
    write_ergoms_message redis_ok_unit_removed green
  fi

  local dir
  dir="$(_redis_dir "$root")"
  if [[ "$purge" == "true" ]] && [[ -d "$dir" ]]; then
    rm -rf "$dir"
    write_ergoms_message ok_removed_path green "" "path=$dir"
  else
    write_ergoms_message ok_stopped_binaries_kept green "" "name=Redis" "pkg=redis" "purge_flag=purge"
  fi
}
