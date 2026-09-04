#!/usr/bin/env bash
# Helpers for centralized log paths (wraps logs_paths.py).

_logs_paths_script() {
  local root="$1"
  echo "$root/core/deployment/scripts/logs_paths.py"
}

_ergo_logs_python() {
  local root="$1"
  if [[ -x "$root/virtual_env/python/bin/python" ]]; then
    echo "$root/virtual_env/python/bin/python"
    return 0
  fi
  return 1
}

resolve_ergo_logs_dir() {
  local root="$1"
  local py script
  py="$(_ergo_logs_python "$root")" || return 1
  script="$(_logs_paths_script "$root")"
  "$py" "$script" dir "$root" 2>/dev/null || echo "$root/logs"
}

resolve_service_log_files() {
  local service_name="$1"
  local root="$2"
  local py script
  py="$(_ergo_logs_python "$root")" || return 1
  script="$(_logs_paths_script "$root")"
  "$py" "$script" service "$service_name" "$root" 2>/dev/null
}

resolve_service_stderr_log() {
  local service_name="$1"
  local root="$2"
  local py script
  py="$(_ergo_logs_python "$root")" || return 1
  script="$(_logs_paths_script "$root")"
  "$py" "$script" stderr "$service_name" "$root" 2>/dev/null
}

# legacy ergo-* → ergo_ms_* (и media_api → ergo_ms_media_api)
normalize_service_name() {
  local service_name="$1"
  local root="$2"
  local py script normalized
  py="$(_ergo_logs_python "$root")" || { printf '%s' "$service_name"; return 0; }
  script="$root/core/deployment/scripts/service_names.py"
  [[ -f "$script" ]] || { printf '%s' "$service_name"; return 0; }
  normalized="$("$py" "$script" normalize "$service_name" 2>/dev/null || true)"
  if [[ -n "$normalized" ]]; then
    printf '%s' "$normalized"
  else
    printf '%s' "$service_name"
  fi
}

is_known_service_log() {
  local service_name="$1"
  local root="$2"
  local py script
  py="$(_ergo_logs_python "$root")" || return 1
  script="$(_logs_paths_script "$root")"
  [[ "$("$py" "$script" known "$service_name" "$root" 2>/dev/null)" == "true" ]]
}

export -f _logs_paths_script
export -f _ergo_logs_python
export -f resolve_ergo_logs_dir
export -f resolve_service_log_files
export -f resolve_service_stderr_log
export -f normalize_service_name
export -f is_known_service_log
