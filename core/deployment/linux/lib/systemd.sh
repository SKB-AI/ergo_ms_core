#!/usr/bin/env bash
# Systemd management for Linux services
# Управление systemd для служб Linux

_systemd_no_proxy_csv() {
  local root="$1"
  local py="$root/virtual_env/python/bin/python"
  local script="$root/core/deployment/no_proxy_hosts.py"
  if [[ -x "$py" && -f "$script" ]]; then
    "$py" "$script" "$root" 2>/dev/null && return 0
  fi
  echo 'localhost,127.0.0.1,::1'
}

write_env_file() {
  local root="$1"
  local env_file="$root/core/deployment/wrappers/ergo_ms.env"
  local legacy="/etc/default/ergo_ms"
  local py="$root/virtual_env/python/bin/python"
  local script="$root/core/deployment/no_proxy_hosts.py"

  mkdir -p "$(dirname "$env_file")" "$root/logs"
  # systemd EnvironmentFile не снимает кавычки — значения без quotes.
  # NO_PROXY и исходящий HTTP_PROXY берём из .env / env/*.env.
  if [[ -x "$py" && -f "$script" ]] && "$py" "$script" --write-systemd-env "$root" "$env_file"; then
    :
  else
    local no_proxy
    no_proxy="$(_systemd_no_proxy_csv "$root")"
    cat >"$env_file" <<EOF
# Environment for ergo_ms services (внутри корня проекта)
ERGO_ROOT=$root
PYTHONUNBUFFERED=1
NODE_ENV=development
ERGO_LOG_CONSOLE=false
NO_PROXY=$no_proxy
no_proxy=$no_proxy
EOF
  fi
  chmod 0644 "$env_file" 2>/dev/null || true
  ERGO_ROOT="$root" write_ergoms_message systemd_env_written white "" "path=$env_file" "root=$root"

  if [[ -f "$legacy" ]]; then
    if [[ $(id -u) -eq 0 ]]; then
      rm -f "$legacy"
    else
      sudo rm -f "$legacy" 2>/dev/null || true
    fi
    ERGO_ROOT="$root" write_ergoms_message systemd_legacy_removed green "" "path=$legacy"
  fi
}

install_unit() {
  local name="$1"
  local content="$2"
  local root="$3"
  local units_dir="$root/core/deployment/wrappers/systemd"
  local unit_path="$units_dir/${name}.service"
  local legacy_path="/etc/systemd/system/${name}.service"
  local env_file="$root/core/deployment/wrappers/ergo_ms.env"

  # Шаблоны используют __ERGO_MS_ENV__; старый /etc/default — на случай ручных unit.
  content="${content//EnvironmentFile=-__ERGO_MS_ENV__/EnvironmentFile=-$env_file}"
  content="${content//EnvironmentFile=__ERGO_MS_ENV__/EnvironmentFile=$env_file}"
  content="${content//EnvironmentFile=-\/etc\/default\/ergo_ms/EnvironmentFile=-$env_file}"
  content="${content//EnvironmentFile=\/etc\/default\/ergo_ms/EnvironmentFile=$env_file}"

  mkdir -p "$units_dir" "$root/logs"
  printf "%s" "$content" > "$unit_path"
  chmod 0644 "$unit_path" 2>/dev/null || true

  if [[ -f "$legacy_path" && ! -L "$legacy_path" ]]; then
    if [[ $(id -u) -eq 0 ]]; then
      rm -f "$legacy_path"
    else
      sudo rm -f "$legacy_path"
    fi
  fi

  if [[ $(id -u) -eq 0 ]]; then
    systemctl link "$unit_path" >/dev/null
    systemctl daemon-reload
  else
    sudo systemctl link "$unit_path" >/dev/null
    sudo systemctl daemon-reload
  fi
  ERGO_ROOT="$root" write_ergoms_message systemd_unit_installed white "" "path=$unit_path"
}

enable_and_start() {
  local unit="$1"
  if [[ $(id -u) -eq 0 ]]; then
    systemctl reset-failed "$unit" 2>/dev/null || true
    systemctl enable --now "$unit"
  else
    sudo systemctl reset-failed "$unit" 2>/dev/null || true
    sudo systemctl enable --now "$unit"
  fi
}

