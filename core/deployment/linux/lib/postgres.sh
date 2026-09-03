#!/usr/bin/env bash
# PostgreSQL management for Linux
# Portable PostgreSQL в virtual_env/packages/postgres

SCRIPT_DIR_POSTGRES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=portable_env.sh
source "$SCRIPT_DIR_POSTGRES/portable_env.sh"

POSTGRES_SERVICE_NAME_DEFAULT='ergo_ms_postgres'
POSTGRES_SERVICE_NAME="$POSTGRES_SERVICE_NAME_DEFAULT"
POSTGRES_SERVICE_DISPLAY_NAME='Ergo MS - PostgreSQL'
POSTGRES_UNIT_PATH="/etc/systemd/system/${POSTGRES_SERVICE_NAME}.service"

_postgres_init_service_config() {
  local root="$1"
  local name display
  name="$(_ergo_env_value "$root" 'POSTGRES_SERVICE_LINUX' 2>/dev/null || true)"
  if [[ -n "${name:-}" ]]; then
    POSTGRES_SERVICE_NAME="$name"
  else
    POSTGRES_SERVICE_NAME="$(ergo_service_name postgres "$root")"
  fi
  display="$(_ergo_env_value "$root" 'POSTGRES_SERVICE_DISPLAY_NAME' 2>/dev/null || true)"
  if [[ -n "${display:-}" ]]; then
    POSTGRES_SERVICE_DISPLAY_NAME="$display"
  else
    POSTGRES_SERVICE_DISPLAY_NAME='Ergo MS - PostgreSQL'
  fi
  POSTGRES_UNIT_PATH="/etc/systemd/system/${POSTGRES_SERVICE_NAME}.service"
}

_postgres_dir() {
  local root="$1"
  echo "$root/virtual_env/packages/postgres"
}

_postgres_bin_dir() {
  local root="$1"
  local dir
  dir="$(_postgres_dir "$root")"
  if [[ -x "$dir/bin/postgres" ]]; then
    echo "$dir/bin"
    return 0
  fi
  if [[ -x "$dir/pgsql/bin/postgres" ]]; then
    echo "$dir/pgsql/bin"
    return 0
  fi
  echo "$dir/bin"
}

_postgres_bin() {
  local root="$1"
  local name="$2"
  echo "$(_postgres_bin_dir "$root")/$name"
}

_postgres_data() {
  local root="$1"
  echo "$(_postgres_dir "$root")/data"
}

_postgres_apply_backup_schedule() {
  local root="$1"
  local uninstall="${2:-false}"
  local py
  py="$(_postgres_python "$root")"
  if [[ -z "$py" || ! -x "$py" ]]; then
    return 0
  fi
  local script="$root/core/deployment/scripts/install_postgres_backup_schedule.py"
  if [[ ! -f "$script" ]]; then
    return 0
  fi
  if [[ "$uninstall" == "true" ]]; then
    "$py" -u "$script" --root "$root" --uninstall || true
  else
    "$py" -u "$script" --root "$root" || true
  fi
}

_postgres_python() {
  local root="$1"
  local py="$root/virtual_env/python/bin/python"
  if [[ -x "$py" ]]; then
    echo "$py"
    return 0
  fi
  command -v python3
}

_postgres_is_installed() {
  local root="$1"
  [[ -x "$(_postgres_bin "$root" postgres)" ]] && [[ -x "$(_postgres_bin "$root" pg_ctl)" ]]
}

_postgres_yaml_default_field() {
  local root="$1"
  local field="$2"
  local yaml_path="$root/databases.yaml"
  [[ -f "$yaml_path" ]] || return 0
  local in_default=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^[[:space:]]*default:[[:space:]]*$ ]]; then
      in_default=1
      continue
    fi
    if [[ "$in_default" -eq 1 && "$line" =~ ^[[:space:]]{2}[A-Za-z_]+:[[:space:]]*$ ]]; then
      break
    fi
    if [[ "$in_default" -eq 1 && "$line" =~ ^[[:space:]]+${field}:[[:space:]]*(.+)$ ]]; then
      printf '%s' "${BASH_REMATCH[1]}" | tr -d '[:space:]"'"'"
      return 0
    fi
  done <"$yaml_path"
}

