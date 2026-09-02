#!/usr/bin/env bash
# Meilisearch management for Linux
# Portable Meilisearch в virtual_env/packages/meilisearch

MEILISEARCH_SERVICE_NAME="$(ergo_service_name meilisearch)"
MEILISEARCH_UNIT_PATH="/etc/systemd/system/${MEILISEARCH_SERVICE_NAME}.service"

_sync_meilisearch_service_name() {
  local root="${1:-}"
  MEILISEARCH_SERVICE_NAME="$(ergo_service_name meilisearch "$root")"
  MEILISEARCH_UNIT_PATH="/etc/systemd/system/${MEILISEARCH_SERVICE_NAME}.service"
}

_meilisearch_dir() {
  local root="$1"
  echo "$root/virtual_env/packages/meilisearch"
}

_meilisearch_runtime_dir() {
  local root="$1"
  echo "$root/virtual_env/cache/meilisearch"
}

_meilisearch_data_dir() {
  local root="$1"
  echo "$root/virtual_env/cache/meilisearch/data.ms"
}

_meilisearch_binary() {
  local root="$1"
  echo "$(_meilisearch_dir "$root")/meilisearch"
}

_meilisearch_log_path() {
  local root="$1"
  local py="$root/virtual_env/python/bin/python"
  local script="$root/core/deployment/scripts/log_env.py"
  if [[ -x "$py" && -f "$script" ]]; then
    local path
    path="$("$py" "$script" path MEILISEARCH "$root" 2>/dev/null || true)"
    [[ -n "$path" ]] && echo "$path" && return 0
  fi
  echo "$root/logs/meilisearch.log"
}

_meilisearch_python() {
  local root="$1"
  local py="$root/virtual_env/python/bin/python"
  if [[ -x "$py" ]]; then
    echo "$py"
    return 0
  fi
  command -v python3
}

_meilisearch_is_installed() {
  local root="$1"
  [[ -x "$(_meilisearch_binary "$root")" ]]
}

_meilisearch_master_key() {
  local root="$1"
  local env_file line value found=""
  # Как load_project_env: позже перекрывает раньше (.env → env/search.env).
  for env_file in "$root/.env" "$root/env/search.env"; do
    [[ -f "$env_file" ]] || continue
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$line" =~ ^[[:space:]]*# ]] && continue
      if [[ "$line" =~ ^MEILI_MASTER_KEY=(.*)$ ]]; then
        value="${BASH_REMATCH[1]}"
        value="${value#\"}"
        value="${value%\"}"
        value="${value#\'}"
        value="${value%\'}"
        found="$value"
      fi
    done < "$env_file"
  done
  if [[ -n "$found" ]]; then
    echo "$found"
    return 0
  fi
  echo "${MEILI_MASTER_KEY:-}"
}

_meilisearch_run_script() {
  local root="$1"
  shift
  local py
  py="$(_meilisearch_python "$root")"
  if [[ -z "$py" ]]; then
    write_ergoms_message python_not_found_setup red --stderr
    return 1
  fi
  "$py" "$root/core/deployment/scripts/install_meilisearch.py" "$@" --root "$root"
}

_meilisearch_unit_content() {
  local root="$1"
  local binary runtime_dir data_dir master_key log_path
  binary="$(_meilisearch_binary "$root")"
  runtime_dir="$(_meilisearch_runtime_dir "$root")"
  data_dir="$(_meilisearch_data_dir "$root")"
  master_key="$(_meilisearch_master_key "$root")"
  log_path="$(_meilisearch_log_path "$root")"
  mkdir -p "$runtime_dir" "$data_dir" "$(dirname "$log_path")"
  : >>"$log_path"
  cat <<UNIT
[Unit]
Description=Ergo MS Meilisearch (portable packages)
After=network.target

[Service]
Type=simple
EnvironmentFile=-__ERGO_MS_ENV__
Environment=MEILI_ENV=development
Environment=MEILI_HTTP_ADDR=127.0.0.1:8004
Environment=MEILI_DB_PATH=$data_dir
Environment=MEILI_MASTER_KEY=$master_key
Environment=MEILI_NO_ANALYTICS=true
WorkingDirectory=$runtime_dir
ExecStart=$binary --db-path $data_dir --http-addr 127.0.0.1:8004 --env development --master-key $master_key --no-analytics
StandardOutput=append:$log_path
StandardError=append:$log_path
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
}

_meilisearch_use_systemd() {
  command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]
}

meilisearch_install() {
  local root="$1"
  local as_service="${2:-false}"

  echo ""
  write_ergoms_message heading_install_run cyan "" "name=Meilisearch"
  echo ""

  if ! _meilisearch_run_script "$root" install; then
    return 1
  fi

  mkdir -p "$(_meilisearch_data_dir "$root")"

  if [[ "$as_service" == "true" ]] || _meilisearch_use_systemd; then
    meilisearch_install_service "$root"
  else
    meilisearch_start "$root"
  fi

  echo ""
  write_ergoms_message ok_installed green "" "name=Meilisearch"
  write_ergoms_message label_path cyan "" "path=$(_meilisearch_dir "$root")"
}

meilisearch_install_service() {
  local root="$1"
  _sync_meilisearch_service_name "$root"
  if ! _meilisearch_is_installed "$root"; then
    write_ergoms_message error_not_installed_run red --stderr "name=Meilisearch" "cmd=ergoms install-meilisearch"
    return 1
  fi

  meilisearch_stop "$root" quiet 2>/dev/null || true
  mkdir -p "$(_meilisearch_data_dir "$root")"

  local content
  content="$(_meilisearch_unit_content "$root")"
  install_unit "$MEILISEARCH_SERVICE_NAME" "$content" "$root"
  enable_and_start "$MEILISEARCH_SERVICE_NAME.service"
  write_ergoms_message ok_systemd_service_installed_running green "" "name=Meilisearch"
}