# Получение базовых unit definitions (API, Client, Beat)
get_base_unit_definitions() {
  local root="${1:-}"
  local client_log client_stdout client_stderr
  local logs_dir="${root}/logs"
  # systemd требует абсолютный путь в StandardError/StandardOutput — ${ERGO_ROOT} не раскрывается.
  local api_name beat_name media_name redis_name
  api_name="$(ergo_service_name api_dev "$root")"
  beat_name="$(ergo_service_name celery_beat "$root")"
  media_name="$(ergo_service_name media_api "$root")"
  redis_name="$(ergo_service_name redis "$root")"
  local api_stderr="${logs_dir}/${api_name}.stderr.log"
  local beat_stderr="${logs_dir}/${beat_name}.stderr.log"
  local media_stderr="${logs_dir}/${media_name}.stderr.log"
  local _log_env_py="$root/virtual_env/python/bin/python"
  local _log_env_script="$root/core/deployment/scripts/log_env.py"

  if [[ -x "$_log_env_py" && -f "$_log_env_script" && -n "$root" ]]; then
    client_log="$("$_log_env_py" "$_log_env_script" path CLIENT_DEV "$root" 2>/dev/null || true)"
  fi
  [[ -z "$client_log" ]] && client_log="${logs_dir}/client-dev.log"
  if [[ -x "$_log_env_py" && -f "$_log_env_script" && -n "$root" ]] \
    && [[ "$("$_log_env_py" "$_log_env_script" client-dev-enabled "$root" 2>/dev/null || true)" == "false" ]]; then
    client_stdout=null
    client_stderr=null
  else
    client_stdout="append:${client_log}"
    client_stderr="append:${client_log}"
  fi

  API_UNIT=$(cat <<UNIT
[Unit]
Description=Ergo API (mode from API_DEPLOY_TYPE)
After=network.target ${redis_name}.service
Wants=${redis_name}.service

[Service]
Type=simple
EnvironmentFile=__ERGO_MS_ENV__
ExecStart=/bin/bash -lc 'cd "\$ERGO_ROOT" && . "\$ERGO_ROOT/virtual_env/python/bin/activate" && python core/api/scripts/start_api.py'
Restart=always
RestartSec=5
TimeoutStopSec=30
Environment=PYTHONUNBUFFERED=1
Environment=ERGO_LOG_CONSOLE=false
StandardOutput=null
StandardError=append:${api_stderr}

[Install]
WantedBy=multi-user.target
UNIT
)

  CLIENT_UNIT=$(cat <<UNIT
[Unit]
Description=Ergo Client (Vite dev or nginx skip)
After=network.target

[Service]
Type=simple
EnvironmentFile=__ERGO_MS_ENV__
ExecStart=/bin/bash -lc 'cd "\$ERGO_ROOT" && . "\$ERGO_ROOT/virtual_env/python/bin/activate" && python core/deployment/scripts/start_client_if_dev.py'
Restart=always
RestartSec=5
TimeoutStopSec=30
Environment=NODE_ENV=development
StandardOutput=${client_stdout}
StandardError=${client_stderr}

[Install]
WantedBy=multi-user.target
UNIT
)

  CELERY_BEAT_UNIT=$(cat <<UNIT
[Unit]
Description=Ergo Celery Beat
After=network.target
Requires=${api_name}.service

[Service]
Type=simple
EnvironmentFile=__ERGO_MS_ENV__
ExecStart=/bin/bash -lc 'cd "\$ERGO_ROOT/core" && . "\$ERGO_ROOT/virtual_env/python/bin/activate" && python api/scripts/start_celery_beat.py'
Restart=always
RestartSec=5
TimeoutStopSec=30
Environment=PYTHONUNBUFFERED=1
Environment=ERGO_LOG_CONSOLE=false
StandardOutput=null
StandardError=append:${beat_stderr}

[Install]
WantedBy=multi-user.target
UNIT
)

  MEDIA_API_UNIT=$(cat <<UNIT
[Unit]
Description=Ergo Media API (CDN / file server)
After=network.target

[Service]
Type=simple
EnvironmentFile=__ERGO_MS_ENV__
Environment=PYTHONUNBUFFERED=1
Environment=ERGO_LOG_CONSOLE=false
ExecStart=/bin/bash -lc 'cd "\$ERGO_ROOT" && . "\$ERGO_ROOT/virtual_env/python/bin/activate" && python core/api/scripts/start_media_api.py'
Restart=always
RestartSec=5
TimeoutStopSec=30
StandardOutput=null
StandardError=append:${media_stderr}

[Install]
WantedBy=multi-user.target
UNIT
)

  export API_UNIT
  export CLIENT_UNIT
  export CELERY_BEAT_UNIT
  export MEDIA_API_UNIT
}

