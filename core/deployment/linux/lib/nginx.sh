. "$(dirname "${BASH_SOURCE[0]}")/nginx_common.sh"

_nginx_install_build_deps() {
  local root="$1"
  write_ergoms_message nginx_install_build_deps cyan
  if command -v apt-get >/dev/null 2>&1; then
    local apt_opts=(
      -o "DPkg::Lock::Timeout=120"
      -o "APT::Acquire::Retries=3"
    )
    _nginx_wait_for_apt_locks "$root" || return 1
    if _nginx_sudo apt-get "${apt_opts[@]}" install -y -qq build-essential zlib1g-dev libssl-dev libpcre3-dev; then
      return 0
    fi
    _nginx_wait_for_apt_locks "$root" || return 1
    _nginx_sudo apt-get "${apt_opts[@]}" install -y -qq build-essential zlib1g-dev libssl-dev libpcre2-dev
    return $?
  fi
  if command -v dnf >/dev/null 2>&1; then
    _nginx_sudo dnf install -y -q gcc make zlib-devel openssl-devel pcre2-devel
    return $?
  fi
  if command -v yum >/dev/null 2>&1; then
    _nginx_sudo yum install -y -q gcc make zlib-devel openssl-devel pcre2-devel
    return $?
  fi
  if command -v pacman >/dev/null 2>&1; then
    _nginx_sudo pacman -Sy --noconfirm base-devel zlib openssl pcre2
    return $?
  fi
  write_ergoms_message nginx_error_pkg_mgr red --stderr
  return 1
}

_nginx_configure_pcre_flag() {
  if [[ -f /usr/include/pcre2.h ]] && ! [[ -f /usr/include/pcre.h ]]; then
    echo '--with-pcre'
  fi
}

_nginx_download_tarball() {
  local dest="$1"
  local url="https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$dest"
    return $?
  fi
  if command -v wget >/dev/null 2>&1; then
    wget -q -O "$dest" "$url"
    return $?
  fi
  write_ergoms_message nginx_error_need_curl_wget red --stderr
  return 1
}

_nginx_install_binary() {
  local root="$1"
  local nginx_bin
  nginx_bin="$(_nginx_binary "$root")"

  if [[ -x "$nginx_bin" ]]; then
    write_ergoms_message nginx_already_installed green "" "path=$nginx_bin"
    "$nginx_bin" -v 2>&1 | sed 's/^/[OK] /'
    return 0
  fi

  if ! _nginx_install_build_deps "$root"; then
    write_ergoms_message nginx_error_build_deps red --stderr
    return 1
  fi

  local nginx_dir tarball src_dir build_dir jobs pcre_flag cache_tmp
  nginx_dir="$(_nginx_packages_dir "$root")"
  cache_tmp="$root/virtual_env/cache/tmp"
  mkdir -p "$cache_tmp"
  build_dir="$(mktemp -d "$cache_tmp/nginx-build.XXXXXX")"
  tarball="$build_dir/nginx-${NGINX_VERSION}.tar.gz"
  src_dir="$build_dir/nginx-${NGINX_VERSION}"
  jobs="$(nproc 2>/dev/null || echo 2)"
  pcre_flag="$(_nginx_configure_pcre_flag)"

  local downloads cache_tarball
  downloads="$root/virtual_env/cache/downloads"
  mkdir -p "$downloads"
  cache_tarball="$downloads/nginx-${NGINX_VERSION}.tar.gz"
  write_ergoms_message nginx_downloading cyan "" "version=$NGINX_VERSION"
  if [[ -s "$cache_tarball" ]]; then
    cp -f "$cache_tarball" "$tarball"
  elif ! _nginx_download_tarball "$tarball"; then
    rm -rf "$build_dir"
    write_ergoms_message nginx_error_download red --stderr
    return 1
  else
    cp -f "$tarball" "$cache_tarball" || true
  fi

  write_ergoms_message nginx_unpacking cyan
  tar -xzf "$tarball" -C "$build_dir"

  if [[ ! -d "$src_dir" ]]; then
    rm -rf "$build_dir"
    write_ergoms_message nginx_error_src_dir red --stderr
    return 1
  fi

  mkdir -p "$nginx_dir/logs" "$nginx_dir/temp" "$nginx_dir/conf"

  write_ergoms_message nginx_configuring cyan "" "path=$nginx_dir"
  local -a configure_opts=(
    --prefix="$nginx_dir"
    --sbin-path="$nginx_dir/sbin/nginx"
    --conf-path="$nginx_dir/conf/nginx.conf"
    --pid-path="$nginx_dir/logs/nginx.pid"
    --lock-path="$nginx_dir/logs/nginx.lock"
    --error-log-path="$nginx_dir/logs/error.log"
    --http-log-path="$nginx_dir/logs/access.log"
    --http-client-body-temp-path="$nginx_dir/temp/client_body"
    --http-proxy-temp-path="$nginx_dir/temp/proxy"
    --http-fastcgi-temp-path="$nginx_dir/temp/fastcgi"
    --http-uwsgi-temp-path="$nginx_dir/temp/uwsgi"
    --http-scgi-temp-path="$nginx_dir/temp/scgi"
    --with-http_ssl_module
    --with-http_v2_module
  )
  if [[ -n "$pcre_flag" ]]; then
    configure_opts+=("$pcre_flag")
  fi

  if ! (cd "$src_dir" && ./configure "${configure_opts[@]}"); then
    rm -rf "$build_dir"
    write_ergoms_message nginx_error_configure red --stderr
    return 1
  fi

  write_ergoms_message nginx_building cyan
  if ! (cd "$src_dir" && make -j"$jobs"); then
    rm -rf "$build_dir"
    write_ergoms_message nginx_error_make red --stderr
    return 1
  fi

  write_ergoms_message nginx_installing_to cyan "" "path=$nginx_dir"
  if ! (cd "$src_dir" && make install); then
    rm -rf "$build_dir"
    write_ergoms_message nginx_error_make_install red --stderr
    return 1
  fi

  rm -rf "$build_dir"

  if [[ -x "$nginx_bin" ]]; then
    write_ergoms_message nginx_installed_to green "" "path=$nginx_dir"
    "$nginx_bin" -v 2>&1 | sed 's/^/[OK] /'
    return 0
  fi

  write_ergoms_message nginx_error_bin_missing red --stderr "path=$nginx_bin"
  return 1
}

