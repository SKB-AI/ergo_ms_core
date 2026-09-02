# init_terminal.sh - Project shell with ergoms-only wrappers
# Source at terminal start so pip, poetry, npm, api and python manage.py are redirected to ergoms.
# Location: core/deployment/linux/init_terminal.sh (project root is 3 levels up).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# Project-Shell делает source и exec bash: функция наследуется, обычные
# переменные — нет. Без экспорта SCRIPT_DIR вызов становится /ergo_ms.sh.
export ERGOMS_LINUX_SCRIPT="$SCRIPT_DIR/ergo_ms.sh"
export ERGOMS_PROJECT_ROOT="$PROJECT_ROOT"

# Activate venv if present
if [[ -f "$PROJECT_ROOT/virtual_env/python/bin/activate" ]]; then
  source "$PROJECT_ROOT/virtual_env/python/bin/activate"
fi

# core/deployment/bin + portable Node.js в PATH
export PATH="$PROJECT_ROOT/core/deployment/bin:$PATH"
if [[ -d "$PROJECT_ROOT/virtual_env/packages/nodejs/bin" ]]; then
  export PATH="$PROJECT_ROOT/virtual_env/packages/nodejs/bin:$PATH"
fi

# Local ergoms; только из каталога проекта и подпапок
ergoms() {
  local cwd root script
  script="${ERGOMS_LINUX_SCRIPT:-}"
  root="${ERGOMS_PROJECT_ROOT:-$PROJECT_ROOT}"
  cwd="$(pwd -P)"
  if [[ -z "$root" || ! -d "$root" ]]; then
    echo "[ERROR] Не найден корень проекта. Откройте терминал Project-Shell или: source core/deployment/linux/init_terminal.sh" >&2
    return 1
  fi
  root="$(cd "$root" && pwd -P)"
  if [[ "$cwd" != "$root" && "$cwd" != "$root"/* ]]; then
    echo "[ERROR] Запускайте ergoms из каталога проекта или его подпапок: $root" >&2
    return 1
  fi
  if [[ -z "$script" || ! -f "$script" ]]; then
    script="$root/core/deployment/linux/ergo_ms.sh"
  fi
  if [[ ! -f "$script" ]]; then
    echo "[ERROR] Не найден $root/core/deployment/linux/ergo_ms.sh" >&2
    return 1
  fi
  bash "$script" "$@"
}

# Wrappers: pass through when ergoms sets ERGOMS_INTERNAL=1; show hint for direct user calls.

pip() {
  echo -e "\033[33mИспользуйте: ergoms python-install or ergoms poetry add <package>\033[0m"
}

pip3() {
  echo -e "\033[33mИспользуйте: ergoms python-install or ergoms poetry add <package>\033[0m"
}

poetry() {
  if [[ -n "${ERGOMS_INTERNAL:-}" ]]; then
    command poetry "$@"
    return
  fi
  echo -e "\033[33mИспользуйте: ergoms poetry <args>, e.g. ergoms poetry install, ergoms python-install, ergoms python-update\033[0m"
}

npm() {
  if [[ -n "${ERGOMS_INTERNAL:-}" ]]; then
    command npm "$@"
    return
  fi
  echo -e "\033[33mUse: ergoms npm <args>, ergoms start-client, ergoms client-build, ergoms install-deps\033[0m"
}

api() {
  if [[ -n "${ERGOMS_INTERNAL:-}" ]]; then
    command api "$@"
    return
  fi
  echo -e "\033[33mUse: ergoms api <args> or ergoms dev, ergoms db-migrate, ergoms migrate-all, ergoms collectstatic\033[0m"
}

media_api() {
  if [[ -n "${ERGOMS_INTERNAL:-}" ]]; then
    command media_api "$@"
    return
  fi
  echo -e "\033[33mUse: ergoms media_api <args> or ergoms start-media\033[0m"
}

python() {
  local first="$1"
  if [[ -n "$first" && ("$first" == *manage.py || "$first" == *"/manage.py") ]]; then
    echo -e "\033[33mUse: ergoms api <command>, e.g. ergoms dev, ergoms db-migrate, ergoms migrate-all, ergoms collectstatic\033[0m"
    return 1
  fi
  command python "$@"
}

python3() {
  local first="$1"
  if [[ -n "$first" && ("$first" == *manage.py || "$first" == *"/manage.py") ]]; then
    echo -e "\033[33mUse: ergoms api <command>, e.g. ergoms dev, ergoms db-migrate, ergoms migrate-all, ergoms collectstatic\033[0m"
    return 1
  fi
  command python3 "$@"
}

export -f ergoms
export -f pip
export -f pip3
export -f poetry
export -f npm
export -f api
export -f media_api
export -f python
export -f python3