# Генерация unit для конкретного Celery worker'а
generate_worker_unit() {
  local worker_name="$1"
  local root="${2:-}"
  local worker_base api_name
  worker_base="$(ergo_service_name celery_worker "$root")"
  api_name="$(ergo_service_name api_dev "$root")"
  local worker_stderr="${root}/logs/${worker_base}_${worker_name}.stderr.log"

  cat <<UNIT
[Unit]
Description=Ergo Celery Worker ($worker_name)
After=network.target
Requires=${api_name}.service

[Service]
Type=simple
EnvironmentFile=__ERGO_MS_ENV__
ExecStart=/bin/bash -lc 'cd "\$ERGO_ROOT/core" && . "\$ERGO_ROOT/virtual_env/python/bin/activate" && python api/scripts/start_celery_worker.py --worker=$worker_name'
Restart=always
RestartSec=5
TimeoutStopSec=30
Environment=PYTHONUNBUFFERED=1
Environment=ERGO_LOG_CONSOLE=false
StandardOutput=null
StandardError=append:${worker_stderr}

[Install]
WantedBy=multi-user.target
UNIT
}

# Генерация unit для единственного worker'а (без конфига)
generate_default_worker_unit() {
  local root="${1:-}"
  local worker_base api_name
  worker_base="$(ergo_service_name celery_worker "$root")"
  api_name="$(ergo_service_name api_dev "$root")"
  local worker_stderr="${root}/logs/${worker_base}.stderr.log"

  cat <<UNIT
[Unit]
Description=Ergo Celery Worker
After=network.target
Requires=${api_name}.service

[Service]
Type=simple
EnvironmentFile=__ERGO_MS_ENV__
ExecStart=/bin/bash -lc 'cd "\$ERGO_ROOT/core" && . "\$ERGO_ROOT/virtual_env/python/bin/activate" && python api/scripts/start_celery_worker.py'
Restart=always
RestartSec=5
TimeoutStopSec=30
Environment=PYTHONUNBUFFERED=1
Environment=ERGO_LOG_CONSOLE=false
StandardOutput=null
StandardError=append:${worker_stderr}

[Install]
WantedBy=multi-user.target
UNIT
}

# Установка всех воркеров из конфигурации
install_worker_units() {
  local root="$1"
  local workers
  workers="$(get_celery_workers "$root")"
  
  local worker_base
  worker_base="$(ergo_service_name celery_worker "$root")"
  if [[ -n "$workers" ]]; then
    ERGO_ROOT="$root" write_ergoms_message systemd_workers_found white "" "workers=$workers"
    for worker in $workers; do
      local unit_content
      unit_content="$(generate_worker_unit "$worker" "$root")"
      install_unit "${worker_base}_${worker}" "$unit_content" "$root"
    done
  else
    ERGO_ROOT="$root" write_ergoms_message systemd_workers_config_missing white
    local unit_content
    unit_content="$(generate_default_worker_unit "$root")"
    install_unit "$worker_base" "$unit_content" "$root"
  fi
}

# Включение и запуск всех воркеров
enable_and_start_workers() {
  local root="$1"
  local workers
  workers="$(get_celery_workers "$root")"
  
  local worker_base
  worker_base="$(ergo_service_name celery_worker "$root")"
  if [[ -n "$workers" ]]; then
    for worker in $workers; do
      enable_and_start "${worker_base}_${worker}.service"
    done
  else
    enable_and_start "${worker_base}.service"
  fi
}

export -f write_env_file
export -f install_unit
export -f enable_and_start
export -f get_base_unit_definitions
export -f generate_worker_unit
export -f generate_default_worker_unit
export -f install_worker_units
export -f enable_and_start_workers