meilisearch_start() {
  local root="$1"
  if ! _meilisearch_is_installed "$root"; then
    write_ergoms_message error_not_installed_run red --stderr "name=Meilisearch" "cmd=ergoms install-meilisearch"
    return 1
  fi

  if systemctl is-active --quiet "$MEILISEARCH_SERVICE_NAME.service" 2>/dev/null; then
    write_ergoms_message ok_service_already_running green "" "name=Meilisearch"
    return 0
  fi

  if [[ -f "$MEILISEARCH_UNIT_PATH" ]]; then
    if [[ $(id -u) -eq 0 ]]; then
      systemctl start "$MEILISEARCH_SERVICE_NAME.service"
    else
      sudo systemctl start "$MEILISEARCH_SERVICE_NAME.service"
    fi
    write_ergoms_message ok_service_started green "" "name=Meilisearch"
    return 0
  fi

  write_ergoms_message arrow_starting cyan "" "name=Meilisearch"
  if _meilisearch_run_script "$root" start; then
    write_ergoms_message ok_started green "" "name=Meilisearch"
  else
    write_ergoms_message error_start_failed_check_logs red --stderr "name=Meilisearch" "path=$(_meilisearch_log_path "$root")"
    return 1
  fi
}

_meilisearch_is_running() {
  local root="${1:-}"

  if systemctl is-active --quiet "$MEILISEARCH_SERVICE_NAME.service" 2>/dev/null; then
    return 0
  fi
  if [[ -n "$root" ]] && _meilisearch_is_installed "$root"; then
    if pgrep -f "$(_meilisearch_binary "$root")" >/dev/null 2>&1; then
      return 0
    fi
  fi
  return 1
}

meilisearch_stop() {
  local root="${1:-}"
  local quiet="${2:-}"

  if ! _meilisearch_is_running "$root"; then
    [[ -z "$quiet" ]] && write_ergoms_message skip_was_not_running gray "" "name=Meilisearch"
    return 0
  fi

  if systemctl is-active --quiet "$MEILISEARCH_SERVICE_NAME.service" 2>/dev/null; then
    write_ergoms_message arrow_stopping_service cyan "" "name=Meilisearch"
    if [[ $(id -u) -eq 0 ]]; then
      systemctl stop "$MEILISEARCH_SERVICE_NAME.service"
    else
      sudo systemctl stop "$MEILISEARCH_SERVICE_NAME.service"
    fi
    if _meilisearch_is_running "$root"; then
      write_ergoms_message error_stop_service_failed red --stderr "name=Meilisearch"
      return 1
    fi
    write_ergoms_message ok_service_stopped green "" "name=Meilisearch"
    return 0
  fi

  if [[ -n "$root" ]]; then
    _meilisearch_run_script "$root" stop || true
  fi
  [[ -z "$quiet" ]] && write_ergoms_message ok_stopped green "" "name=Meilisearch"
}

meilisearch_restart() {
  local root="$1"
  if ! _meilisearch_is_installed "$root"; then
    write_ergoms_message error_not_installed_run red --stderr "name=Meilisearch" "cmd=ergoms install-meilisearch"
    return 1
  fi

  if [[ -f "$MEILISEARCH_UNIT_PATH" ]]; then
    if [[ $(id -u) -eq 0 ]]; then
      systemctl restart "$MEILISEARCH_SERVICE_NAME.service"
    else
      sudo systemctl restart "$MEILISEARCH_SERVICE_NAME.service"
    fi
    write_ergoms_message ok_service_restarted green "" "name=Meilisearch"
    return 0
  fi

  meilisearch_stop "$root"
  meilisearch_start "$root"
}

meilisearch_uninstall() {
  local root="$1"
  local purge="${2:-false}"

  write_ergoms_message heading_remove cyan "" "name=Meilisearch"
  meilisearch_stop "$root" quiet 2>/dev/null || true

  if [[ -f "$MEILISEARCH_UNIT_PATH" ]]; then
    if [[ $(id -u) -eq 0 ]]; then
      systemctl disable --now "$MEILISEARCH_SERVICE_NAME.service" 2>/dev/null || true
      rm -f "$MEILISEARCH_UNIT_PATH"
      systemctl daemon-reload
    else
      sudo systemctl disable --now "$MEILISEARCH_SERVICE_NAME.service" 2>/dev/null || true
      sudo rm -f "$MEILISEARCH_UNIT_PATH"
      sudo systemctl daemon-reload
    fi
  fi

  local unit_src="$root/core/deployment/wrappers/systemd/${MEILISEARCH_SERVICE_NAME}.service"
  [[ -f "$unit_src" ]] && rm -f "$unit_src"

  if [[ "$purge" == "true" ]]; then
    rm -rf "$(_meilisearch_dir "$root")" "$(_meilisearch_runtime_dir "$root")" "$root/virtual_env/meilisearch"
    write_ergoms_message ok_removed_path green "" "path=$(_meilisearch_dir "$root")"
  else
    write_ergoms_message ok_stopped_binaries_kept green "" "name=Meilisearch" "pkg=meilisearch" "purge_flag=--purge"
  fi
}

export -f meilisearch_install
export -f meilisearch_install_service
export -f meilisearch_start
export -f meilisearch_stop
export -f meilisearch_restart
export -f meilisearch_uninstall
