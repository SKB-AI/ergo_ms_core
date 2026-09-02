#!/usr/bin/env bash
# CLI wrapper: core/deployment/bin + симлинк /usr/local/bin/ergoms для sudo.

_ERGOMS_CLI_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_ERGOMS_SYSTEM_CLI='/usr/local/bin/ergoms'

_ergoms_project_root_from_cli_lib() {
  (cd "$_ERGOMS_CLI_LIB_DIR/../../../.." && pwd)
}

_ergoms_bin_dir() {
  echo "$1/core/deployment/bin"
}

_ergoms_resolve_path() {
  local path="$1"
  if command -v readlink >/dev/null 2>&1 && readlink -f "$path" >/dev/null 2>&1; then
    readlink -f "$path"
    return
  fi
  printf '%s\n' "$path"
}

_ergoms_run_privileged() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    return 1
  fi
}

_ergoms_link_system_cli() {
  local target="$1"
  if [[ -d /usr/local/bin && -w /usr/local/bin ]]; then
    ln -sfn "$target" "$_ERGOMS_SYSTEM_CLI"
  else
    _ergoms_run_privileged ln -sfn "$target" "$_ERGOMS_SYSTEM_CLI"
  fi
}

create_cli_wrapper() {
  local project_root="${1:-}"
  if [[ -z "$project_root" ]]; then
    project_root="$(_ergoms_project_root_from_cli_lib)"
  fi
  local bin_dir local_bin expected current
  bin_dir="$(_ergoms_bin_dir "$project_root")"
  local_bin="$bin_dir/ergoms"

  if [[ ! -f "$local_bin" ]]; then
    ERGO_ROOT="$project_root" write_ergoms_message cli_local_missing red --stderr "path=$local_bin"
    ERGO_ROOT="$project_root" write_ergoms_message cli_restore_bin yellow --stderr
    return 1
  fi

  chmod +x "$local_bin" 2>/dev/null || true
  expected="$(_ergoms_resolve_path "$local_bin")"

  ERGO_ROOT="$project_root" write_ergoms_message cli_ok_path green "" "path=$bin_dir"

  if [[ -L "$_ERGOMS_SYSTEM_CLI" || -e "$_ERGOMS_SYSTEM_CLI" ]]; then
    current="$(_ergoms_resolve_path "$_ERGOMS_SYSTEM_CLI")"
    if [[ "$current" == "$expected" ]]; then
      ERGO_ROOT="$project_root" write_ergoms_message cli_system_link_skip gray "" "path=$_ERGOMS_SYSTEM_CLI"
    elif [[ -L "$_ERGOMS_SYSTEM_CLI" ]]; then
      ERGO_ROOT="$project_root" write_ergoms_message cli_system_link_foreign red --stderr "path=$_ERGOMS_SYSTEM_CLI" "target=$current"
      return 1
    else
      ERGO_ROOT="$project_root" write_ergoms_message cli_system_link_exists red --stderr "path=$_ERGOMS_SYSTEM_CLI"
      return 1
    fi
  else
    if [[ ! -d /usr/local/bin ]]; then
      if ! _ergoms_run_privileged mkdir -p /usr/local/bin; then
        ERGO_ROOT="$project_root" write_ergoms_message cli_system_link_need_root red --stderr "path=$_ERGOMS_SYSTEM_CLI"
        return 1
      fi
    fi
    if ! _ergoms_link_system_cli "$expected"; then
      ERGO_ROOT="$project_root" write_ergoms_message cli_system_link_need_root red --stderr "path=$_ERGOMS_SYSTEM_CLI"
      return 1
    fi
    ERGO_ROOT="$project_root" write_ergoms_message cli_system_link_ok green "" "path=$_ERGOMS_SYSTEM_CLI" "target=$expected"
  fi

  ERGO_ROOT="$project_root" write_ergoms_message cli_run_hint cyan
  ERGO_ROOT="$project_root" write_ergoms_message cli_sudo_hint cyan
  ERGO_ROOT="$project_root" write_ergoms_message cli_cwd_hint cyan
}

remove_cli_wrapper() {
  local project_root="${1:-}"
  if [[ -z "$project_root" ]]; then
    project_root="$(_ergoms_project_root_from_cli_lib)"
  fi
  local local_bin expected current
  local_bin="$(_ergoms_bin_dir "$project_root")/ergoms"
  expected="$(_ergoms_resolve_path "$local_bin")"

  ERGO_ROOT="$project_root" write_ergoms_message cli_bin_not_removed cyan

  if [[ ! -e "$_ERGOMS_SYSTEM_CLI" && ! -L "$_ERGOMS_SYSTEM_CLI" ]]; then
    ERGO_ROOT="$project_root" write_ergoms_message cli_system_link_missing gray "" "path=$_ERGOMS_SYSTEM_CLI"
    return 0
  fi

  current="$(_ergoms_resolve_path "$_ERGOMS_SYSTEM_CLI")"
  if [[ ! -L "$_ERGOMS_SYSTEM_CLI" ]] || [[ "$current" != "$expected" ]]; then
    ERGO_ROOT="$project_root" write_ergoms_message cli_system_link_skip_other yellow --stderr "path=$_ERGOMS_SYSTEM_CLI" "target=$current"
    return 0
  fi

  if [[ -w /usr/local/bin ]]; then
    if ! rm -f "$_ERGOMS_SYSTEM_CLI"; then
      ERGO_ROOT="$project_root" write_ergoms_message cli_system_link_need_root red --stderr "path=$_ERGOMS_SYSTEM_CLI"
      return 1
    fi
  elif ! _ergoms_run_privileged rm -f "$_ERGOMS_SYSTEM_CLI"; then
    ERGO_ROOT="$project_root" write_ergoms_message cli_system_link_need_root red --stderr "path=$_ERGOMS_SYSTEM_CLI"
    return 1
  fi
  ERGO_ROOT="$project_root" write_ergoms_message cli_system_link_removed green "" "path=$_ERGOMS_SYSTEM_CLI"
}

export -f create_cli_wrapper
export -f remove_cli_wrapper
