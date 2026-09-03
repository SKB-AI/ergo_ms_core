# Clean project dependencies helpers
# Вспомогательные функции для очистки зависимостей проекта

stop_blocking_processes_for_clean() {
  local root="$1"
  local venv_python="$root/virtual_env/python"
  local packages_dir="$root/virtual_env/packages"
  local stopped=0

  write_ergoms_message clean_stopping_blockers gray

  # Службы текущего корня — {prefix}_*; ergo-* только у префикса по умолчанию
  if command -v systemctl >/dev/null 2>&1; then
    local prefix pattern
    prefix="$(ergo_service_prefix "$root")"
    pattern="${prefix}_"
    while IFS= read -r unit; do
      [[ -z "$unit" ]] && continue
      if systemctl is-active --quiet "$unit" 2>/dev/null; then
        if systemctl stop "$unit" 2>/dev/null; then
          write_ergoms_message clean_service_stopped gray "" "name=$unit"
          stopped=1
        else
          write_ergoms_message clean_warn_stop_service_root yellow --stderr "name=$unit"
        fi
      fi
    done < <(
      systemctl list-units --type=service --all --no-legend 2>/dev/null | awk -v p="$pattern" '
        index($1, p) { print $1 }
      '
    )
    if [[ "$prefix" == "ergo_ms" ]]; then
      while IFS= read -r unit; do
        [[ -z "$unit" ]] && continue
        if systemctl is-active --quiet "$unit" 2>/dev/null; then
          if systemctl stop "$unit" 2>/dev/null; then
            write_ergoms_message clean_service_stopped gray "" "name=$unit"
            stopped=1
          else
            write_ergoms_message clean_warn_stop_service_root yellow --stderr "name=$unit"
          fi
        fi
      done < <(systemctl list-units --type=service --all --no-legend 2>/dev/null | awk '/^ergo-/ {print $1}')
    fi
  fi

  if command -v fuser >/dev/null 2>&1; then
    if [[ -d "$venv_python" ]] && fuser -k "$venv_python" 2>/dev/null; then
      stopped=1
    fi
    if [[ -d "$packages_dir" ]] && fuser -k "$packages_dir" 2>/dev/null; then
      stopped=1
    fi
  fi

  if command -v pkill >/dev/null 2>&1; then
    if pkill -f "${venv_python}" 2>/dev/null; then
      stopped=1
    fi
    if pkill -f "${packages_dir}" 2>/dev/null; then
      stopped=1
    fi
    if pkill -f "${root}/virtual_env/npm/node_modules" 2>/dev/null; then
      stopped=1
    fi
    if pkill -f "${root}/node_modules" 2>/dev/null; then
      stopped=1
    fi
  fi

  if [[ "$stopped" -eq 1 ]]; then
    sleep 0.8
  fi
}

clear_project_shell_environment() {
  local venv_path="$1"
  local venv_norm
  venv_norm="$(cd "$venv_path" 2>/dev/null && pwd -P)" || {
    unset VIRTUAL_ENV 2>/dev/null || true
    return 0
  }

  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    local active_norm
    active_norm="$(cd "$VIRTUAL_ENV" 2>/dev/null && pwd -P)" || active_norm="$VIRTUAL_ENV"
    if [[ "$active_norm" == "$venv_norm" ]]; then
      unset VIRTUAL_ENV
      write_ergoms_message clean_venv_cleared gray
    fi
  fi

  if [[ -n "${PATH:-}" ]]; then
    local scripts_path="${venv_norm}/bin"
    local new_path=""
    local part norm
    IFS=':' read -r -a path_parts <<< "$PATH"
    for part in "${path_parts[@]}"; do
      [[ -z "$part" ]] && continue
      norm="$(cd "$part" 2>/dev/null && pwd -P)" || norm="$part"
      if [[ "$norm" == "$scripts_path" ]]; then
        continue
      fi
      if [[ -z "$new_path" ]]; then
        new_path="$part"
      else
        new_path="${new_path}:$part"
      fi
    done
    PATH="$new_path"
    export PATH
  fi
}

remove_path_robust() {
  local path="$1"
  local max_retries="${2:-3}"
  local attempt

  [[ -e "$path" ]] || return 0

  for ((attempt = 1; attempt <= max_retries; attempt++)); do
    if rm -rf "$path" 2>/dev/null; then
      return 0
    fi
    sleep 0.4
  done

  [[ ! -e "$path" ]]
}

