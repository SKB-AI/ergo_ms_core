#!/usr/bin/env bash
set -euo pipefail

# This script installs and starts systemd services for ergo_ms on Linux.
# It auto-detects the project root and avoids hardcoded directories by using
# an EnvironmentFile and systemd's %E{VAR} specifier.

# Load modules
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# shellcheck source=lib/core.sh
source "$LIB_DIR/core.sh"
# shellcheck source=lib/systemd.sh
source "$LIB_DIR/systemd.sh"
# shellcheck source=lib/services.sh
source "$LIB_DIR/services.sh"
# shellcheck source=lib/setup.sh
source "$LIB_DIR/setup.sh"
# shellcheck source=lib/cli.sh
source "$LIB_DIR/cli.sh"
# shellcheck source=lib/commands.sh
source "$LIB_DIR/commands.sh"
# shellcheck source=lib/nginx.sh
source "$LIB_DIR/nginx.sh"
# shellcheck source=lib/redis.sh
source "$LIB_DIR/redis.sh"
# shellcheck source=lib/meilisearch.sh
source "$LIB_DIR/meilisearch.sh"
# shellcheck source=lib/postgres.sh
source "$LIB_DIR/postgres.sh"
# shellcheck source=lib/tls.sh
source "$LIB_DIR/tls.sh"
# shellcheck source=lib/lifecycle.sh
source "$LIB_DIR/lifecycle.sh"
# shellcheck source=lib/help.sh
source "$LIB_DIR/help.sh"
# shellcheck source=lib/cli_log.sh
source "$LIB_DIR/cli_log.sh"