_nginx_log_env() {
  local root="$1"
  shift
  local py="$root/virtual_env/python/bin/python"
  local script="$root/core/deployment/scripts/log_env.py"
  if [[ -x "$py" && -f "$script" ]]; then
    "$py" "$script" "$@" "$root" 2>/dev/null
  fi
}

_nginx_write_main_conf() {
  local root="$1"
  local site_conf="$2"
  local main_conf nginx_dir runtime_logs_dir central_logs_dir temp_dir include_path
  local nginx_error_level error_log_path access_log_path access_log_line
  nginx_dir="$(_nginx_packages_dir "$root")"
  main_conf="$(_nginx_main_conf "$root")"
  runtime_logs_dir="$nginx_dir/logs"
  central_logs_dir="$(_nginx_log_env "$root" logs-dir)"
  [[ -z "$central_logs_dir" ]] && central_logs_dir="$root/logs"
  nginx_error_level="$(_nginx_log_env "$root" nginx-error-level)"
  [[ -z "$nginx_error_level" ]] && nginx_error_level="warn"
  error_log_path="$(_nginx_log_env "$root" path NGINX_ERROR)"
  access_log_path="$(_nginx_log_env "$root" path NGINX_ACCESS)"
  [[ -z "$error_log_path" ]] && error_log_path="$central_logs_dir/nginx-error.log"
  [[ -z "$access_log_path" ]] && access_log_path="$central_logs_dir/nginx-access.log"
  if [[ "$(_nginx_log_env "$root" nginx-access-enabled)" == "false" ]]; then
    access_log_line="access_log off;"
  else
    access_log_line="access_log $access_log_path;"
  fi
  temp_dir="$nginx_dir/temp"
  include_path="$site_conf"

  mkdir -p "$nginx_dir/conf" "$runtime_logs_dir" "$temp_dir" "$central_logs_dir"

  cat >"$main_conf" <<EOF
worker_processes auto;

error_log $error_log_path $nginx_error_level;
pid       $runtime_logs_dir/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include       mime.types;
    default_type  application/octet-stream;

    $access_log_line

    sendfile        on;
    keepalive_timeout 65;

    client_body_temp_path $temp_dir/client_body;
    proxy_temp_path       $temp_dir/proxy;
    fastcgi_temp_path     $temp_dir/fastcgi;
    uwsgi_temp_path       $temp_dir/uwsgi;
    scgi_temp_path        $temp_dir/scgi;

    include $include_path;
}
EOF
}