_postgres_listen_port() {
  local root="$1"
  local port_file
  port_file="$(_postgres_dir "$root")/PORT"
  if [[ -f "$port_file" ]]; then
    local raw
    raw="$(tr -d '[:space:]' <"$port_file" || true)"
    if [[ "$raw" =~ ^[0-9]+$ ]]; then
      echo "$raw"
      return 0
    fi
  fi
  local yaml_port
  yaml_port="$(_postgres_yaml_default_field "$root" port || true)"
  if [[ "$yaml_port" =~ ^[0-9]+$ ]]; then
    echo "$yaml_port"
    return 0
  fi
  echo "5433"
}

_postgres_listen_bind() {
  local root="$1"
  local yaml_host
  yaml_host="$(_postgres_yaml_default_field "$root" host || true)"
  case "$(printf '%s' "$yaml_host" | tr '[:upper:]' '[:lower:]')" in
    '' ) echo "127.0.0.1" ;;
    localhost|::1) echo "127.0.0.1" ;;
    *) echo "$yaml_host" ;;
  esac
}

_postgres_db_access_field() {
  local root="$1"
  local field="$2"
  local fallback="$3"
  local value
  value="$(_postgres_yaml_default_field "$root" "$field" || true)"
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
  else
    printf '%s' "$fallback"
  fi
}

_postgres_db_access_summary() {
  local root="$1"
  local db_name db_user db_password
  db_name="$(_postgres_db_access_field "$root" name ergo_ms)"
  db_user="$(_postgres_db_access_field "$root" user postgres)"
  db_password="$(_postgres_db_access_field "$root" password admin)"
  write_ergoms_message pg_label_db cyan "" "name=$db_name"
  write_ergoms_message pg_label_user cyan "" "user=$db_user"
  write_ergoms_message pg_label_password cyan "" "password=$db_password"
  write_ergoms_message pg_info_credentials_source cyan
}

_postgres_yaml_port_hint() {
  local root="$1"
  local listen_port="$2"
  local yaml_port
  yaml_port="$(_postgres_yaml_default_field "$root" port || true)"
  if [[ -z "$yaml_port" ]]; then
    write_ergoms_message pg_info_set_default_port cyan "" "port=$listen_port"
    return 0
  fi
  if [[ "$yaml_port" != "$listen_port" ]]; then
    write_ergoms_message pg_warn_port_mismatch yellow "" "yaml_port=$yaml_port" "listen_port=$listen_port"
    write_ergoms_message pg_info_reinstall_or_align_port cyan
  fi
}

_postgres_portable_present() {
  local root="$1"
  if _postgres_is_installed "$root"; then
    return 0
  fi
  [[ -f "$POSTGRES_UNIT_PATH" ]] && return 0
  return 1
}

_postgres_run_script() {
  local root="$1"
  shift
  local py
  py="$(_postgres_python "$root")"
  if [[ -z "$py" ]]; then
    write_ergoms_message python_not_found_setup red --stderr
    return 1
  fi
  export PYTHONIOENCODING=utf-8
  export PYTHONUTF8=1
  "$py" "$root/core/deployment/scripts/install_postgres.py" --root "$root" "$@"
}

_postgres_system_present() {
  local root="$1"
  _postgres_run_script "$root" --check-system-only
}

_postgres_force_install() {
  local root="$1"
  _postgres_run_script "$root" --check-force-only
}