clean_target_has_work() {
  local path="$1"
  local full_remove="$2"

  if [[ ! -e "$path" ]]; then
    return 1
  fi

  if [[ "$full_remove" == "1" ]]; then
    return 0
  fi

  local item
  while IFS= read -r -d '' item; do
    return 0
  done < <(find "$path" -mindepth 1 -maxdepth 1 ! -name '.gitkeep' -print0 2>/dev/null)

  return 1
}

new_clean_trash_staging() {
  local root="$1"
  local staging
  # Каталог в корне проекта — тот же том, что и цели (мгновенный mv)
  staging="$root/.ergo_clean_trash_$$_$RANDOM"
  mkdir -p "$staging"
  printf '%s' "$staging"
}

move_path_to_clean_trash() {
  local path="$1"
  local staging_root="$2"
  local leaf dest

  [[ -e "$path" ]] || return 1

  leaf="$(basename "$path")"
  dest="$staging_root/$leaf"
  if [[ -e "$dest" ]]; then
    dest="$staging_root/${leaf}_$$_$RANDOM"
  fi

  if mv "$path" "$dest" 2>/dev/null; then
    printf '%s' "$dest"
    return 0
  fi
  return 1
}

start_background_trash_removal() {
  local staging_root="$1"
  [[ -n "$staging_root" && -d "$staging_root" ]] || return 0
  (rm -rf "$staging_root" >/dev/null 2>&1 &)
}

restore_clean_directory_skeleton() {
  local path="$1"
  local with_gitkeep="$2"

  mkdir -p "$path"
  if [[ "$with_gitkeep" == "1" ]]; then
    : >"$path/.gitkeep"
  fi
}