_nginx_stop_system_nginx_if_running() {
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet nginx 2>/dev/null; then
      write_ergoms_message nginx_stop_system_conflict yellow
      _nginx_sudo systemctl stop nginx 2>/dev/null || true
    fi
  fi
}

_nginx_render_template() {
  local template="$1"
  local root="$2"
  local server_name="${3:-localhost}"
  local listen_host="${4:-0.0.0.0}"
  local listen_port="${5:-80}"
  local use_ssl="${6:-false}"
  local py="$root/virtual_env/python/bin/python"
  [[ -x "$py" ]] || py="python3"

  local script="$root/core/deployment/scripts/render_nginx_config.py"
  if [[ ! -f "$script" ]]; then
    write_ergoms_message error_render_nginx_missing red --stderr
    return 1
  fi

  local -a args=(
    "$script"
    --template "$template"
    --root "$root"
    --server-name "$server_name"
    --listen-host "$listen_host"
    --listen-port "$listen_port"
    --use-https "$use_ssl"
  )
  if [[ "$use_ssl" == "true" ]]; then
    args+=(
      --ssl-cert "${ERGO_SSL_CERT:-/etc/ssl/certs/ssl-cert-snakeoil.pem}"
      --ssl-key "${ERGO_SSL_KEY:-/etc/ssl/private/ssl-cert-snakeoil.key}"
    )
  fi

  "$py" "${args[@]}"
}

_nginx_select_template() {
  local root="$1"
  local use_ssl="${2:-false}"
  local nginx_dir="$root/core/deployment/nginx"

  if [[ "$use_ssl" == "true" ]] && [[ -f "$nginx_dir/ergo_ms.conf.template" ]]; then
    echo "$nginx_dir/ergo_ms.conf.template"
  else
    echo "$nginx_dir/ergo_ms_http.conf.template"
  fi
}

_nginx_write_rendered_site() {
  local root="$1"
  local server_name="$2"
  local listen_host="$3"
  local listen_port="$4"
  local use_ssl="$5"
  local template rendered site_conf

  template="$(_nginx_select_template "$root" "$use_ssl")"
  if [[ ! -f "$template" ]]; then
    write_ergoms_message error_template_not_found red --stderr "path=$template"
    return 1
  fi

  local dist_path="$root/core/client/dist"
  if [[ ! -f "$dist_path/index.html" ]]; then
    write_ergoms_message error_index_html_not_found red --stderr "path=$dist_path"
    write_ergoms_message nginx_need_client_build yellow >&2
    echo "    ergoms client-build" >&2
    write_ergoms_message hint_then_install_nginx yellow >&2
    return 1
  fi

  rendered="$(_nginx_render_template "$template" "$root" "$server_name" "$listen_host" "$listen_port" "$use_ssl")"
  site_conf="$(_nginx_site_conf "$root")"
  printf '%s\n' "$rendered" >"$site_conf"
  write_ergoms_message ok_config_written green "" "path=$site_conf"
  _nginx_write_main_conf "$root" "$site_conf"
  restore_project_ownership "$root" "$(_nginx_packages_dir "$root")"
}

_nginx_resolve_listen() {
  # После _nginx_read_env / _nginx_resolve_env. Имена 2–5 — nameref на переменные вызывающего.
  local root="$1"
  local -n _rl_name="$2"
  local -n _rl_host="$3"
  local -n _rl_port="$4"
  local -n _rl_ssl="$5"
  local override_name="${6:-}"
  local override_port="${7:-}"
  local override_ssl="${8:-false}"
  _rl_host="${NGINX_LISTEN_HOST:-0.0.0.0}"
  _rl_port="${override_port:-${NGINX_LISTEN_PORT:-80}}"
  _rl_ssl="${override_ssl:-false}"
  if [[ -n "$override_name" ]]; then
    _rl_name="$override_name"
  elif [[ -n "${NGINX_PUBLIC_HOST:-}" ]]; then
    _rl_name="$NGINX_PUBLIC_HOST"
  else
    _rl_name="${NGINX_SERVER_NAME:-localhost}"
  fi
  if _nginx_should_use_ssl "$_rl_ssl" "$_rl_port"; then
    _rl_ssl="true"
    export ERGO_SSL_CERT="${ERGO_SSL_CERT:-/etc/ssl/certs/ssl-cert-snakeoil.pem}"
    export ERGO_SSL_KEY="${ERGO_SSL_KEY:-/etc/ssl/private/ssl-cert-snakeoil.key}"
    _nginx_warn_insecure_certs "$root"
  fi
}