_postgres_resolve_service_user() {
  local root="$1"
  local configured owner current data
  configured="$(_ergo_env_value "$root" 'POSTGRES_SERVICE_USER' 2>/dev/null || true)"
  if [[ -n "${configured:-}" && "$configured" != "root" ]]; then
    echo "$configured"
    return 0
  fi
  data="$(_postgres_data "$root")"
  if [[ -d "$data" ]]; then
    owner="$(stat -c '%U' "$data" 2>/dev/null || true)"
    if [[ -n "$owner" && "$owner" != "root" ]]; then
      echo "$owner"
      return 0
    fi
  fi
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    echo "$SUDO_USER"
    return 0
  fi
  current="$(id -un 2>/dev/null || true)"
  if [[ -n "$current" && "$current" != "root" ]]; then
    echo "$current"
    return 0
  fi
  owner="$(stat -c '%U' "$root" 2>/dev/null || true)"
  if [[ -n "$owner" && "$owner" != "root" ]]; then
    echo "$owner"
    return 0
  fi
  return 1
}

_postgres_unit_content() {
  local root="$1"
  local postgres data user group
  _postgres_init_service_config "$root"
  postgres="$(_postgres_bin "$root" postgres)"
  data="$(_postgres_data "$root")"
  if ! user="$(_postgres_resolve_service_user "$root")"; then
    write_ergoms_message pg_error_need_unprivileged_user red --stderr
    return 1
  fi
  group="$(id -gn "$user" 2>/dev/null || echo "$user")"
  cat <<UNIT
[Unit]
Description=${POSTGRES_SERVICE_DISPLAY_NAME}
After=network.target

[Service]
Type=simple
EnvironmentFile=-__ERGO_MS_ENV__
User=$user
Group=$group
ExecStart=$postgres -D $data
ExecStop=$(_postgres_bin "$root" pg_ctl) stop -D $data -m fast
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
}

_postgres_use_systemd() {
  command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]
}

postgres_install() {
  local root="$1"
  local listen_port="${2:-}"
  local no_skip_system="${3:-false}"

  _postgres_init_service_config "$root"

  echo ""
  write_ergoms_message heading_install_run cyan "" "name=PostgreSQL"
  echo ""

  local force_env=false
  if _postgres_force_install "$root"; then
    force_env=true
  fi

  if _postgres_system_present "$root" && [[ "$force_env" != "true" ]] && [[ "$no_skip_system" != "true" ]]; then
    write_ergoms_message pg_skip_system_service gray
    write_ergoms_message pg_info_force_install cyan
    return 0
  fi

  local args=(--no-start)
  [[ -n "$listen_port" ]] && args+=(--port "$listen_port")
  if [[ "$force_env" == "true" ]] || [[ "$no_skip_system" == "true" ]]; then
    args+=(--no-skip-system)
  fi

  if ! _postgres_run_script "$root" "${args[@]}"; then
    write_ergoms_message error_install_failed red --stderr "name=PostgreSQL"
    return 1
  fi

  if ! _postgres_is_installed "$root"; then
    write_ergoms_message pg_skip_portable gray
    return 0
  fi

  if _postgres_use_systemd; then
    postgres_install_service "$root"
  else
    postgres_start "$root"
  fi

  if ! _postgres_run_script "$root" --ensure-db-only; then
    write_ergoms_message pg_error_create_dbs red --stderr
    return 1
  fi

  local listen_port listen_bind
  listen_port="$(_postgres_listen_port "$root")"
  listen_bind="$(_postgres_listen_bind "$root")"
  echo ""
  write_ergoms_message ok_installed green "" "name=PostgreSQL"
  write_ergoms_message label_path cyan "" "path=$(_postgres_dir "$root")"
  write_ergoms_message label_service cyan "" "name=$POSTGRES_SERVICE_NAME"
  write_ergoms_message label_listening cyan "" "addr=${listen_bind}:${listen_port}"
  _postgres_db_access_summary "$root"
  _postgres_yaml_port_hint "$root" "$listen_port"
}

postgres_install_service() {
  local root="$1"
  _postgres_init_service_config "$root"
  if ! _postgres_is_installed "$root"; then
    write_ergoms_message error_not_installed_run red --stderr "name=PostgreSQL" "cmd=ergoms install-postgres"
    return 1
  fi

  postgres_stop "$root" quiet 2>/dev/null || true

  local content user
  content="$(_postgres_unit_content "$root")" || return 1
  user="$(_postgres_resolve_service_user "$root")"
  write_ergoms_message pg_info_service_user cyan "" "user=$user"
  install_unit "$POSTGRES_SERVICE_NAME" "$content" "$root"
  enable_and_start "$POSTGRES_SERVICE_NAME.service"
  write_ergoms_message ok_systemd_service_installed_running green "" "name=PostgreSQL"
  _postgres_apply_backup_schedule "$root"
}

