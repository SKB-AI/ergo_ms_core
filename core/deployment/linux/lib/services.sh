#!/usr/bin/env bash
# Service management functions
# Функции управления службами

# Глобальная переменная для хранения корня проекта в контексте служб
SERVICE_PROJECT_ROOT=""

# Установка корня проекта для операций со службами
set_service_project_root() {
  SERVICE_PROJECT_ROOT="$1"
  # Сбрасываем кэш списка служб при изменении корня
  reset_units_cache
}

_unit_is_present() {
  local unit="$1"
  [[ "$unit" == *.service ]] || unit="${unit}.service"
  [[ -f "/etc/systemd/system/$unit" || -L "/etc/systemd/system/$unit" ]] && return 0
  systemctl list-unit-files --type=service --no-legend "$unit" 2>/dev/null | grep -q .
}

_unit_short_name() {
  local unit="$1"
  unit="${unit%.service}"
  printf '%s' "$unit"
}

# Заполняет массив имён служб для ergoms start/stop/restart (без .service).
# Имена пишет в переданный nameref-массив.
_collect_managed_service_names() {
  local root="$1"
  local -n _names_ref="$2"
  local u
  _names_ref=()
  if is_redis_enabled "$root"; then
    _names_ref+=("Redis")
  fi
  if is_search_enabled "$root"; then
    _names_ref+=("Meilisearch")
  fi
  for u in $(units_list "$root"); do
    [[ "$u" == *_redis.service || "$u" == *_redis ]] && continue
    [[ "$u" == *_meilisearch.service || "$u" == *_meilisearch ]] && continue
    # Манифест модуля может объявлять unit, которого ещё нет в systemd.
    _unit_is_present "$u" || continue
    _names_ref+=("$(_unit_short_name "$u")")
  done
}

# Склеивает имена через ", " (аргументы — элементы массива; безопасно в $()).
_join_csv_names() {
  local out="" n
  for n in "$@"; do
    [[ -n "$out" ]] && out+=", "
    out+="$n"
  done
  printf '%s' "$out"
}

