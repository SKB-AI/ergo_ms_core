#!/usr/bin/env bash
# Файловый журнал сессии ergoms: setup-full.log или ergoms.log (см. cli_session_log.py).

_cli_log_python() {
  local root="$1"
  if [[ -x "$root/virtual_env/python/bin/python" ]]; then
    echo "$root/virtual_env/python/bin/python"
    return 0
  fi
  if [[ -x "$root/virtual_env/packages/python/bin/python3" ]]; then
    echo "$root/virtual_env/packages/python/bin/python3"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  return 1
}

attach_cli_session_log() {
  local root="$1"
  local cmd="$2"
  if [[ -n "${ERGO_CLI_LOG_ATTACHED:-}" ]]; then
    return 0
  fi
  if [[ -z "$root" || -z "$cmd" ]]; then
    return 0
  fi

  local script="$root/core/deployment/scripts/cli_session_log.py"
  if [[ ! -f "$script" ]]; then
    return 0
  fi

  local py path
  py="$(_cli_log_python "$root")" || return 0
  path="$("$py" "$script" prepare "$cmd" "$root" 2>/dev/null)" || return 0
  if [[ -z "$path" ]]; then
    return 0
  fi

  mkdir -p "$(dirname "$path")"
  export ERGO_CLI_LOG_ATTACHED=1
  exec > >(tee -a "$path") 2>&1
}

export -f _cli_log_python
export -f attach_cli_session_log
