#!/usr/bin/env bash
# Core utilities for ErgoMS deployment
# Базовые утилиты для развертывания ErgoMS

SCRIPT_DIR_CORE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Trim leading/trailing whitespace without xargs (xargs treats quotes specially).
_ergoms_trim() {
  local s="${1-}"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}
export -f _ergoms_trim

# shellcheck source=console_tags.sh
source "$SCRIPT_DIR_CORE/console_tags.sh"
# shellcheck source=nginx_env.sh
source "$SCRIPT_DIR_CORE/nginx_env.sh"
# shellcheck source=redis_env.sh
source "$SCRIPT_DIR_CORE/redis_env.sh"
# shellcheck source=search_env.sh
source "$SCRIPT_DIR_CORE/search_env.sh"
# shellcheck source=portable_env.sh
source "$SCRIPT_DIR_CORE/portable_env.sh"

# Константы (базовые службы без воркеров)
CLI_NAME="ergoms"

ergo_service_prefix() {
  local root="${1:-}"
  if [[ -n "${ERGO_SERVICE_PREFIX:-}" ]]; then
    printf '%s' "$ERGO_SERVICE_PREFIX"
    return 0
  fi
  if [[ -n "$root" ]]; then
    local from_env
    from_env="$(_ergo_env_value "$root" 'ERGO_SERVICE_PREFIX' 2>/dev/null || true)"
    if [[ -n "${from_env:-}" ]]; then
      printf '%s' "$from_env"
      return 0
    fi
  fi
  printf '%s' 'ergo_ms'
}

ergo_service_name() {
  local role="$1"
  local root="${2:-}"
  printf '%s_%s' "$(ergo_service_prefix "$root")" "$role"
}

BASE_SERVICES="$(ergo_service_name api_dev) $(ergo_service_name client_dev) $(ergo_service_name celery_beat)"

# Глобальная переменная для кэширования списка служб
CACHED_UNITS_LIST=""

require_root_or_sudo() {
  if [[ $(id -u) -ne 0 ]]; then
    if ! command -v sudo >/dev/null 2>&1; then
      write_ergoms_message admin_required_linux red --stderr
      exit 1
    fi
  fi
}

# portable-пакеты в virtual_env принадлежат владельцу корня, не root.
restore_project_ownership() {
  local root="$1"
  local path="$2"
  [[ -e "$path" ]] || return 0
  local owner group
  owner="$(stat -c '%U' "$root" 2>/dev/null || true)"
  group="$(stat -c '%G' "$root" 2>/dev/null || true)"
  [[ -n "$owner" && "$owner" != "root" ]] || return 0
  if [[ "$(id -u)" -eq 0 ]]; then
    chown -R "$owner:$group" "$path"
    return 0
  fi
  local current
  current="$(stat -c '%U' "$path" 2>/dev/null || true)"
  [[ "$current" == "$owner" ]] && return 0
  command -v sudo >/dev/null 2>&1 || return 1
  sudo chown -R "$owner:$group" "$path"
}