postgres_start() {
  local root="$1"
  _postgres_init_service_config "$root"
  if ! _postgres_is_installed "$root"; then
    write_ergoms_message error_not_installed_run red --stderr "name=PostgreSQL" "cmd=ergoms install-postgres"
    return 1
  fi

  if systemctl is-active --quiet "$POSTGRES_SERVICE_NAME.service" 2>/dev/null; then
    write_ergoms_message ok_service_already_running green "" "name=PostgreSQL"
    return 0
  fi

  if [[ -f "$POSTGRES_UNIT_PATH" ]]; then
    if [[ $(id -u) -eq 0 ]]; then
      systemctl start "$POSTGRES_SERVICE_NAME.service"
    else
      sudo systemctl start "$POSTGRES_SERVICE_NAME.service"
    fi
    write_ergoms_message pg_ok_service_started green
    return 0
  fi

  local pg_ctl data log_file owner
  pg_ctl="$(_postgres_bin "$root" pg_ctl)"
  data="$(_postgres_data "$root")"
  log_file="$(_postgres_dir "$root")/logs/pg_ctl.log"
  mkdir -p "$(_postgres_dir "$root")/logs"
  write_ergoms_message arrow_starting cyan "" "name=PostgreSQL"
  # pg_ctl нельзя от root; при sudo стартуем от владельца data.
  if [[ $(id -u) -eq 0 ]]; then
    if ! owner="$(_postgres_resolve_service_user "$root")"; then
      write_ergoms_message pg_error_need_unprivileged_user red --stderr
      return 1
    fi
    if ! runuser -u "$owner" -- "$pg_ctl" start -D "$data" -l "$log_file" -w -t 60; then
      write_ergoms_message error_start_failed_check_logs red --stderr "name=PostgreSQL" "path=$log_file"
      return 1
    fi
  elif ! "$pg_ctl" start -D "$data" -l "$log_file" -w -t 60; then
    write_ergoms_message error_start_failed_check_logs red --stderr "name=PostgreSQL" "path=$log_file"
    return 1
  fi
  write_ergoms_message ok_started green "" "name=PostgreSQL"
}

postgres_stop() {
  local root="${1:-}"
  local quiet="${2:-}"
  _postgres_init_service_config "$root"

  if systemctl is-active --quiet "$POSTGRES_SERVICE_NAME.service" 2>/dev/null; then
    [[ -z "$quiet" ]] && write_ergoms_message arrow_stopping_service cyan "" "name=PostgreSQL"
    if [[ $(id -u) -eq 0 ]]; then
      systemctl stop "$POSTGRES_SERVICE_NAME.service"
    else
      sudo systemctl stop "$POSTGRES_SERVICE_NAME.service"
    fi
    [[ -z "$quiet" ]] && write_ergoms_message ok_service_stopped green "" "name=PostgreSQL"
    return 0
  fi

  if [[ -n "$root" ]] && _postgres_is_installed "$root"; then
    local pg_ctl data
    pg_ctl="$(_postgres_bin "$root" pg_ctl)"
    data="$(_postgres_data "$root")"
    [[ -z "$quiet" ]] && write_ergoms_message pg_arrow_stop_pg_ctl cyan
    "$pg_ctl" stop -D "$data" -m fast -w 2>/dev/null || true
  fi

  [[ -z "$quiet" ]] && write_ergoms_message ok_stopped green "" "name=PostgreSQL"
  return 0
}

postgres_restart() {
  local root="$1"
  _postgres_init_service_config "$root"
  if systemctl is-active --quiet "$POSTGRES_SERVICE_NAME.service" 2>/dev/null \
    || [[ -f "$POSTGRES_UNIT_PATH" ]]; then
    if [[ $(id -u) -eq 0 ]]; then
      systemctl restart "$POSTGRES_SERVICE_NAME.service"
    else
      sudo systemctl restart "$POSTGRES_SERVICE_NAME.service"
    fi
    write_ergoms_message ok_service_restarted green "" "name=PostgreSQL"
    return 0
  fi
  postgres_stop "$root"
  postgres_start "$root"
}