_nginx_resolve_env() {
  local root="$1"
  local py="$root/virtual_env/python/bin/python"
  local script="$root/core/deployment/scripts/resolve_env.py"
  [[ -x "$py" && -f "$script" ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    [[ -n "$key" ]] && export "$key=$value"
  done < <("$py" "$script" --root "$root" --scope nginx 2>/dev/null || true)
}

_nginx_unit_content() {
  local root="$1"
  local nginx_bin main_conf nginx_dir
  nginx_bin="$(_nginx_binary "$root")"
  main_conf="$(_nginx_main_conf "$root")"
  nginx_dir="$(_nginx_packages_dir "$root")"

  cat <<UNIT
[Unit]
Description=Ergo MS - Nginx
After=network.target

[Service]
Type=forking
PIDFile=$nginx_dir/logs/nginx.pid
WorkingDirectory=$nginx_dir
ExecStartPre=$nginx_bin -t -c $main_conf
ExecStart=$nginx_bin -c $main_conf
ExecReload=$nginx_bin -s reload -c $main_conf
ExecStop=$nginx_bin -s quit -c $main_conf
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
}

nginx_install_service() {
  local root="$1"
  if ! _nginx_is_installed "$root"; then
    write_ergoms_message error_not_installed_run red --stderr "name=Nginx" "cmd=ergoms install-nginx"
    return 1
  fi

  nginx_stop_service "$root" quiet 2>/dev/null || true

  local content
  content="$(_nginx_unit_content "$root")"
  install_unit "$NGINX_SERVICE_NAME" "$content" "$root"
  _nginx_sudo systemctl daemon-reload
  enable_and_start "${NGINX_SERVICE_NAME}.service"
  write_ergoms_message ok_systemd_service_installed_running green "" "name=nginx"
}

nginx_install() {
  local root="$1"
  _nginx_read_env "$root"
  _nginx_resolve_env "$root"
  local server_name listen_host listen_port use_ssl
  _nginx_resolve_listen "$root" server_name listen_host listen_port use_ssl "${2:-}" "${3:-}" "${4:-false}"

  echo ""
  write_ergoms_message heading_install_only cyan "" "name=Nginx"
  echo ""

  _nginx_stop_system_nginx_if_running

  if ! _nginx_install_binary "$root"; then
    return 1
  fi

  if ! _nginx_write_rendered_site "$root" "$server_name" "$listen_host" "$listen_port" "$use_ssl"; then
    return 1
  fi

  local nginx_bin main_conf nginx_dir
  nginx_bin="$(_nginx_binary "$root")"
  main_conf="$(_nginx_main_conf "$root")"
  nginx_dir="$(_nginx_packages_dir "$root")"

  write_ergoms_message arrow_checking_nginx_config cyan
  if (cd "$nginx_dir" && "$nginx_bin" -t -c "$main_conf"); then
    write_ergoms_message ok_config_valid green
  else
    write_ergoms_message error_nginx_t_failed_dot red --stderr
    return 1
  fi

  nginx_install_service "$root"
  restore_project_ownership "$root" "$nginx_dir"

  write_ergoms_message ok_installed_and_running green "" "name=Nginx"
  write_ergoms_message label_path cyan "" "path=$nginx_dir"
  write_ergoms_message label_config cyan "" "path=$(_nginx_site_conf "$root")"
  write_ergoms_message label_logs cyan "" "path=$nginx_dir/logs"
  if [[ "$use_ssl" == "true" ]]; then
    write_ergoms_message label_listening_https cyan "" "host=$server_name"
  else
    write_ergoms_message label_listening_http_bind cyan "" "host=$server_name" "port=$listen_port" "bind=$listen_host"
  fi
}

nginx_uninstall() {
  local root="$1"
  local purge="${2:-false}"

  echo ""
  write_ergoms_message heading_remove cyan "" "name=Nginx"
  echo ""

  _nginx_stop_system_nginx_if_running
  nginx_stop_service "$root" quiet 2>/dev/null || true

  if [[ -f "$(_nginx_unit_file "$root")" ]] || [[ -L "/etc/systemd/system/${NGINX_SERVICE_NAME}.service" ]]; then
    write_ergoms_message arrow_remove_nginx_unit yellow
    local unit_file link_path
    unit_file="$(_nginx_unit_file "$root")"
    link_path="/etc/systemd/system/${NGINX_SERVICE_NAME}.service"
    if [[ $(id -u) -eq 0 ]]; then
      systemctl disable --now "${NGINX_SERVICE_NAME}.service" 2>/dev/null || true
      rm -f "$link_path" "$unit_file"
      systemctl daemon-reload
    else
      sudo systemctl disable --now "${NGINX_SERVICE_NAME}.service" 2>/dev/null || true
      sudo rm -f "$link_path"
      rm -f "$unit_file" 2>/dev/null || true
      sudo systemctl daemon-reload
    fi
    write_ergoms_message ok_nginx_unit_removed green
  fi

  local site_conf
  site_conf="$(_nginx_site_conf "$root")"
  if [[ -f "$site_conf" ]]; then
    rm -f "$site_conf"
    write_ergoms_message ok_config_removed green "" "path=$site_conf"
  fi

  local nginx_dir
  nginx_dir="$(_nginx_packages_dir "$root")"
  if [[ "$purge" == "true" ]] && [[ -d "$nginx_dir" ]]; then
    rm -rf "$nginx_dir"
    write_ergoms_message ok_removed_path green "" "path=$nginx_dir"
  elif [[ -d "$nginx_dir" ]]; then
    write_ergoms_message ok_stopped_binaries_kept green "" "name=Nginx" "pkg=nginx" "purge_flag=--purge"
  fi

  write_ergoms_message ok_removed green "" "name=Nginx"
}

nginx_start_service() {
  local root="$1"
  if ! _nginx_is_installed "$root"; then
    write_ergoms_message error_not_installed_run red --stderr "name=Nginx" "cmd=ergoms install-nginx"
    return 1
  fi

  if [[ -f "$(_nginx_unit_file "$root")" ]] || [[ -L "/etc/systemd/system/${NGINX_SERVICE_NAME}.service" ]]; then
    if systemctl is-active --quiet "${NGINX_SERVICE_NAME}.service" 2>/dev/null; then
      write_ergoms_message ok_service_already_running green "" "name=nginx"
      return 0
    fi
    write_ergoms_message arrow_starting_service cyan "" "name=nginx"
    _nginx_sudo systemctl start "${NGINX_SERVICE_NAME}.service"
    write_ergoms_message ok_service_started green "" "name=nginx"
    return 0
  fi

  nginx_stop_service "$root" quiet 2>/dev/null || true

  local nginx_bin main_conf nginx_dir
  nginx_bin="$(_nginx_binary "$root")"
  main_conf="$(_nginx_main_conf "$root")"
  nginx_dir="$(_nginx_packages_dir "$root")"
  local pid_file="$nginx_dir/logs/nginx.pid"
  rm -f "$pid_file"

  write_ergoms_message arrow_starting cyan "" "name=nginx"
  (cd "$nginx_dir" && "$nginx_bin" -c "$main_conf")
  sleep 2

  if [[ -f "$pid_file" ]] || pgrep -f "$nginx_bin" >/dev/null 2>&1; then
    write_ergoms_message ok_started green "" "name=Nginx"
    return 0
  fi

  err_log="$(_nginx_log_env "$root" path NGINX_ERROR)"; [[ -z "$err_log" ]] && err_log="$root/logs/nginx-error.log"; write_ergoms_message error_start_failed_check_logs red --stderr "name=Nginx" "path=$err_log"
  return 1
}

_nginx_remove_stale_pid_file() {
  local root="${1:-}"
  [[ -z "$root" ]] && return 0
  _nginx_is_installed "$root" || return 0

  local pid_file
  pid_file="$(_nginx_packages_dir "$root")/logs/nginx.pid"
  [[ -f "$pid_file" ]] || return 0

  local pid
  pid="$(tr -d '[:space:]' <"$pid_file" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]]; then
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pid_file"
      return 0
    fi
    local comm
    comm="$(tr -d '\0' <"/proc/$pid/comm" 2>/dev/null || true)"
    if [[ "$comm" != "nginx" ]]; then
      rm -f "$pid_file"
    fi
    return 0
  fi
  rm -f "$pid_file"
}

_nginx_wait_stopped() {
  local root="${1:-}"
  local timeout_sec="${2:-10}"
  local i max=$((timeout_sec * 2))

  for ((i = 0; i < max; i++)); do
    _nginx_remove_stale_pid_file "$root"
    if ! _nginx_is_running "$root"; then
      return 0
    fi
    sleep 0.5
  done

  _nginx_remove_stale_pid_file "$root"
  _nginx_is_running "$root" && return 1
  return 0
}

_nginx_force_stop_processes() {
  pkill -f 'virtual_env/packages/nginx/sbin/nginx' 2>/dev/null || true
  pkill -f 'virtual_env/packages/nginx/nginx.exe' 2>/dev/null || true
}

_nginx_is_running() {
  local root="${1:-}"

  _nginx_remove_stale_pid_file "$root"

  if [[ -f "$(_nginx_unit_file "$root")" ]] || [[ -L "/etc/systemd/system/${NGINX_SERVICE_NAME}.service" ]] && systemctl is-active --quiet "${NGINX_SERVICE_NAME}.service" 2>/dev/null; then
    return 0
  fi
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null; then
    return 0
  fi
  if [[ -n "$root" ]] && _nginx_is_installed "$root"; then
    local nginx_dir
    nginx_dir="$(_nginx_packages_dir "$root")"
    if pgrep -f "$nginx_dir/sbin/nginx" >/dev/null 2>&1; then
      return 0
    fi
  fi
  if pgrep -f 'virtual_env/packages/nginx/sbin/nginx' >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

nginx_stop_service() {
  local root="${1:-}"
  local quiet="${2:-}"

  _nginx_remove_stale_pid_file "$root"

  if ! _nginx_is_running "$root"; then
    [[ -z "$quiet" ]] && write_ergoms_message skip_was_not_running gray "" "name=Nginx"
    return 0
  fi

  if [[ -f "$(_nginx_unit_file "$root")" ]] || [[ -L "/etc/systemd/system/${NGINX_SERVICE_NAME}.service" ]] && systemctl is-active --quiet "${NGINX_SERVICE_NAME}.service" 2>/dev/null; then
    write_ergoms_message arrow_stopping_service cyan "" "name=nginx"
    _nginx_sudo systemctl stop "${NGINX_SERVICE_NAME}.service"
    if ! _nginx_wait_stopped "$root" 15; then
      _nginx_force_stop_processes
      if ! _nginx_wait_stopped "$root" 5; then
        write_ergoms_message error_stop_service_failed red --stderr "name=nginx"
        return 1
      fi
    fi
    write_ergoms_message ok_service_stopped green "" "name=nginx"
    return 0
  fi

  if [[ -n "$root" ]] && _nginx_is_installed "$root"; then
    local nginx_bin main_conf nginx_dir
    nginx_bin="$(_nginx_binary "$root")"
    main_conf="$(_nginx_main_conf "$root")"
    nginx_dir="$(_nginx_packages_dir "$root")"

    if [[ -x "$nginx_bin" ]]; then
      write_ergoms_message arrow_stopping_process cyan "" "name=nginx"
      (cd "$nginx_dir" && "$nginx_bin" -s quit -c "$main_conf" 2>/dev/null) || true
      if _nginx_wait_stopped "$root" 8; then
        [[ -z "$quiet" ]] && write_ergoms_message ok_stopped green "" "name=Nginx"
        return 0
      fi
    fi
  fi

  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null; then
    _nginx_sudo systemctl stop nginx 2>/dev/null || true
  fi

  _nginx_force_stop_processes

  if ! _nginx_wait_stopped "$root" 5; then
    write_ergoms_message error_stop_failed red --stderr "name=nginx"
    return 1
  fi
  [[ -z "$quiet" ]] && write_ergoms_message ok_stopped green "" "name=Nginx"
}

nginx_reload_service() {
  local root="$1"
  if ! _nginx_is_installed "$root"; then
    write_ergoms_message error_not_installed_run red --stderr "name=Nginx" "cmd=ergoms install-nginx"
    return 1
  fi

  _nginx_read_env "$root"
  _nginx_resolve_env "$root"
  local server_name listen_host listen_port use_ssl
  _nginx_resolve_listen "$root" server_name listen_host listen_port use_ssl
  if ! _nginx_write_rendered_site "$root" "$server_name" "$listen_host" "$listen_port" "$use_ssl"; then
    return 1
  fi

  local nginx_bin main_conf nginx_dir
  nginx_bin="$(_nginx_binary "$root")"
  main_conf="$(_nginx_main_conf "$root")"
  nginx_dir="$(_nginx_packages_dir "$root")"

  write_ergoms_message arrow_checking_config cyan
  if ! (cd "$nginx_dir" && "$nginx_bin" -t -c "$main_conf"); then
    write_ergoms_message error_config_check_failed_no_reload red --stderr
    return 1
  fi

  if [[ -f "$(_nginx_unit_file "$root")" ]] || [[ -L "/etc/systemd/system/${NGINX_SERVICE_NAME}.service" ]] && systemctl is-active --quiet "${NGINX_SERVICE_NAME}.service" 2>/dev/null; then
    write_ergoms_message arrow_reload_nginx_service cyan
    _nginx_sudo systemctl reload "${NGINX_SERVICE_NAME}.service"
    write_ergoms_message ok_reloaded green "" "name=Nginx"
    return 0
  fi

  write_ergoms_message arrow_reloading cyan "" "name=nginx"
  (cd "$nginx_dir" && "$nginx_bin" -s reload -c "$main_conf")
  write_ergoms_message ok_reloaded green "" "name=Nginx"
}

nginx_status_service() {
  local root="$1"
  if ! _nginx_is_installed "$root"; then
    write_ergoms_message component_not_installed gray "" "name=Nginx"
    write_ergoms_message label_expected_path gray "" "path=$(_nginx_packages_dir "$root")"
    return 0
  fi

  local nginx_dir site_conf
  nginx_dir="$(_nginx_packages_dir "$root")"
  site_conf="$(_nginx_site_conf "$root")"

  echo ""
  write_ergoms_message heading_status cyan "" "name=Nginx"

  if [[ -f "$(_nginx_unit_file "$root")" ]] || [[ -L "/etc/systemd/system/${NGINX_SERVICE_NAME}.service" ]]; then
    if systemctl is-active --quiet "${NGINX_SERVICE_NAME}.service" 2>/dev/null; then
      write_ergoms_message label_service_status green "" "name=$NGINX_SERVICE_NAME" "status=Running"
    else
      write_ergoms_message label_service_status yellow "" "name=$NGINX_SERVICE_NAME" "status=Not running"
    fi
  elif pgrep -f "$nginx_dir/sbin/nginx" >/dev/null 2>&1; then
    write_ergoms_message status_running_pid_process green "" "pid=$(pgrep -f "$nginx_dir/sbin/nginx" | head -n1)"
  else
    write_ergoms_message status_process_not_running red
  fi

  if [[ -f "$site_conf" ]]; then
    write_ergoms_message config_installed_at cyan "" "path=$site_conf"
  else
    write_ergoms_message config_not_installed yellow
  fi

  write_ergoms_message label_path_indent2 cyan "" "path=$nginx_dir"
  write_ergoms_message label_logs_indent2 cyan "" "path=$nginx_dir/logs"
  echo ""
}

nginx_test_config() {
  local root="$1"
  if ! _nginx_is_installed "$root"; then
    write_ergoms_message error_component_not_installed red --stderr "name=Nginx"
    return 1
  fi

  local nginx_bin main_conf nginx_dir
  nginx_bin="$(_nginx_binary "$root")"
  main_conf="$(_nginx_main_conf "$root")"
  nginx_dir="$(_nginx_packages_dir "$root")"
  (cd "$nginx_dir" && "$nginx_bin" -t -c "$main_conf")
}

export -f nginx_install
export -f nginx_install_service
export -f nginx_uninstall
export -f nginx_start_service
export -f nginx_stop_service
export -f nginx_reload_service
export -f nginx_status_service
export -f nginx_test_config