write_ergoms_message() {
  local key="$1"
  local color="${2:-white}"
  local use_stderr="${3:-}"
  local root="${ERGO_ROOT:-}"
  if [[ -z "$root" ]]; then
    root="$(detect_project_root 2>/dev/null || echo '')"
  fi
  local python="$root/virtual_env/python/bin/python"
  local script="$root/core/deployment/scripts/ergoms_console.py"
  if [[ -f "$python" && -f "$script" ]]; then
    local args=("$script" --key "$key" --color "$color")
    [[ -n "$use_stderr" ]] && args+=(--stderr)
    shift 3 || true
    while [[ $# -gt 0 ]]; do
      args+=(--param "$1")
      shift
    done
    "$python" "${args[@]}"
    return
  fi
  echo "[$key]" >&2
}

write_ergoms_text() {
  local text="$1"
  local color="${2:-white}"
  local use_stderr="${3:-}"
  local root="${ERGO_ROOT:-}"
  if [[ -z "$root" ]]; then
    root="$(detect_project_root 2>/dev/null || echo '')"
  fi
  local python="$root/virtual_env/python/bin/python"
  local script="$root/core/deployment/scripts/ergoms_console.py"
  if [[ -f "$python" && -f "$script" ]]; then
    local args=("$script" --text "$text" --color "$color")
    [[ -n "$use_stderr" ]] && args+=(--stderr)
    "$python" "${args[@]}"
    return
  fi
  if [[ -n "$use_stderr" ]]; then
    echo "$text" >&2
  else
    echo "$text"
  fi
}

detect_project_root() {
  if [[ -n "${ERGOMS_PROJECT_ROOT:-}" && -d "${ERGOMS_PROJECT_ROOT}/core/deployment" ]]; then
    if command -v readlink >/dev/null 2>&1; then
      readlink -f "$ERGOMS_PROJECT_ROOT"
    else
      (cd "$ERGOMS_PROJECT_ROOT" && pwd)
    fi
    return 0
  fi
  # Службы systemd передают корень через EnvironmentFile
  if [[ -n "${ERGO_ROOT:-}" && -d "${ERGO_ROOT}/modules" && -d "${ERGO_ROOT}/core/deployment" ]]; then
    if command -v readlink >/dev/null 2>&1; then
      readlink -f "$ERGO_ROOT"
    else
      (cd "$ERGO_ROOT" && pwd)
    fi
    return 0
  fi

  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  # core/deployment/linux/lib → core/deployment
  local deployment_dir
  deployment_dir="$(cd "$script_dir/../.." && pwd)"

  # Prefer git root if available (may fail as root: dubious ownership)
  if command -v git >/dev/null 2>&1; then
    if git -C "$deployment_dir" rev-parse --show-toplevel >/dev/null 2>&1; then
      git -C "$deployment_dir" rev-parse --show-toplevel
      return 0
    fi
  fi

  # Fallback: …/core/deployment → корень проекта (на два уровня вверх)
  echo "$(cd "$deployment_dir/../.." && pwd)"
}

# Парсинг YAML файла для получения имён воркеров
# Используем простой парсер без внешних зависимостей
parse_workers_from_yaml() {
  local yaml_file="$1"
  local workers=()
  
  if [[ ! -f "$yaml_file" ]]; then
    echo ""
    return
  fi
  
  local in_workers=false
  local indent_level=0
  
  while IFS= read -r line || [[ -n "$line" ]]; do
    # Пропускаем комментарии и пустые строки
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    
    # Проверяем начало секции workers:
    if [[ "$line" =~ ^workers:[[:space:]]*$ ]]; then
      in_workers=true
      continue
    fi
    
    # Если мы в секции workers
    if [[ "$in_workers" == true ]]; then
      # Проверяем, не началась ли новая секция верхнего уровня (defaults:, etc)
      if [[ "$line" =~ ^[a-z_]+:[[:space:]]*$ ]] && [[ ! "$line" =~ ^[[:space:]] ]]; then
        in_workers=false
        continue
      fi
      
      # Ищем имена воркеров (строки с отступом в 2 пробела и двоеточием)
      if [[ "$line" =~ ^[[:space:]]{2}([a-z_]+):[[:space:]]*$ ]]; then
        local worker_name="${BASH_REMATCH[1]}"
        workers+=("$worker_name")
      fi
    fi
  done < "$yaml_file"
  
  echo "${workers[*]}"
}

# Получение списка воркеров из celery_workers.yaml
get_celery_workers() {
  local project_root="${1:-}"
  
  if [[ -z "$project_root" ]]; then
    project_root="$(detect_project_root 2>/dev/null || echo '')"
  fi
  
  if [[ -z "$project_root" ]]; then
    echo ""
    return
  fi
  
  local workers_config="$project_root/celery_workers.yaml"
  parse_workers_from_yaml "$workers_config"
}

_postgres_portable_enabled() {
  local project_root="${1:-}"
  local svc_name='ergo_ms_postgres'
  local from_env
  [[ -x "$project_root/virtual_env/packages/postgres/bin/postgres" ]] && return 0
  [[ -x "$project_root/virtual_env/packages/postgres/pgsql/bin/postgres" ]] && return 0
  from_env="$(_ergo_env_value "$project_root" 'POSTGRES_SERVICE_LINUX' 2>/dev/null || true)"
  [[ -n "${from_env:-}" ]] && svc_name="$from_env"
  [[ -f "/etc/systemd/system/${svc_name}.service" ]] && return 0
  return 1
}

# OS-службы модулей из host_lifecycle.yaml (service_units)
list_module_host_units() {
  local project_root="${1:-}"
  local py script
  [[ -n "$project_root" ]] || return 0
  py="$project_root/virtual_env/python/bin/python"
  script="$project_root/core/deployment/scripts/host_lifecycle_loader.py"
  [[ -x "$py" && -f "$script" ]] || return 0
  "$py" "$script" --root "$project_root" --units 2>/dev/null || true
}

list_module_host_stop_commands() {
  local project_root="${1:-}"
  local py script
  [[ -n "$project_root" ]] || return 0
  py="$project_root/virtual_env/python/bin/python"
  script="$project_root/core/deployment/scripts/host_lifecycle_loader.py"
  [[ -x "$py" && -f "$script" ]] || return 0
  "$py" "$script" --root "$project_root" --stop-commands 2>/dev/null || true
}

# Строки «cmd<TAB>unit1 unit2» — для stop: не дублировать stop_command, если unit уже неактивен.
list_module_host_stop_pairs() {
  local project_root="${1:-}"
  local py script
  [[ -n "$project_root" ]] || return 0
  py="$project_root/virtual_env/python/bin/python"
  script="$project_root/core/deployment/scripts/host_lifecycle_loader.py"
  [[ -x "$py" && -f "$script" ]] || return 0
  "$py" "$script" --root "$project_root" --stop-commands-paired 2>/dev/null || true
}

host_profile_script() {
  echo "${1:-}/core/deployment/lifecycle/host_profile.py"
}

host_profile_python() {
  echo "${1:-}/virtual_env/python/bin/python"
}

host_profile_wants() {
  local project_root="${1:-}"
  local service_id="${2:-}"
  local py script
  py="$(host_profile_python "$project_root")"
  script="$(host_profile_script "$project_root")"
  [[ -x "$py" && -f "$script" ]] || return 0
  "$py" "$script" --root "$project_root" --wants "$service_id" >/dev/null 2>&1
}

host_profile_core_units() {
  local project_root="${1:-}"
  local py script
  py="$(host_profile_python "$project_root")"
  script="$(host_profile_script "$project_root")"
  [[ -x "$py" && -f "$script" ]] || return 0
  "$py" "$script" --root "$project_root" --core-units 2>/dev/null || true
}

# Генерация списка служб на основе конфигурации воркеров
generate_units_list() {
  local project_root="${1:-}"
  local units=""
  local postgres_svc
  local from_env
  local unit
  local module_unit
  local core_unit
  postgres_svc="$(ergo_service_name postgres "$project_root")"

  while IFS= read -r core_unit; do
    [[ -z "$core_unit" ]] && continue
    units="$units ${core_unit}.service"
  done < <(host_profile_core_units "$project_root")

  if is_nginx_enabled "$project_root"; then
    units="$units $(ergo_service_name nginx "$project_root").service"
  fi

  if is_redis_enabled "$project_root"; then
    units="$(ergo_service_name redis "$project_root").service $units"
  fi

  if is_search_enabled "$project_root"; then
    units="$(ergo_service_name meilisearch "$project_root").service $units"
  fi

  if _postgres_portable_enabled "$project_root"; then
    from_env="$(_ergo_env_value "$project_root" 'POSTGRES_SERVICE_LINUX' 2>/dev/null || true)"
    [[ -n "${from_env:-}" ]] && postgres_svc="$from_env"
    units="${postgres_svc}.service $units"
  fi
  
  if host_profile_wants "$project_root" yaml_workers; then
    local workers
    local worker_base
    workers="$(get_celery_workers "$project_root")"
    worker_base="$(ergo_service_name celery_worker "$project_root")"

    if [[ -n "$workers" ]]; then
      for worker in $workers; do
        units="$units ${worker_base}_${worker}.service"
      done
    else
      units="$units ${worker_base}.service"
    fi
  fi

  while IFS= read -r module_unit; do
    [[ -z "$module_unit" ]] && continue
    unit="$module_unit"
    [[ "$unit" == *.service ]] || unit="${unit}.service"
    # Не дублировать ядровые имена
    case " $units " in
      *" $unit "*) continue ;;
    esac
    units="$units $unit"
  done < <(list_module_host_units "$project_root")
  
  echo "$units"
}

# Получение списка имён воркеров (без .service)
get_worker_service_names() {
  local project_root="${1:-}"
  local services=""

  if ! host_profile_wants "$project_root" yaml_workers; then
    echo ""
    return 0
  fi

  local workers
  workers="$(get_celery_workers "$project_root")"

  local worker_base
  worker_base="$(ergo_service_name celery_worker "$project_root")"
  if [[ -n "$workers" ]]; then
    for worker in $workers; do
      services="$services ${worker_base}_${worker}"
    done
  else
    services="$worker_base"
  fi

  echo "$services"
}

units_list() {
  local project_root="${1:-}"
  
  # Кэшируем результат для ускорения
  if [[ -n "$CACHED_UNITS_LIST" ]]; then
    echo "$CACHED_UNITS_LIST"
    return
  fi
  
  CACHED_UNITS_LIST="$(generate_units_list "$project_root")"
  echo "$CACHED_UNITS_LIST"
}

# Сброс кэша списка служб (если нужно перечитать конфиг)
reset_units_cache() {
  CACHED_UNITS_LIST=""
}

cli_name() {
  echo "$CLI_NAME"
}


systemctl_do() {
  if [[ $(id -u) -eq 0 ]]; then
    systemctl "$@"
  else
    sudo systemctl "$@"
  fi
}

daemon_reload() {
  if [[ $(id -u) -eq 0 ]]; then
    systemctl daemon-reload
  else
    sudo systemctl daemon-reload
  fi
}

export -f ergo_service_prefix
export -f ergo_service_name
export -f require_root_or_sudo
export -f restore_project_ownership
export -f write_ergoms_message
export -f write_ergoms_text
export -f detect_project_root
export -f parse_workers_from_yaml
export -f get_celery_workers
export -f list_module_host_units
export -f list_module_host_stop_commands
export -f list_module_host_stop_pairs
export -f host_profile_wants
export -f host_profile_core_units
export -f generate_units_list
export -f get_worker_service_names
export -f units_list
export -f reset_units_cache
export -f cli_name
export -f systemctl_do
export -f daemon_reload