main() {
  local command=""
  local ERGO_ROOT
  local arg_root=""
  local purge=false
  local no_cli=false
  local recreate_venv=false
  local with_postgres=false
  local SELF_SCRIPT
  if command -v readlink >/dev/null 2>&1; then
    SELF_SCRIPT="$(readlink -f "$0")"
  else
    SELF_SCRIPT="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  fi

  # Try to detect root early to load custom commands
  local detected_root=""
  detected_root="$(detect_project_root 2>/dev/null || echo '')"
  
  declare -A available_custom_cmds
  if [[ -n "$detected_root" ]]; then
    load_custom_commands "$detected_root" 2>/dev/null || true
    for key in "${!CUSTOM_COMMANDS[@]}"; do
      available_custom_cmds["$key"]="${CUSTOM_COMMANDS[$key]}"
    done
  fi

  # First positional can be a command
  if (( $# > 0 )); then
    command="$1"
    local normalized_cmd="${command//_/-}"
    if [[ ! -v "available_custom_cmds[$command]" ]] && [[ -v "available_custom_cmds[$normalized_cmd]" ]]; then
      command="$normalized_cmd"
    fi
    case "$command" in
      install|install-services|install-api-service|install-client-service|install-worker-service|install-beat-service|install-media-service|start|stop|restart|status|uninstall-services|install-cli|uninstall-cli|logs|setup-full|update-submodules|update-module-submodules|clean|help|poetry|api|media_api|npm|install-nginx|install-nginx-service|uninstall-nginx|start-nginx|stop-nginx|restart-nginx|reload-nginx|status-nginx|test-nginx|install-tls|renew-tls|status-tls|install-redis|install-redis-service|uninstall-redis|start-redis|stop-redis|restart-redis|status-redis|test-redis|install-postgres|install-postgres-service|uninstall-postgres|start-postgres|stop-postgres|restart-postgres|status-postgres|test-postgres|migrate-postgres-to-portable|install-python|install-python-runtime|install-nodejs|install-node)
        shift ;;
      *:poetry)
        shift ;;  # module:poetry command, handled below
      -h|--help)
        print_usage "$detected_root"; exit 0 ;;
      *)
        # Check if it's a custom command
        if [[ -v "available_custom_cmds[$command]" ]]; then
          shift
        else
          write_ergoms_message unknown_command red --stderr "name=$command"
          print_usage "$detected_root"
          exit 1
        fi
        ;;
    esac
  fi

  # If no command provided, show help
  if [[ -z "$command" ]]; then
    print_usage "$detected_root"
    exit 0
  fi

  # Справка без root
  if [[ "$command" == "help" ]]; then
    print_usage "$detected_root" "$@"
    exit 0
  fi

  # Check if it's a <module>:poetry command (doesn't require root)
  local is_module_poetry_command=false
  local module_poetry_name=""
  if [[ "$command" =~ ^([a-zA-Z0-9_-]+):poetry$ ]]; then
    is_module_poetry_command=true
    module_poetry_name="${BASH_REMATCH[1]}"
  fi

  # Check if it's a proxy command (doesn't require root)
  local is_proxy_command=false
  case "$command" in
    poetry|api|media_api|npm)
      is_proxy_command=true ;;
  esac
  
  # Check if it's a logs command (doesn't require root for viewing)
  local is_logs_command=false
  if [[ "$command" == "logs" ]]; then
    is_logs_command=true
  fi
  
  # Check if it's a custom command (doesn't require root)
  # Exclude built-in commands to avoid recursion (e.g. install-cli would re-invoke self via commands.conf)
  local builtin_override="install-cli|uninstall-cli|install|install-services|install-api-service|install-client-service|install-worker-service|install-beat-service|install-media-service|start|stop|restart|status|uninstall-services|setup-full|install-nginx|install-nginx-service|uninstall-nginx|start-nginx|stop-nginx|restart-nginx|reload-nginx|status-nginx|test-nginx|install-tls|renew-tls|status-tls|install-redis|install-redis-service|uninstall-redis|start-redis|stop-redis|restart-redis|status-redis|test-redis|install-postgres|install-postgres-service|uninstall-postgres|start-postgres|stop-postgres|restart-postgres|status-postgres|test-postgres|migrate-postgres-to-portable|install-python|install-python-runtime|install-nodejs|install-node"
  local is_custom_command=false
  if [[ -v "available_custom_cmds[$command]" ]] && [[ ! "$command" =~ ^($builtin_override)$ ]]; then
    is_custom_command=true
  fi
  
  # Check if it's a deployment command (doesn't require root)
  local is_deploy_command=false
  case "$command" in
    deploy-api|deploy-client|deploy-api-dev|deploy-client-dev|deploy-all)
      is_deploy_command=true ;;
  esac
  
  # Check if it's a clean command (doesn't require root)
  local is_clean_command=false
  if [[ "$command" == "clean" ]]; then
    is_clean_command=true
  fi

  # Check if it's an update-submodules command (doesn't require root)
  local is_update_submodules_command=false
  if [[ "$command" == "update-submodules" ]]; then
    is_update_submodules_command=true
  fi

  local is_update_module_submodules_command=false
  if [[ "$command" == "update-module-submodules" ]]; then
    is_update_module_submodules_command=true
  fi

  # Check if it's a nginx command (requires root for install/uninstall, not for others)
  local is_nginx_command=false
  case "$command" in
    install-nginx|install-nginx-service|uninstall-nginx|start-nginx|stop-nginx|restart-nginx|reload-nginx|status-nginx|test-nginx|install-tls|renew-tls|status-tls)
      is_nginx_command=true ;;
  esac

  # Check if it's a redis command
  local is_redis_command=false
  case "$command" in
    install-redis|install-redis-service|uninstall-redis|start-redis|stop-redis|restart-redis|status-redis|test-redis)
      is_redis_command=true ;;
  esac

  # Check if it's a postgres command
  local is_postgres_command=false
  case "$command" in
    install-postgres|install-postgres-service|uninstall-postgres|start-postgres|stop-postgres|restart-postgres|status-postgres|test-postgres|migrate-postgres-to-portable)
      is_postgres_command=true ;;
  esac

  local is_runtime_command=false
  case "$command" in
    install-python|install-python-runtime|install-nodejs|install-node)
      is_runtime_command=true ;;
  esac

  # Parse flags/positional root for proxy, custom, logs, deploy, clean, update-submodules, nginx, redis, and runtime commands
  if [[ "$is_proxy_command" == true ]] || [[ "$is_module_poetry_command" == true ]] || [[ "$is_custom_command" == true ]] || [[ "$is_logs_command" == true ]] || [[ "$is_deploy_command" == true ]] || [[ "$is_clean_command" == true ]] || [[ "$is_update_submodules_command" == true ]] || [[ "$is_update_module_submodules_command" == true ]] || [[ "$is_nginx_command" == true ]] || [[ "$is_redis_command" == true ]] || [[ "$is_postgres_command" == true ]] || [[ "$is_runtime_command" == true ]]; then
    while (( "$#" )); do
      case "$1" in
        --root)
          shift; arg_root="${1:-}"; shift || true ;;
        --root=*)
          arg_root="${1#*=}"; shift ;;
        -h|--help)
          print_usage "$detected_root"; exit 0 ;;
        *)
          break ;;  # Rest are arguments for the command
      esac
    done
    
    # Detect project root
    if [[ -n "$arg_root" ]]; then
      if [[ -d "$arg_root" ]]; then
        if command -v readlink >/dev/null 2>&1; then ERGO_ROOT="$(readlink -f "$arg_root")"; else ERGO_ROOT="$(cd "$arg_root" && pwd)"; fi
      else
        write_ergoms_message error_root_not_dir red --stderr "path=$arg_root"
        exit 1
      fi
    else
      ERGO_ROOT="$(detect_project_root)"
    fi

    attach_cli_session_log "$ERGO_ROOT" "$command"
    
    # Execute clean and update-submodules as built-in first (avoid recursion via commands.conf)
    if [[ "$is_clean_command" == true ]]; then
      clear_project_dependencies "$ERGO_ROOT"
      exit 0
    fi
    if [[ "$is_update_submodules_command" == true ]]; then
      invoke_lifecycle_runner "$ERGO_ROOT" update-submodules
      exit 0
    fi
    if [[ "$is_update_module_submodules_command" == true ]]; then
      invoke_lifecycle_runner "$ERGO_ROOT" update-module-submodules
      exit 0
    fi

    if [[ "$is_runtime_command" == true ]]; then
      invoke_lifecycle_runner "$ERGO_ROOT" "$command"
      exit 0
    fi

    # Execute nginx/tls via lifecycle runner
    if [[ "$is_nginx_command" == true ]]; then
      local nginx_purge=false
      local extra=()
      while (( "$#" )); do
        case "$1" in
          --purge) nginx_purge=true; shift ;;
          --dry-run) extra+=(--dry-run); shift ;;
          *) break ;;
        esac
      done
      [[ "$nginx_purge" == true ]] && extra+=(--purge)
      case "$command" in
        install-nginx|install-nginx-service|install-tls)
          extra+=(--server-name "${1:-}" --listen-port "${2:-}" --domain "${1:-}" --email "${2:-}")
          ;;
      esac
      invoke_lifecycle_runner "$ERGO_ROOT" "$command" "${extra[@]}"
      exit 0
    fi

    # Execute redis via lifecycle runner
    if [[ "$is_redis_command" == true ]]; then
      local redis_port="" redis_purge=false
      local extra=()
      while (( "$#" )); do
        case "$1" in
          --purge) redis_purge=true; shift ;;
          --configure)
            write_ergoms_message redis_configure_deprecated_short yellow --stderr
            shift
            ;;
          *)
            if [[ -z "$redis_port" ]] && [[ "$1" =~ ^[0-9]+$ ]]; then
              redis_port="$1"
              shift
            else
              break
            fi
            ;;
        esac
      done
      [[ "$redis_purge" == true ]] && extra+=(--purge)
      [[ -n "$redis_port" ]] && extra+=(--listen-port "$redis_port")
      case "$command" in
        install-redis)
          invoke_lifecycle_runner "$ERGO_ROOT" install-redis "${extra[@]}"
          ;;
        install-redis-service)
          invoke_lifecycle_runner "$ERGO_ROOT" install-redis-service "${extra[@]}"
          ;;
        *)
          invoke_lifecycle_runner "$ERGO_ROOT" "$command" "${extra[@]}"
          ;;
      esac
      exit 0
    fi

    # Execute postgres via lifecycle runner
    if [[ "$is_postgres_command" == true ]]; then
      if [[ "$command" == "migrate-postgres-to-portable" ]]; then
        local extra=()
        local has_source_password=false
        while (( "$#" )); do
          case "$1" in
            --source-port)
              [[ -n "${2:-}" ]] || { write_ergoms_message error_arg_requires_value red --stderr "flag=--source-port"; exit 1; }
              extra+=(--source-port "$2"); shift 2 ;;
            --source-host)
              [[ -n "${2:-}" ]] || { write_ergoms_message error_arg_requires_value red --stderr "flag=--source-host"; exit 1; }
              extra+=(--source-host "$2"); shift 2 ;;
            --source-user)
              [[ -n "${2:-}" ]] || { write_ergoms_message error_arg_requires_value red --stderr "flag=--source-user"; exit 1; }
              extra+=(--source-user "$2"); shift 2 ;;
            --source-password)
              [[ -n "${2:-}" ]] || { write_ergoms_message error_arg_requires_value red --stderr "flag=--source-password"; exit 1; }
              extra+=(--source-password "$2"); has_source_password=true; shift 2 ;;
            --force|--dry-run)
              extra+=("$1"); shift ;;
            *)
              write_ergoms_message error_unknown_arg red --stderr "arg=$1"
              exit 1 ;;
          esac
        done
        if [[ "$has_source_password" != true ]]; then
          write_ergoms_message error_need_source_password red --stderr
          exit 1
        fi
        invoke_lifecycle_runner "$ERGO_ROOT" migrate-postgres-to-portable "${extra[@]}"
        exit 0
      fi
      local pg_port="" pg_purge=false pg_force=false
      local extra=()
      while (( "$#" )); do
        case "$1" in
          --purge) pg_purge=true; shift ;;
          --no-skip-system|--with-postgres) pg_force=true; shift ;;
          *)
            if [[ -z "$pg_port" ]] && [[ "$1" =~ ^[0-9]+$ ]]; then
              pg_port="$1"
              shift
            else
              break
            fi
            ;;
        esac
      done
      [[ "$pg_purge" == true ]] && extra+=(--purge)
      [[ -n "$pg_port" ]] && extra+=(--listen-port "$pg_port")
      [[ "$pg_force" == true ]] && extra+=(--with-postgres)
      case "$command" in
        install-postgres)
          invoke_lifecycle_runner "$ERGO_ROOT" install-postgres "${extra[@]}"
          ;;
        install-postgres-service)
          invoke_lifecycle_runner "$ERGO_ROOT" install-postgres-service "${extra[@]}"
          ;;
        *)
          invoke_lifecycle_runner "$ERGO_ROOT" "$command" "${extra[@]}"
          ;;
      esac
      exit 0
    fi

    # Execute custom command
    if [[ "$is_custom_command" == true ]]; then
      invoke_custom_command "$ERGO_ROOT" "$command" "$@"
      exit 0
    fi
    
    # Execute deploy command
    if [[ "$is_deploy_command" == true ]]; then
      invoke_custom_command "$ERGO_ROOT" "$command" "$@"
      exit 0
    fi
    
    # Execute logs command
    if [[ "$is_logs_command" == true ]]; then
      if [[ $# -eq 0 ]]; then
        write_ergoms_message service_name_required red --stderr
        write_ergoms_message available_services yellow --stderr "items=$(units_list "$ERGO_ROOT" | tr ' ' ',')"
        write_ergoms_message logs_usage cyan --stderr
        exit 1
      fi
      
      local service_name="$1"
      local lines="${2:-500}"
      local module_filter=""

      if [[ "$service_name" == "celery-tasks" ]]; then
        if [[ -n "${2:-}" && "$2" =~ ^[0-9]+$ ]]; then
          lines="$2"
        elif [[ -n "${2:-}" ]]; then
          module_filter="$2"
          lines="${3:-500}"
        fi
        set_service_project_root "$ERGO_ROOT"
        show_celery_tasks_logs "$module_filter" "$lines"
        exit 0
      fi

      if [[ "$service_name" == "celery-beat" ]]; then
        if [[ -n "${2:-}" && "$2" =~ ^[0-9]+$ ]]; then
          lines="$2"
        elif [[ -n "${2:-}" ]]; then
          module_filter="$2"
          lines="${3:-500}"
        fi
        set_service_project_root "$ERGO_ROOT"
        show_celery_beat_logs "$module_filter" "$lines"
        exit 0
      fi

      if [[ "$service_name" == "setup-full" || "$service_name" == "setup" || "$service_name" == "ergoms" ]]; then
        set_service_project_root "$ERGO_ROOT"
        show_service_logs "$service_name" "$lines"
        exit 0
      fi
      
      set_service_project_root "$ERGO_ROOT"

      # shellcheck source=lib/logs_paths.sh
      source "$ERGO_ROOT/core/deployment/linux/lib/logs_paths.sh"
      service_name="$(normalize_service_name "$service_name" "$ERGO_ROOT")"

      # При NGINX_ENABLED Vite-служба не ставится — не открываем её логи с ошибкой
      if [[ "$service_name" == "ergo_ms_client_dev" || "$service_name" == "ergo_ms_client_dev.service" ]]; then
        if is_nginx_enabled "$ERGO_ROOT"; then
          write_ergoms_message skip_client_dev_nginx gray
          exit 0
        fi
      fi

      local valid=false
      local postgres_svc='ergo_ms_postgres'
      local postgres_from_env
      postgres_from_env="$(_ergo_env_value "$ERGO_ROOT" 'POSTGRES_SERVICE_LINUX' 2>/dev/null || true)"
      [[ -n "${postgres_from_env:-}" ]] && postgres_svc="$postgres_from_env"
      case "$service_name" in
        ergo_ms_nginx|ergo_ms_nginx.service|ergo_ms_redis|ergo_ms_redis.service|ergo_ms_redis|ergo_ms_meilisearch|ergo_ms_meilisearch.service|"$postgres_svc"|"${postgres_svc}.service"|ergo_ms_postgres|ergo_ms_postgres.service)
          valid=true
          ;;
      esac

      if [[ "$valid" == false ]]; then
        for u in $(units_list "$ERGO_ROOT"); do
          if [[ "$u" == "$service_name" || "$u" == "${service_name}.service" ]]; then
            valid=true
            service_name="$u"
            break
          fi
        done
      fi

      if [[ "$valid" == false ]]; then
        write_ergoms_message 'unknown_service' red stderr "name=$service_name"
        write_ergoms_message available_services yellow --stderr "items=$(units_list "$ERGO_ROOT" | tr '\n' ' ') ergo_ms_nginx ergo_ms_redis ergo_ms_meilisearch $postgres_svc celery-tasks celery-beat setup-full ergoms"
        exit 1
      fi

      show_service_logs "$service_name" "$lines"
      exit 0
    fi
    
    # Execute <module>:poetry command
    if [[ "$is_module_poetry_command" == true ]]; then
      invoke_module_poetry_command "$ERGO_ROOT" "$module_poetry_name" "$@"
      exit 0
    fi

    # Execute proxy command
    case "$command" in
      poetry)    invoke_poetry_command "$ERGO_ROOT" "$@" ;;
      api)       invoke_api_command "$ERGO_ROOT" "$@" ;;
      media_api) invoke_media_api_command "$ERGO_ROOT" "$@" ;;
      npm)       invoke_npm_command "$ERGO_ROOT" "$@" ;;
    esac
    exit 0
  fi

  # For non-proxy commands, require root/sudo
  require_root_or_sudo

  # Parse flags/positional root for service commands
  while (( "$#" )); do
    case "$1" in
      --root)
        shift; arg_root="${1:-}"; shift || true ;;
      --root=*)
        arg_root="${1#*=}"; shift ;;
      --purge)
        purge=true; shift ;;
      --no-cli)
        no_cli=true; shift ;;
      --recreate-venv)
        recreate_venv=true; shift ;;
      --with-postgres)
        with_postgres=true; shift ;;
      -h|--help)
        print_usage "$detected_root"; exit 0 ;;
      *)
        if [[ -z "$arg_root" ]]; then
          arg_root="$1"; shift
        else
          write_ergoms_message error_unknown_arg_plain red --stderr "arg=$1"; print_usage "$detected_root"; exit 1
        fi
        ;;
    esac
  done

  # Detect project root for service commands
  if [[ -n "$arg_root" ]]; then
    if [[ -d "$arg_root" ]]; then
      if command -v readlink >/dev/null 2>&1; then ERGO_ROOT="$(readlink -f "$arg_root")"; else ERGO_ROOT="$(cd "$arg_root" && pwd)"; fi
    else
      write_ergoms_message error_root_not_dir red --stderr "path=$arg_root"
      exit 1
    fi
  else
    ERGO_ROOT="$(detect_project_root)"
  fi

  # Устанавливаем корень проекта для функций служб
  set_service_project_root "$ERGO_ROOT"
  attach_cli_session_log "$ERGO_ROOT" "$command"

  # Fast-path commands that don't need install
  case "$command" in
    start)    invoke_lifecycle_runner "$ERGO_ROOT" start; exit 0 ;;
    stop)     invoke_lifecycle_runner "$ERGO_ROOT" stop; exit 0 ;;
    restart)  invoke_lifecycle_runner "$ERGO_ROOT" restart; exit 0 ;;
    status)   invoke_lifecycle_runner "$ERGO_ROOT" status; exit 0 ;;
    uninstall-services)
      extra=()
      [[ "$purge" == true ]] && extra+=(--purge)
      invoke_lifecycle_runner "$ERGO_ROOT" uninstall-services "${extra[@]}"
      exit 0
      ;;
    install-cli) create_cli_wrapper "$ERGO_ROOT"; exit 0 ;;
    uninstall-cli) remove_cli_wrapper "$ERGO_ROOT"; exit 0 ;;
    setup-full)
      extra=()
      [[ "${recreate_venv:-false}" == true ]] && extra+=(--recreate-venv)
      [[ "${with_postgres:-false}" == true ]] && extra+=(--with-postgres)
      invoke_lifecycle_runner "$ERGO_ROOT" setup-full "${extra[@]}"
      exit 0
      ;;
    install|install-services)
      invoke_lifecycle_runner "$ERGO_ROOT" install-services
      exit 0
      ;;
    install-api-service)
      invoke_lifecycle_runner "$ERGO_ROOT" install-api-service
      exit 0
      ;;
    install-client-service)
      invoke_lifecycle_runner "$ERGO_ROOT" install-client-service
      exit 0
      ;;
    install-worker-service)
      invoke_lifecycle_runner "$ERGO_ROOT" install-worker-service
      exit 0
      ;;
    install-beat-service)
      invoke_lifecycle_runner "$ERGO_ROOT" install-beat-service
      exit 0
      ;;
    install-media-service)
      invoke_lifecycle_runner "$ERGO_ROOT" install-media-service
      exit 0
      ;;
    *)        write_ergoms_message unknown_command red --stderr "name=$command"; print_usage "$detected_root"; exit 1 ;;
  esac
}

main "$@"