postgres_status() {
  local root="$1"
  local dir
  _postgres_init_service_config "$root"
  dir="$(_postgres_dir "$root")"

  if ! _postgres_is_installed "$root"; then
    write_ergoms_message component_not_installed gray "" "name=PostgreSQL"
    write_ergoms_message label_expected_path gray "" "path=$dir"
    return 0
  fi

  echo ""
  write_ergoms_message heading_status cyan "" "name=PostgreSQL"
  if systemctl is-active --quiet "$POSTGRES_SERVICE_NAME.service" 2>/dev/null; then
    write_ergoms_message label_service_status green "" "name=$POSTGRES_SERVICE_NAME" "status=active"
  elif [[ -f "$POSTGRES_UNIT_PATH" ]]; then
    write_ergoms_message label_service_status yellow "" "name=$POSTGRES_SERVICE_NAME" "status=inactive"
  else
    write_ergoms_message service_not_registered yellow
  fi
  local listen_port listen_bind
  listen_port="$(_postgres_listen_port "$root")"
  listen_bind="$(_postgres_listen_bind "$root")"
  write_ergoms_message label_path_indent2 cyan "" "path=$dir"
  write_ergoms_message label_listening_indent2 cyan "" "addr=${listen_bind}:${listen_port}"
  _postgres_yaml_port_hint "$root" "$listen_port"
  if _postgres_run_script "$root" --ping-only; then
    echo "  Ping: OK"
  else
    write_ergoms_message ping_failed_server_down yellow
  fi
}

postgres_test() {
  local root="$1"
  _postgres_run_script "$root" --ping-only
}

postgres_migrate_to_portable() {
  local root="$1"
  shift || true

  echo ""
  write_ergoms_message pg_heading_migrate cyan
  echo ""

  if ! _postgres_is_installed "$root"; then
    write_ergoms_message error_not_installed_run red --stderr "name=PostgreSQL" "cmd=ergoms install-postgres"
    return 1
  fi

  local py
  py="$(_postgres_python "$root")"
  if [[ -z "$py" ]]; then
    write_ergoms_message python_not_found_setup red --stderr
    return 1
  fi
  export PYTHONIOENCODING=utf-8
  export PYTHONUTF8=1
  export PYTHONUNBUFFERED=1
  "$py" -u "$root/core/deployment/scripts/migrate_postgres_to_portable.py" --root "$root" "$@"
}

postgres_uninstall() {
  local root="$1"
  local purge="${2:-false}"

  _postgres_init_service_config "$root"
  write_ergoms_message heading_remove cyan "" "name=PostgreSQL"
  postgres_stop "$root" quiet 2>/dev/null || true

  if [[ -f "$POSTGRES_UNIT_PATH" ]]; then
    if [[ $(id -u) -eq 0 ]]; then
      systemctl disable --now "$POSTGRES_SERVICE_NAME.service" 2>/dev/null || true
      rm -f "$POSTGRES_UNIT_PATH"
      systemctl daemon-reload
    else
      sudo systemctl disable --now "$POSTGRES_SERVICE_NAME.service" 2>/dev/null || true
      sudo rm -f "$POSTGRES_UNIT_PATH"
      sudo systemctl daemon-reload
    fi
    write_ergoms_message pg_ok_service_removed green
  fi
  _postgres_apply_backup_schedule "$root" true

  local dir
  dir="$(_postgres_dir "$root")"
  if [[ "$purge" == "true" ]] && [[ -d "$dir" ]]; then
    rm -rf "$dir"
    write_ergoms_message ok_removed_path green "" "path=$dir"
  else
    write_ergoms_message ok_stopped_binaries_kept green "" "name=PostgreSQL" "pkg=postgres" "purge_flag=--purge"
  fi
}
