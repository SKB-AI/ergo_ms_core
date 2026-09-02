#!/usr/bin/env bash
# Внутренний dispatch для lifecycle (services, nginx, redis, postgres, tls, cli).
set -euo pipefail

category="${1:?category}"
operation="${2:?operation}"
root="${3:?root}"
shift 3 || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "$SCRIPT_DIR/../../linux/lib" && pwd)"

# shellcheck source=../../linux/lib/core.sh
source "$LIB_DIR/core.sh"
# shellcheck source=../../linux/lib/systemd.sh
source "$LIB_DIR/systemd.sh"
# shellcheck source=../../linux/lib/services.sh
source "$LIB_DIR/services.sh"
# shellcheck source=../../linux/lib/cli.sh
source "$LIB_DIR/cli.sh"
# shellcheck source=../../linux/lib/nginx.sh
source "$LIB_DIR/nginx.sh"
# shellcheck source=../../linux/lib/redis.sh
source "$LIB_DIR/redis.sh"
# shellcheck source=../../linux/lib/meilisearch.sh
source "$LIB_DIR/meilisearch.sh"
# shellcheck source=../../linux/lib/postgres.sh
source "$LIB_DIR/postgres.sh"
# shellcheck source=../../linux/lib/tls.sh
source "$LIB_DIR/tls.sh"
# shellcheck source=../../linux/lib/portable_python.sh
source "$LIB_DIR/portable_python.sh"
# shellcheck source=../../linux/lib/portable_nodejs.sh
source "$LIB_DIR/portable_nodejs.sh"
# shellcheck source=../../linux/lib/lifecycle.sh
source "$LIB_DIR/lifecycle.sh"

set_service_project_root "$root"

_invoke_infra_backend() {
  local service="$1"
  local operation="$2"
  local backend="$root/core/deployment/lifecycle/services/backends/${service}_backend.py"
  if [[ ! -f "$backend" ]]; then
    echo "[ERROR] Backend не найден: $backend" >&2
    exit 1
  fi
  local deployment_dir="$root/core/deployment"
  local py
  if py="$(lifecycle_python_exe "$root")"; then
    PYTHONPATH="$deployment_dir${PYTHONPATH:+:$PYTHONPATH}" \
      "$py" "$backend" "$operation" --root "$root"
  elif command -v python3.12 >/dev/null 2>&1; then
    PYTHONPATH="$deployment_dir${PYTHONPATH:+:$PYTHONPATH}" \
      python3.12 "$backend" "$operation" --root "$root"
  else
    PYTHONPATH="$deployment_dir${PYTHONPATH:+:$PYTHONPATH}" \
      python3 "$backend" "$operation" --root "$root"
  fi
}