clean_directory_contents() {
  local dir_path="$1"
  local label="$2"
  local root="$3"
  local staging_root="$4"

  if [[ ! -d "$dir_path" ]]; then
    write_ergoms_message clean_skip_not_found gray "" "label=$label"
    return 0
  fi

  local -a items=()
  local item
  while IFS= read -r -d '' item; do
    items+=("$item")
  done < <(find "$dir_path" -mindepth 1 -maxdepth 1 ! -name '.gitkeep' -print0 2>/dev/null)

  if [[ ${#items[@]} -eq 0 ]]; then
    write_ergoms_message clean_skip_already_empty gray "" "label=$label"
    return 0
  fi

  local removed_count=${#items[@]}
  local moved
  moved="$(move_path_to_clean_trash "$dir_path" "$staging_root")" || moved=""
  if [[ -n "$moved" ]]; then
    # Каталог уехал целиком (включая .gitkeep) — возвращаем исходный .gitkeep
    restore_clean_directory_skeleton "$dir_path" 0
    if [[ -f "$moved/.gitkeep" ]]; then
      mv "$moved/.gitkeep" "$dir_path/.gitkeep"
    else
      restore_clean_directory_skeleton "$dir_path" 1
    fi
    write_ergoms_message clean_ok_removed_count_bg green "" "count=$removed_count" "label=$label"
    return 10
  fi

  local failed_items=()
  if ! find "$dir_path" -mindepth 1 -maxdepth 1 ! -name '.gitkeep' -exec rm -rf {} + 2>/dev/null; then
    true
  fi

  while IFS= read -r -d '' item; do
    failed_items+=("$(basename "$item")")
  done < <(find "$dir_path" -mindepth 1 -maxdepth 1 ! -name '.gitkeep' -print0 2>/dev/null)

  if [[ ${#failed_items[@]} -gt 0 ]]; then
    stop_blocking_processes_for_clean "$root"
    local retry_failed=()
    local base full_item
    for base in "${failed_items[@]}"; do
      full_item="$dir_path/$base"
      [[ -e "$full_item" ]] || continue
      if remove_path_robust "$full_item" 5; then
        continue
      fi
      retry_failed+=("$base")
    done
    failed_items=("${retry_failed[@]}")
  fi

  if [[ ${#failed_items[@]} -gt 0 ]]; then
    write_ergoms_message clean_error_clear_failed red --stderr "label=$label" "items=${failed_items[*]}"
    write_ergoms_message clean_hint_close_venv yellow --stderr
    return 1
  fi

  write_ergoms_message clean_ok_removed_count green "" "count=$removed_count" "label=$label"
  return 0
}

remove_full_path_fast() {
  local path="$1"
  local label="$2"
  local root="$3"
  local staging_root="$4"

  if [[ ! -e "$path" ]]; then
    write_ergoms_message clean_skip_not_found gray "" "label=$label"
    return 0
  fi

  if move_path_to_clean_trash "$path" "$staging_root" >/dev/null; then
    write_ergoms_message clean_ok_label_removed_bg green "" "label=$label"
    return 10
  fi

  if remove_path_robust "$path"; then
    write_ergoms_message clean_ok_label_removed green "" "label=$label"
    return 0
  fi

  stop_blocking_processes_for_clean "$root"
  if remove_path_robust "$path" 5; then
    write_ergoms_message clean_ok_label_removed green "" "label=$label"
    return 0
  fi

  write_ergoms_message clean_error_remove_failed red --stderr "label=$label"
  write_ergoms_message clean_hint_close_terminals yellow --stderr
  return 1
}

clear_project_dependencies() {
  local root="$1"

  local -a clean_paths=(
    "virtual_env/npm/node_modules"
    "node_modules"
    "virtual_env/python"
    "virtual_env/static_api"
    "virtual_env/celery"
    "virtual_env/nodejs"
    "virtual_env/packages"
    "virtual_env/resources"
    "virtual_env/trained_models"
    "virtual_env/cache"
    # legacy sibling (до переноса в cache/docker-cache)
    "virtual_env/docker-cache"
  )

  echo ""
  write_ergoms_message clean_heading cyan
  echo ""
  write_ergoms_message clean_will_remove yellow
  local p
  for p in "${clean_paths[@]}"; do
    echo "  - $p"
  done
  echo ""
  write_ergoms_message clean_media_kept green
  write_ergoms_message clean_backups_kept green
  echo ""

  write_ergoms_message clean_confirm white; read -r confirmation
  if [[ ! "$confirmation" =~ ^[yY]$ ]]; then
    write_ergoms_message clean_cancelled yellow
    return
  fi

  local has_work=0
  for p in "${clean_paths[@]}"; do
    local full_path="$root/$p"
    local full_remove=0
    [[ "$p" == "node_modules" || "$p" == "virtual_env/npm/node_modules" ]] && full_remove=1
    if clean_target_has_work "$full_path" "$full_remove"; then
      has_work=1
      break
    fi
  done

  if [[ "$has_work" -eq 0 ]]; then
    echo ""
    write_ergoms_message clean_skip_nothing gray
    echo ""
    write_ergoms_message clean_done_heading green
    echo ""
    return
  fi

  stop_blocking_processes_for_clean "$root"
  clear_project_shell_environment "$root/virtual_env/python"

  local staging
  staging="$(new_clean_trash_staging "$root")"
  local any_async=0
  local total=${#clean_paths[@]}
  local step=0
  local rc

  for p in "${clean_paths[@]}"; do
    step=$((step + 1))
    local full_path="$root/$p"
    echo ""
    write_ergoms_message clean_step yellow "" "step=$step" "total=$total" "label=$p"

    local full_remove=0
    [[ "$p" == "node_modules" || "$p" == "virtual_env/npm/node_modules" ]] && full_remove=1

    if ! clean_target_has_work "$full_path" "$full_remove"; then
      if [[ ! -e "$full_path" ]]; then
        write_ergoms_message clean_skip_not_found gray "" "label=$p"
      else
        write_ergoms_message clean_skip_already_empty gray "" "label=$p"
      fi
      continue
    fi

    if [[ "$full_remove" -eq 1 ]]; then
      remove_full_path_fast "$full_path" "$p" "$root" "$staging"
      rc=$?
    else
      clean_directory_contents "$full_path" "$p" "$root" "$staging"
      rc=$?
    fi

    if [[ "$rc" -eq 10 ]]; then
      any_async=1
    fi
  done

  if [[ "$any_async" -eq 1 ]]; then
    start_background_trash_removal "$staging"
    echo ""
    write_ergoms_message clean_info_async gray
  else
    rm -rf "$staging" >/dev/null 2>&1 || true
  fi

  echo ""
  write_ergoms_message clean_done_heading green
  echo ""
  write_ergoms_message clean_reinstall_hint cyan
  echo "  ergoms setup"
  echo ""
}

export -f stop_blocking_processes_for_clean
export -f clear_project_shell_environment
export -f remove_path_robust
export -f clean_target_has_work
export -f new_clean_trash_staging
export -f move_path_to_clean_trash
export -f start_background_trash_removal
export -f restore_clean_directory_skeleton
export -f clean_directory_contents
export -f remove_full_path_fast
export -f clear_project_dependencies
