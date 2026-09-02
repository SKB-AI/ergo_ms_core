#!/usr/bin/env bash
# Lifecycle runner invocation for Linux ergo_ms

ensure_portable_runtimes_for_setup() {
  local root="$1"
  local mode="${2:-both}"  # both|python|node
  local respect_env="${3:-false}"
  # shellcheck source=portable_env.sh
  source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/portable_env.sh"
  # shellcheck source=portable_python.sh
  source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/portable_python.sh"
  # shellcheck source=portable_nodejs.sh
  source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/portable_nodejs.sh"

  case "$mode" in
    python)
      if [[ "$respect_env" == "true" ]] && ! is_portable_python_enabled "$root"; then
        echo "$(format_ergo_console skip 'PORTABLE_PYTHON_ENABLED=false — portable Python не устанавливается')"
        return 0
      fi
      install_portable_python "$root" "false" || return 1
      ;;
    node)
      if [[ "$respect_env" == "true" ]] && ! is_portable_nodejs_enabled "$root"; then
        echo "$(format_ergo_console skip 'PORTABLE_NODEJS_ENABLED=false — portable Node.js не устанавливается')"
        return 0
      fi
      install_portable_nodejs "$root" "false" || return 1
      ;;
    *)
      if [[ "$respect_env" == "true" ]] && ! is_portable_python_enabled "$root"; then
        echo "$(format_ergo_console skip 'PORTABLE_PYTHON_ENABLED=false — portable Python не устанавливается')"
      else
        install_portable_python "$root" "false" || return 1
      fi
      if [[ "$respect_env" == "true" ]] && ! is_portable_nodejs_enabled "$root"; then
        echo "$(format_ergo_console skip 'PORTABLE_NODEJS_ENABLED=false — portable Node.js не устанавливается')"
      else
        install_portable_nodejs "$root" "false" || return 1
      fi
      ;;
  esac
}

lifecycle_python_exe() {
  local root="$1"
  local venv_py="$root/virtual_env/python/bin/python"
  if [[ -x "$venv_py" ]]; then
    echo "$venv_py"
    return 0
  fi
  local portable_py="$root/virtual_env/packages/python/bin/python3"
  if [[ -x "$portable_py" ]]; then
    echo "$portable_py"
    return 0
  fi
  return 1
}

invoke_lifecycle_runner() {
  local root="$1"
  local recipe="$2"
  shift 2
  local runner="$root/core/deployment/lifecycle/runner.py"

  if [[ ! -f "$runner" ]]; then
    echo "[ERROR] lifecycle runner не найден: $runner" >&2
    exit 1
  fi

  case "$recipe" in
    setup-full)
      ensure_portable_runtimes_for_setup "$root" both true || exit 1
      ;;
    install-python|install-python-runtime)
      ensure_portable_runtimes_for_setup "$root" python false || exit 1
      ;;
    install-nodejs|install-node)
      ensure_portable_runtimes_for_setup "$root" node false || exit 1
      ;;
  esac

  local py
  if py="$(lifecycle_python_exe "$root")"; then
    "$py" "$runner" "$recipe" --project-root "$root" "$@"
  elif command -v python3.12 >/dev/null 2>&1; then
    python3.12 "$runner" "$recipe" --project-root "$root" "$@"
  elif command -v python3 >/dev/null 2>&1; then
    python3 "$runner" "$recipe" --project-root "$root" "$@"
  else
    python "$runner" "$recipe" --project-root "$root" "$@"
  fi
}

export -f ensure_portable_runtimes_for_setup lifecycle_python_exe invoke_lifecycle_runner