case "$category" in
  service)
    case "$operation" in
      install-all) install_services "$root"; start_all ;;
      install-api) install_single_service "api" "$root"; systemctl_do start ergo_ms_api_dev.service ;;
      install-client) install_single_service "client" "$root"; systemctl_do start ergo_ms_client_dev.service 2>/dev/null || true ;;
      install-media) install_single_service "media" "$root"; systemctl_do start ergo_ms_media_api.service ;;
      install-beat) install_single_service "beat" "$root"; systemctl_do start ergo_ms_celery_beat.service ;;
      install-workers) install_single_service "worker" "$root" ;;
      start-all) start_all ;;
      start-api) systemctl_do start ergo_ms_api_dev.service ;;
      start-client) systemctl_do start ergo_ms_client_dev.service ;;
      start-media) systemctl_do start ergo_ms_media_api.service ;;
      start-beat) systemctl_do start ergo_ms_celery_beat.service ;;
      start-workers)
        for u in $(units_list "$root"); do
          [[ "$u" == ergo_ms_celery_worker* ]] && systemctl_do start "$u"
        done
        ;;
      stop-all) stop_all ;;
      stop-api) systemctl_do stop ergo_ms_api_dev.service ;;
      stop-client) systemctl_do stop ergo_ms_client_dev.service ;;
      stop-media) systemctl_do stop ergo_ms_media_api.service ;;
      stop-beat) systemctl_do stop ergo_ms_celery_beat.service ;;
      stop-workers)
        for u in $(units_list "$root"); do
          [[ "$u" == ergo_ms_celery_worker* ]] && systemctl_do stop "$u"
        done
        ;;
      restart-all) restart_all ;;
      restart-api) systemctl_do restart ergo_ms_api_dev.service ;;
      restart-client) systemctl_do restart ergo_ms_client_dev.service ;;
      restart-media) systemctl_do restart ergo_ms_media_api.service ;;
      restart-beat) systemctl_do restart ergo_ms_celery_beat.service ;;
      restart-workers)
        for u in $(units_list "$root"); do
          [[ "$u" == ergo_ms_celery_worker* ]] && systemctl_do restart "$u"
        done
        ;;
      status-all) status_all ;;
      status-api) systemctl_do status ergo_ms_api_dev.service ;;
      status-client) systemctl_do status ergo_ms_client_dev.service ;;
      status-media) systemctl_do status ergo_ms_media_api.service ;;
      status-beat) systemctl_do status ergo_ms_celery_beat.service ;;
      status-workers)
        for u in $(units_list "$root"); do
          [[ "$u" == ergo_ms_celery_worker* ]] && systemctl_do status "$u"
        done
        ;;
      uninstall-all)
        purge=false
        [[ "${1:-}" == "--purge" ]] && purge=true
        uninstall_all "$purge"
        ;;
      *) echo "[ERROR] Неизвестная операция service: $operation" >&2; exit 1 ;;
    esac
    ;;
  nginx)
    case "$operation" in
      install) nginx_install "$root" "${1:-}" "${2:-}" "false" ;;
      install-service)
        if _nginx_is_installed "$root"; then
          nginx_install_service "$root"
        else
          nginx_install "$root" "${1:-}" "${2:-}" "false"
        fi
        ;;
      uninstall)
        purge=false
        [[ "${1:-}" == "--purge" ]] && purge=true
        nginx_uninstall "$root" "$purge"
        ;;
      start) nginx_start_service "$root" ;;
      stop) nginx_stop_service "$root" ;;
      restart) nginx_stop_service "$root"; nginx_start_service "$root" ;;
      reload) _invoke_infra_backend nginx reload ;;
      status) _invoke_infra_backend nginx status ;;
      test) _invoke_infra_backend nginx test ;;
      *) echo "[ERROR] Неизвестная операция nginx: $operation" >&2; exit 1 ;;
    esac
    ;;
  redis)
    port=""
    for arg in "$@"; do
      case "$arg" in
        --purge) purge_flag=true ;;
        [0-9]*) port="$arg" ;;
      esac
    done
    case "$operation" in
      install) redis_install "$root" "$port" "false" ;;
      install-service)
        if _redis_is_installed "$root"; then
          redis_install_service "$root"
        else
          redis_install "$root" "$port" "true"
        fi
        ;;
      uninstall)
        purge=false
        [[ "${1:-}" == "--purge" ]] && purge=true
        redis_uninstall "$root" "$purge"
        ;;
      start) redis_start "$root" ;;
      stop) redis_stop "$root" ;;
      restart) redis_restart "$root" ;;
      status) _invoke_infra_backend redis status ;;
      test) _invoke_infra_backend redis test ;;
      *) echo "[ERROR] Неизвестная операция redis: $operation" >&2; exit 1 ;;
    esac
    ;;

  postgres)
    if [[ "$operation" == "migrate-to-portable" ]]; then
      postgres_migrate_to_portable "$root" "$@"
      exit $?
    fi
    port=""
    no_skip=false
    purge=false
    for arg in "$@"; do
      case "$arg" in
        --purge) purge=true ;;
        --no-skip-system) no_skip=true ;;
        [0-9]*) port="$arg" ;;
      esac
    done
    case "$operation" in
      install)
        if [[ "$no_skip" == true ]]; then
          postgres_install "$root" "$port" "true"
        else
          postgres_install "$root" "$port" "false"
        fi
        ;;
      install-service) postgres_install_service "$root" ;;
      uninstall) postgres_uninstall "$root" "$purge" ;;
      start) postgres_start "$root" ;;
      stop) postgres_stop "$root" ;;
      restart) postgres_restart "$root" ;;
      status) postgres_status "$root" ;;
      test) postgres_test "$root" ;;
      *) echo "[ERROR] Неизвестная операция postgres: $operation" >&2; exit 1 ;;
    esac
    ;;
  meilisearch)
    purge=false
    for arg in "$@"; do
      case "$arg" in
        --purge) purge=true ;;
      esac
    done
    case "$operation" in
      install) meilisearch_install "$root" "false" ;;
      install-service)
        if _meilisearch_is_installed "$root"; then
          meilisearch_install_service "$root"
        else
          meilisearch_install "$root" "true"
        fi
        ;;
      uninstall) meilisearch_uninstall "$root" "$purge" ;;
      start) meilisearch_start "$root" ;;
      stop) meilisearch_stop "$root" ;;
      restart) meilisearch_restart "$root" ;;
      status) _invoke_infra_backend meilisearch status ;;
      test) _invoke_infra_backend meilisearch test ;;
      *) echo "[ERROR] Неизвестная операция meilisearch: $operation" >&2; exit 1 ;;
    esac
    ;;
  tls)
    case "$operation" in
      install) tls_install "$root" "${1:-}" "${2:-}" "${3:-false}" ;;
      renew)
        dry=false
        [[ "${1:-}" == "--dry-run" ]] && dry=true
        tls_renew "$root" "$dry"
        ;;
      status) tls_status "$root" ;;
      *) echo "[ERROR] Неизвестная операция tls: $operation" >&2; exit 1 ;;
    esac
    ;;
  cli)
    case "$operation" in
      install) create_cli_wrapper "$root" ;;
      uninstall) remove_cli_wrapper "$root" ;;
      *) echo "[ERROR] Неизвестная операция cli: $operation" >&2; exit 1 ;;
    esac
    ;;
  runtime)
    force=false
    [[ "${1:-}" == "--force" ]] && force=true
    case "$operation" in
      install-python) install_portable_python "$root" "$force" ;;
      install-nodejs) install_portable_nodejs "$root" "$force" ;;
      *) echo "[ERROR] Неизвестная операция runtime: $operation" >&2; exit 1 ;;
    esac
    ;;
  *)
    echo "[ERROR] Неизвестная категория: $category" >&2
    exit 1
    ;;
esac