start_all() {
  local root="${SERVICE_PROJECT_ROOT:-}"
  local u name err items
  local -a planned=()
  local started=0 already=0 missing=0 failed=0

  _collect_managed_service_names "$root" planned
  items="$(_join_csv_names "${planned[@]}")"
  write_ergoms_message svc_starting_all cyan "" \
    "count=${#planned[@]}" "items=$items"

  # Redis при ERGO_BROKER=redis — через redis_start (служба или процесс)
  if is_redis_enabled "$root" && declare -F redis_start >/dev/null 2>&1; then
    if systemctl is-active --quiet "$(ergo_service_name redis).service" 2>/dev/null; then
      redis_start "$root" || true
      already=$((already + 1))
    elif redis_start "$root"; then
      started=$((started + 1))
    else
      failed=$((failed + 1))
    fi
  fi

  # Meilisearch при ERGO_SEARCH_ENABLED — через meilisearch_start
  if is_search_enabled "$root" && declare -F meilisearch_start >/dev/null 2>&1; then
    if systemctl is-active --quiet "$(ergo_service_name meilisearch).service" 2>/dev/null; then
      meilisearch_start "$root" || true
      already=$((already + 1))
    elif meilisearch_start "$root"; then
      started=$((started + 1))
    else
      failed=$((failed + 1))
    fi
  fi

  local -a pending=()
  for u in $(units_list "$root"); do
    # Redis / Meilisearch уже обработаны отдельно
    [[ "$u" == *_redis.service || "$u" == *_redis ]] && continue
    [[ "$u" == *_meilisearch.service || "$u" == *_meilisearch ]] && continue
    name="$(_unit_short_name "$u")"
    if ! _unit_is_present "$u"; then
      continue
    fi
    if systemctl is-active --quiet "$u" 2>/dev/null; then
      write_ergoms_message ok_service_already_running green "" "name=$name"
      already=$((already + 1))
      continue
    fi
    pending+=("$u")
  done
  if ((${#pending[@]})); then
    systemctl_do start "${pending[@]}" >/dev/null 2>&1 || true
    for u in "${pending[@]}"; do
      name="$(_unit_short_name "$u")"
      if systemctl is-active --quiet "$u" 2>/dev/null; then
        write_ergoms_message svc_started_ok green "" "name=$name"
        started=$((started + 1))
      else
        err="$(systemctl is-failed "$u" 2>/dev/null || true)"
        write_ergoms_message svc_start_failed red --stderr "name=$name" "error=${err:-systemctl start failed}"
        failed=$((failed + 1))
      fi
    done
  fi

  write_ergoms_message svc_start_summary green "" \
    "started=$started" "already=$already" "missing=$missing" "failed=$failed"
}

# Собирает PID текущего процесса и всех предков (чтобы не завершить shell, запустивший ergoms stop).
_kill_ergo_skip_pids_ancestor_chain() {
  local p=$$ n=0 max=64
  while [[ "$p" -gt 1 && "$n" -lt "$max" ]]; do
    printf '%s\n' "$p"
    p=$(awk '/^PPid:/{print $2}' "/proc/$p/status" 2>/dev/null || echo 1)
    [[ -z "$p" || "$p" == 0 ]] && break
    ((n++)) || true
  done
}

# Долгий одноразовый прогон (не служба ergo_ms_*): маркер в cmdline или у предка.
# Нужен, чтобы ergoms stop не рвал расчёт аналитики после закрытия IDE.
_cmdline_is_keep_alive() {
  local c="$1"
  [[ -n "$c" ]] || return 1
  [[ "$c" == *"ergo-keep-alive"* ]] && return 0
  return 1
}

# Терминалы «Logs: All Services» / start-db-dev / module:logs-* — не останавливать вместе со службами.
_cmdline_is_log_watcher() {
  local c="$1"
  [[ -n "$c" ]] || return 1
  # ergoms logs <service> / bash ergo_ms.sh logs …
  [[ "$c" == *"ergoms logs"* || "$c" == *"ergo_ms.sh logs"* ]] && return 0
  # Модульные хвосты: ergoms <module>:logs-<name>
  [[ "$c" == *":logs-"* ]] && return 0
  # Логи default БД (VS Code Start All / Logs)
  [[ "$c" == *"start-db-dev"* || "$c" == *"start_db_logs_dev.py"* ]] && return 0
  # docker logs -f (в т.ч. дочерний процесс start-db-dev)
  [[ "$c" == *"docker_cli.py"* && "$c" == *" logs"* ]] && return 0
  [[ "$c" == *"docker-logs"* ]] && return 0
  # Скрипты модулей deployment/logs_*.py
  [[ "$c" == */logs_*.py* ]] && return 0
  # Pipeline show_*_logs: tail -F / -f <файл под корнем проекта>
  if [[ "$c" =~ (^|[[:space:]/])tail([[:space:]]|$) ]]; then
    if [[ "$c" == *" -F"* || "$c" == *"-F "* || "$c" == *" -f"* || "$c" == *"-f "* ]]; then
      return 0
    fi
  fi
  return 1
}

_read_proc_cmdline() {
  local proc_dir="$1"
  local cmd=""
  { cmd="$(tr '\0' ' ' <"$proc_dir/cmdline")"; } 2>/dev/null || return 1
  [[ -n "$cmd" ]] || return 1
  printf '%s' "$cmd"
}

# true, если процесс или его предок — просмотр логов или ergo-keep-alive (не убивать при stop).
_pid_in_log_watch_session() {
  local pid="$1"
  local -n _skip_ref="$2"
  local cmd ppid n=0 max=48
  while [[ "$pid" -gt 1 && "$n" -lt "$max" ]]; do
    # Дошли до цепочки самого ergoms stop — это не log-терминал.
    [[ -v "_skip_ref[$pid]" ]] && return 1
    cmd="$(_read_proc_cmdline "/proc/$pid")" || return 1
    if _cmdline_is_log_watcher "$cmd" || _cmdline_is_keep_alive "$cmd"; then
      return 0
    fi
    ppid="$(awk '/^PPid:/{print $2}' "/proc/$pid/status" 2>/dev/null || echo 1)"
    [[ -z "$ppid" || "$ppid" == 0 ]] && break
    pid="$ppid"
    ((n++)) || true
  done
  return 1
}

# Завершает оставшиеся после systemctl пользовательские процессы этого репозитория (Daphne, Celery, Vite,
# фоновые ergoms/npm и т.д.) по наличию абсолютного пути корня или virtual_env в cmdline.
# Сессии просмотра логов (VS Code Logs: All Services и т.п.) не трогает.
kill_ergo_project_session_processes() {
  local root="${1:-${SERVICE_PROJECT_ROOT:-}}"
  [[ -n "$root" ]] || return 0
  [[ -d /proc ]] || return 0
  if command -v readlink >/dev/null 2>&1; then
    root="$(readlink -f "$root" 2>/dev/null || echo "$root")"
  fi
  local venv_mark="${root}/virtual_env"
  local proc pid cmd round
  local -A skip=()
  while IFS= read -r pid; do
    [[ -n "$pid" ]] && skip["$pid"]=1
  done < <(_kill_ergo_skip_pids_ancestor_chain)

  _cmdline_matches_project() {
    local c="$1"
    [[ -n "$c" ]] || return 1
    [[ "$c" == *"$root"* ]] && return 0
    [[ "$c" == *"$venv_mark"* ]] && return 0
    return 1
  }

  for round in 1 2; do
    for proc in /proc/[0-9]*; do
      [[ -e "$proc/cmdline" ]] || continue
      pid="${proc##/proc/}"
      [[ "$pid" =~ ^[0-9]+$ ]] || continue
      [[ -v "skip[$pid]" ]] && continue
      cmd="$(_read_proc_cmdline "$proc")" || continue
      _cmdline_matches_project "$cmd" || continue
      _pid_in_log_watch_session "$pid" skip && continue
      if [[ "$round" -eq 1 ]]; then
        kill -TERM "$pid" 2>/dev/null || true
      else
        kill -0 "$pid" 2>/dev/null || continue
        cmd="$(_read_proc_cmdline "$proc")" || continue
        _cmdline_matches_project "$cmd" || continue
        _pid_in_log_watch_session "$pid" skip && continue
        kill -KILL "$pid" 2>/dev/null || true
      fi
    done
    if [[ "$round" -eq 1 ]]; then
      sleep 0.8
    fi
  done
  return 0
}

# systemd по умолчанию ждёт TimeoutStopSec=90 с на каждый unit. Celery/Ollama
# часто не выходят по SIGTERM сразу — последовательный stop тогда тянется минутами.
_SYSTEMD_STOP_WAIT_SEC=30
export _SYSTEMD_STOP_WAIT_SEC

_units_still_busy() {
  local states st
  [[ $# -eq 0 ]] && return 1
  states="$(systemctl show -p ActiveState --value -- "$@" 2>/dev/null || true)"
  while IFS= read -r st; do
    [[ -z "$st" ]] && continue
    case "$st" in
      inactive|failed) ;;
      *) return 0 ;;
    esac
  done <<< "$states"
  return 1
}

_stop_units_parallel() {
  local -a units=("$@") leftover=()
  local started st idx
  [[ ${#units[@]} -eq 0 ]] && return 0
  systemctl_do stop --no-block "${units[@]}" >/dev/null 2>&1 || true
  started="$SECONDS"
  while (( SECONDS - started < _SYSTEMD_STOP_WAIT_SEC )); do
    _units_still_busy "${units[@]}" || return 0
    sleep 0.25
  done
  idx=0
  while IFS= read -r st; do
    [[ -z "$st" ]] && continue
    case "$st" in
      inactive|failed) ;;
      *) leftover+=("${units[$idx]}") ;;
    esac
    idx=$((idx + 1))
  done < <(systemctl show -p ActiveState --value -- "${units[@]}" 2>/dev/null || true)
  if ((${#leftover[@]})); then
    systemctl_do kill --kill-whom=all -s SIGKILL "${leftover[@]}" >/dev/null 2>&1 || true
    systemctl_do stop "${leftover[@]}" >/dev/null 2>&1 || true
  fi
}

stop_all() {
  local root="${SERVICE_PROJECT_ROOT:-}"
  local u name items
  local -a planned=() pending=()
  local stopped=0 skipped=0 missing=0 failed=0
  # Unit'ы с Requires= гаснут каскадом при stop зависимости — снимок, чтобы не писать «уже остановлены».
  declare -A _stop_was_active=()

  _collect_managed_service_names "$root" planned
  items="$(_join_csv_names "${planned[@]}")"
  write_ergoms_message svc_stopping_all cyan "" \
    "count=${#planned[@]}" "items=$items"

  for u in $(units_list "$root"); do
    [[ "$u" == *_redis.service || "$u" == *_redis ]] && continue
    [[ "$u" == *_meilisearch.service || "$u" == *_meilisearch ]] && continue
    name="$(_unit_short_name "$u")"
    if ! _unit_is_present "$u"; then
      continue
    fi
    if systemctl is-active --quiet "$u" 2>/dev/null; then
      _stop_was_active["$name"]=1
      pending+=("$u")
    fi
  done

  # Один systemctl stop — как start_all, иначе 20+ unit'ов ждут друг друга.
  if ((${#pending[@]})); then
    _stop_units_parallel "${pending[@]}"
  fi

  for u in $(units_list "$root"); do
    [[ "$u" == *_redis.service || "$u" == *_redis ]] && continue
    [[ "$u" == *_meilisearch.service || "$u" == *_meilisearch ]] && continue
    name="$(_unit_short_name "$u")"
    if ! _unit_is_present "$u"; then
      continue
    fi
    if [[ -z "${_stop_was_active[$name]:-}" ]]; then
      skipped=$((skipped + 1))
      continue
    fi
    if systemctl is-active --quiet "$u" 2>/dev/null; then
      write_ergoms_message svc_stop_failed red --stderr "name=$name" "error=systemctl stop failed"
      failed=$((failed + 1))
      continue
    fi
    write_ergoms_message svc_stopped_ok green "" "name=$name"
    stopped=$((stopped + 1))
  done

  # Модульные stop_commands — только если нет установленных unit'ов или какой-то ещё active.
  # Иначе после systemctl stop остаётся ложный «процесс не найден».
  if [[ -n "$root" && -d "$root" ]] && command -v ergoms >/dev/null 2>&1; then
    local pair_cmd pair_units unit_item need_stop
    while IFS=$'\t' read -r pair_cmd pair_units; do
      [[ -z "$pair_cmd" ]] && continue
      need_stop=1
      if [[ -n "${pair_units:-}" ]]; then
        need_stop=0
        for unit_item in $pair_units; do
          [[ "$unit_item" == *.service ]] || unit_item="${unit_item}.service"
          if ! _unit_is_present "$unit_item"; then
            need_stop=1
            break
          fi
          if systemctl is-active --quiet "$unit_item" 2>/dev/null; then
            need_stop=1
            break
          fi
        done
      fi
      [[ "$need_stop" -eq 1 ]] || continue
      ( cd "$root" && ergoms "$pair_cmd" ) || true
    done < <(list_module_host_stop_pairs "$root")
  fi

  if is_search_enabled "$root" && declare -F meilisearch_stop >/dev/null 2>&1; then
    if declare -F _meilisearch_is_running >/dev/null 2>&1 && _meilisearch_is_running "$root"; then
      if meilisearch_stop "$root"; then
        stopped=$((stopped + 1))
      else
        failed=$((failed + 1))
      fi
    else
      # Без повторного SKIP в консоли — учитываем в итоге.
      skipped=$((skipped + 1))
    fi
  fi

  if is_redis_enabled "$root" && declare -F redis_stop >/dev/null 2>&1; then
    if declare -F _redis_is_running >/dev/null 2>&1 && _redis_is_running "$root"; then
      if redis_stop "$root"; then
        stopped=$((stopped + 1))
      else
        failed=$((failed + 1))
      fi
    else
      skipped=$((skipped + 1))
    fi
  fi

  write_ergoms_message svc_stop_summary green "" \
    "stopped=$stopped" "skipped=$skipped" "missing=$missing" "failed=$failed"

  # Иначе kill_ergo_project_session_processes рвёт node/vite — npm печатает ERR на «упавший» lifecycle.
  if [[ -n "$root" && -d "$root" ]] && command -v ergoms >/dev/null 2>&1; then
    ( cd "$root" && ergoms npm run stop-dev ) || true
  fi
  kill_ergo_project_session_processes "$root" || true
}

restart_all() {
  local root="${SERVICE_PROJECT_ROOT:-}"
  local u name err items
  local -a planned=() pending=()
  local restarted=0 missing=0 failed=0

  _collect_managed_service_names "$root" planned
  items="$(_join_csv_names "${planned[@]}")"
  write_ergoms_message svc_restarting_all cyan "" \
    "count=${#planned[@]}" "items=$items"

  if is_redis_enabled "$root" && declare -F redis_restart >/dev/null 2>&1; then
    if redis_restart "$root"; then
      restarted=$((restarted + 1))
    else
      failed=$((failed + 1))
    fi
  fi

  if is_search_enabled "$root" && declare -F meilisearch_restart >/dev/null 2>&1; then
    if meilisearch_restart "$root"; then
      restarted=$((restarted + 1))
    else
      failed=$((failed + 1))
    fi
  fi

  for u in $(units_list "$root"); do
    [[ "$u" == *_redis.service || "$u" == *_redis ]] && continue
    [[ "$u" == *_meilisearch.service || "$u" == *_meilisearch ]] && continue
    if ! _unit_is_present "$u"; then
      continue
    fi
    pending+=("$u")
  done
  if ((${#pending[@]})); then
    systemctl_do restart "${pending[@]}" >/dev/null 2>&1 || true
    for u in "${pending[@]}"; do
      name="$(_unit_short_name "$u")"
      if systemctl is-active --quiet "$u" 2>/dev/null; then
        write_ergoms_message svc_restarted_ok green "" "name=$name"
        restarted=$((restarted + 1))
      else
        err="$(systemctl is-failed "$u" 2>/dev/null || true)"
        write_ergoms_message svc_restart_failed red --stderr "name=$name" "error=${err:-systemctl restart failed}"
        failed=$((failed + 1))
      fi
    done
  fi

  write_ergoms_message svc_restart_summary green "" \
    "restarted=$restarted" "missing=$missing" "failed=$failed"
}

status_all() {
  local root="${SERVICE_PROJECT_ROOT:-}"
  local u
  for u in $(units_list "$root"); do
    if _unit_is_present "$u"; then
      # systemctl status: 0 — active, 3 — inactive. Для просмотра это не ошибка;
      # иначе set -e / pipefail рвёт install-services до шага nginx.
      systemctl_do --no-pager status "$u" || true
    else
      echo "[SKIP] $u — unit не установлен"
    fi
  done
  if is_redis_enabled "$root" && declare -F redis_status >/dev/null 2>&1; then
    # Если unit уже выведен выше — redis_status дублирует; ок для процесса без unit
    if ! _unit_is_present "$(ergo_service_name redis)"; then
      redis_status "$root" || true
    fi
  fi
}

show_celery_tasks_logs() {
  local module_name="${1:-}"
  local lines="${2:-500}"
  local root="${SERVICE_PROJECT_ROOT:-}"

  if [[ -z "$root" ]]; then
    root="$(detect_project_root 2>/dev/null || pwd)"
  fi

  # shellcheck source=lib/logs_paths.sh
  source "$(dirname "${BASH_SOURCE[0]}")/logs_paths.sh"

  local log_file
  log_file="$(resolve_ergo_logs_dir "$root")/celery_tasks.log"
  if [[ ! -f "$log_file" ]]; then
    write_ergoms_message svc_celery_tasks_log_missing red --stderr "path=$log_file"
    return 1
  fi

  write_ergoms_message svc_tail_celery_tasks cyan "" "lines=$lines"
  echo "   $log_file"
  if [[ -n "$module_name" ]]; then
    local pattern="celery\\.module\\.${module_name}|modules\\.${module_name}"
    write_ergoms_message svc_log_filter gray "" "pattern=$pattern"
    echo ""
    grep -E "$pattern" "$log_file" 2>/dev/null | tail -n "$lines" || true
    tail -n 0 -F "$log_file" | grep --line-buffered -E "$pattern" | cat
    return 0
  fi

  echo ""
  tail -n "$lines" -F "$log_file" | cat
}

show_celery_beat_logs() {
  local module_name="${1:-}"
  local lines="${2:-500}"
  local root="${SERVICE_PROJECT_ROOT:-}"

  if [[ -z "$root" ]]; then
    root="$(detect_project_root 2>/dev/null || pwd)"
  fi

  # shellcheck source=lib/logs_paths.sh
  source "$(dirname "${BASH_SOURCE[0]}")/logs_paths.sh"

  local log_file
  log_file="$(resolve_ergo_logs_dir "$root")/celery_beat.log"
  if [[ ! -f "$log_file" ]]; then
    write_ergoms_message svc_celery_beat_log_missing red --stderr "path=$log_file"
    return 1
  fi

  write_ergoms_message svc_tail_celery_beat cyan "" "lines=$lines"
  echo "   $log_file"
  if [[ -n "$module_name" ]]; then
    local pattern="celery\\.beat\\.module\\.${module_name}"
    write_ergoms_message svc_log_filter gray "" "pattern=$pattern"
    echo ""
    grep -E "$pattern" "$log_file" 2>/dev/null | tail -n "$lines" || true
    tail -n 0 -F "$log_file" | grep --line-buffered -E "$pattern" | cat
    return 0
  fi

  echo ""
  tail -n "$lines" -F "$log_file" | cat
}

show_service_logs() {
  local service_name="$1"
  local lines="${2:-500}"
  local root="${SERVICE_PROJECT_ROOT:-}"

  if [[ -z "$root" ]]; then
    root="$(detect_project_root 2>/dev/null || pwd)"
  fi

  # shellcheck source=lib/logs_paths.sh
  source "$(dirname "${BASH_SOURCE[0]}")/logs_paths.sh"

  case "$service_name" in
    *_postgres|*_postgres.service|ergo-postgres|ergo-postgres.service|*_db|*_sqlite|*_mysql|*_mssql)
      local db_py="$root/virtual_env/python/bin/python"
      local db_script="$root/core/deployment/scripts/start_db_logs_dev.py"
      if [[ -x "$db_py" && -f "$db_script" ]]; then
        exec "$db_py" -u "$db_script"
      fi
      ;;
  esac

  if [[ "$service_name" == *_nginx || "$service_name" == *_nginx.service ]]; then
    local nginx_py="$root/virtual_env/python/bin/python"
    local nginx_script="$root/core/deployment/scripts/start_nginx_logs_dev.py"
    if [[ -x "$nginx_py" && -f "$nginx_script" ]]; then
      exec "$nginx_py" -u "$nginx_script" "$lines"
    fi
    local files=()
    while IFS= read -r file; do
      [[ -n "$file" && -f "$file" ]] && files+=("$file")
    done < <(resolve_service_log_files "$(ergo_service_name nginx "$root")" "$root" 2>/dev/null || true)
    if [[ ${#files[@]} -eq 0 ]]; then
      write_ergoms_message svc_nginx_logs_missing red --stderr "path=$(resolve_ergo_logs_dir "$root")"
      write_ergoms_message svc_nginx_logs_hint yellow --stderr
      return 1
    fi
    write_ergoms_message svc_tail_nginx_logs cyan "" "lines=$lines"
    echo ""
    tail -n "$lines" -q -F "${files[@]}" | cat
    return 0
  fi

  if [[ "$service_name" == *_redis || "$service_name" == *_redis.service || "$service_name" == "ergo-redis" || "$service_name" == "ergo-redis.service" ]]; then
    service_name="$(ergo_service_name redis "$root")"
  fi

  if [[ "$service_name" == *_meilisearch || "$service_name" == *_meilisearch.service ]]; then
    service_name="$(ergo_service_name meilisearch "$root")"
  fi

  local -a log_files=()
  while IFS= read -r file; do
    [[ -n "$file" ]] && log_files+=("$file")
  done < <(resolve_service_log_files "$service_name" "$root" 2>/dev/null || true)

  if [[ ${#log_files[@]} -eq 0 ]]; then
    write_ergoms_message svc_service_logs_missing red --stderr "name=$service_name"
    write_ergoms_message svc_logs_expected_in yellow --stderr "path=$(resolve_ergo_logs_dir "$root")"
    return 1
  fi

  write_ergoms_message svc_tail_service_logs cyan "" "lines=$lines" "name=$service_name"
  for file in "${log_files[@]}"; do
    echo "   $file"
  done
  if [[ "$service_name" == *_celery_worker* ]]; then
    write_ergoms_message svc_module_tasks_log_hint gray "" "path=$(resolve_ergo_logs_dir "$root")/celery_tasks.log"
    write_ergoms_message svc_filter_celery_tasks_cmd gray
  fi
  if [[ "$service_name" == *_celery_beat || "$service_name" == *_celery_beat.service ]]; then
    write_ergoms_message svc_module_beat_log_hint gray "" "path=$(resolve_ergo_logs_dir "$root")/celery_beat.log"
    write_ergoms_message svc_filter_celery_beat_cmd gray
  fi
  echo ""
  tail -n "$lines" -F "${log_files[@]}" | cat
}

uninstall_all() {
  local purge="$1"
  local root="${SERVICE_PROJECT_ROOT:-}"
  
  stop_all || true
  local u
  for u in $(units_list "$root"); do
    _unit_is_present "$u" || continue
    systemctl_do disable "$u" || true
  done
  for u in $(units_list "$root"); do
    if [[ -f "/etc/systemd/system/$u" ]]; then
      if [[ $(id -u) -eq 0 ]]; then
        rm -f "/etc/systemd/system/$u"
      else
        sudo rm -f "/etc/systemd/system/$u"
      fi
      write_ergoms_message svc_unit_removed gray "" "path=/etc/systemd/system/$u"
    fi
  done

  # Старые имена ergo-* — только у рабочего префикса
  local prefix
  prefix="$(ergo_service_prefix "$root")"
  if [[ "$prefix" == "ergo_ms" ]]; then
    local legacy
    for legacy in \
      ergo-api-dev.service \
      ergo-client-dev.service \
      ergo-media-api.service \
      ergo-celery-beat.service \
      ergo-celery-worker.service \
      ergo-redis.service \
      ergo-postgres.service
    do
      systemctl_do disable "$legacy" 2>/dev/null || true
      systemctl_do stop "$legacy" 2>/dev/null || true
      if [[ -f "/etc/systemd/system/$legacy" ]] || [[ -L "/etc/systemd/system/$legacy" ]]; then
        if [[ $(id -u) -eq 0 ]]; then
          rm -f "/etc/systemd/system/$legacy"
        else
          sudo rm -f "/etc/systemd/system/$legacy"
        fi
        write_ergoms_message svc_legacy_unit_removed gray "" "path=/etc/systemd/system/$legacy"
      fi
    done
    while IFS= read -r legacy; do
      [[ -z "$legacy" ]] && continue
      systemctl_do disable "$legacy" 2>/dev/null || true
      systemctl_do stop "$legacy" 2>/dev/null || true
      if [[ -f "/etc/systemd/system/$legacy" ]] || [[ -L "/etc/systemd/system/$legacy" ]]; then
        if [[ $(id -u) -eq 0 ]]; then
          rm -f "/etc/systemd/system/$legacy"
        else
          sudo rm -f "/etc/systemd/system/$legacy"
        fi
        write_ergoms_message svc_legacy_unit_removed gray "" "path=/etc/systemd/system/$legacy"
      fi
    done < <(systemctl list-unit-files --type=service --no-legend 2>/dev/null | awk '/^ergo-celery-worker-/ {print $1}')
  fi
  
  daemon_reload
  if [[ "$purge" == "true" ]]; then
    if [[ -f "/etc/default/ergo_ms" ]]; then
      if [[ $(id -u) -eq 0 ]]; then
        rm -f "/etc/default/ergo_ms"
      else
        sudo rm -f "/etc/default/ergo_ms"
      fi
      write_ergoms_message svc_unit_removed gray "" "path=/etc/default/ergo_ms"
    fi
  fi
}

disable_client_service_if_nginx() {
  local root="$1"
  if ! is_nginx_enabled "$root"; then
    return 0
  fi
  local client_unit
  client_unit="$(ergo_service_name client_dev "$root").service"
  systemctl_do disable "$client_unit" 2>/dev/null || true
  systemctl_do stop "$client_unit" 2>/dev/null || true
  nginx_skip_client_message "$root"
}

_remove_unit_if_present() {
  local unit="$1"
  _unit_is_present "$unit" || return 0
  systemctl_do stop "$unit" 2>/dev/null || true
  systemctl_do disable "$unit" 2>/dev/null || true
  if [[ -f "/etc/systemd/system/$unit" || -L "/etc/systemd/system/$unit" ]]; then
    if [[ $(id -u) -eq 0 ]]; then
      rm -f "/etc/systemd/system/$unit"
    else
      sudo rm -f "/etc/systemd/system/$unit"
    fi
    write_ergoms_message svc_unit_removed gray "" "path=/etc/systemd/system/$unit"
  fi
}

remove_stale_host_profile_units() {
  local root="$1"
  local unit workers worker

  local worker_base
  worker_base="$(ergo_service_name celery_worker "$root")"
  if ! host_profile_wants "$root" api; then
    _remove_unit_if_present "$(ergo_service_name api_dev "$root").service"
  fi
  if ! host_profile_wants "$root" client; then
    _remove_unit_if_present "$(ergo_service_name client_dev "$root").service"
  fi
  if ! host_profile_wants "$root" media; then
    _remove_unit_if_present "$(ergo_service_name media_api "$root").service"
  fi
  if ! host_profile_wants "$root" beat; then
    _remove_unit_if_present "$(ergo_service_name celery_beat "$root").service"
  fi
  if ! host_profile_wants "$root" yaml_workers; then
    workers="$(get_celery_workers "$root")"
    if [[ -n "$workers" ]]; then
      for worker in $workers; do
        _remove_unit_if_present "${worker_base}_${worker}.service"
      done
    fi
    _remove_unit_if_present "${worker_base}.service"
  fi
}

install_services() {
  local root="$1"
  local skip_client=0
  is_nginx_enabled "$root" && skip_client=1
  
  echo ""
  write_ergoms_message svc_install_heading cyan
  echo ""
  
  cd "$root" || exit 1
  
  # Устанавливаем корень для операций со службами
  set_service_project_root "$root"
  
  write_env_file "$root"
  
  # Получаем базовые unit definitions
  get_base_unit_definitions "$root"

  reset_units_cache
  remove_stale_host_profile_units "$root"
  
  if host_profile_wants "$root" api; then
    install_unit "$(ergo_service_name api_dev "$root")"        "$API_UNIT" "$root"
  fi
  if host_profile_wants "$root" client; then
    if (( skip_client == 0 )); then
      install_unit "$(ergo_service_name client_dev "$root")"     "$CLIENT_UNIT" "$root"
    else
      disable_client_service_if_nginx "$root"
    fi
  else
    disable_client_service_if_nginx "$root"
  fi
  if host_profile_wants "$root" media; then
    install_unit "$(ergo_service_name media_api "$root")"      "$MEDIA_API_UNIT" "$root"
  fi
  if host_profile_wants "$root" beat; then
    install_unit "$(ergo_service_name celery_beat "$root")"    "$CELERY_BEAT_UNIT" "$root"
  fi
  
  if host_profile_wants "$root" yaml_workers; then
    install_worker_units "$root"
  fi

  daemon_reload

  if host_profile_wants "$root" api; then
    enable_and_start "$(ergo_service_name api_dev "$root").service"
  fi
  if host_profile_wants "$root" client && (( skip_client == 0 )); then
    enable_and_start "$(ergo_service_name client_dev "$root").service"
  fi
  if host_profile_wants "$root" media; then
    enable_and_start "$(ergo_service_name media_api "$root").service"
  fi
  if host_profile_wants "$root" beat; then
    enable_and_start "$(ergo_service_name celery_beat "$root").service"
  fi
  
  if host_profile_wants "$root" yaml_workers; then
    enable_and_start_workers "$root"
  fi

  echo ""
  write_ergoms_message svc_installed_running_heading green
  echo ""
  status_all
  echo ""
  write_ergoms_message svc_services_started_bang green
}

install_single_service() {
  local service_name="$1"
  local root="$2"
  
  echo ""
  write_ergoms_message svc_install_one_heading cyan "" "name=$service_name"
  echo ""
  
  cd "$root" || exit 1
  
  # Устанавливаем корень для операций со службами
  set_service_project_root "$root"
  
  write_env_file "$root"
  
  # Получаем базовые unit definitions
  get_base_unit_definitions "$root"
  
  local unit_name=""
  
  case "$service_name" in
    "api")
      unit_name="$(ergo_service_name api_dev "$root")"
      install_unit "$unit_name" "$API_UNIT" "$root"
      ;;
    "client")
      if is_nginx_enabled "$root"; then
        disable_client_service_if_nginx "$root"
        return 0
      fi
      unit_name="$(ergo_service_name client_dev "$root")"
      install_unit "$unit_name" "$CLIENT_UNIT" "$root"
      ;;
    "worker")
      # Устанавливаем все воркеры из конфигурации
      install_worker_units "$root"
      daemon_reload
      enable_and_start_workers "$root"
      echo ""
      write_ergoms_message svc_workers_installed_running_heading green
      echo ""
      status_all
      echo ""
      write_ergoms_message svc_workers_started_bang green
      return
      ;;
    "beat")
      unit_name="$(ergo_service_name celery_beat "$root")"
      install_unit "$unit_name" "$CELERY_BEAT_UNIT" "$root"
      ;;
    "media")
      unit_name="$(ergo_service_name media_api "$root")"
      install_unit "$unit_name" "$MEDIA_API_UNIT" "$root"
      ;;
    *)
      write_ergoms_message unknown_service red --stderr "name=$service_name"
      exit 1
      ;;
  esac

  daemon_reload
  enable_and_start "${unit_name}.service"

  echo ""
  write_ergoms_message svc_one_installed_running_heading green "" "name=$service_name"
  echo ""
  status_all
  echo ""
  write_ergoms_message svc_one_started_bang green "" "name=$service_name"
}

export -f remove_stale_host_profile_units
export -f disable_client_service_if_nginx
export -f set_service_project_root
export -f start_all
export -f _units_still_busy
export -f _stop_units_parallel
export -f stop_all
export -f restart_all
export -f status_all
export -f show_service_logs
export -f uninstall_all
export -f install_services
export -f install_single_service
