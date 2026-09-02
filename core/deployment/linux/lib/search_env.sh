#!/usr/bin/env bash
# Effective search: ERGO_SEARCH_ENABLED (по умолчанию true, как в ergo_modes).

is_search_enabled() {
  local root="${1:-}"
  local env_file line value
  local explicit=''

  if [[ -z "$root" ]]; then
    root="$(detect_project_root 2>/dev/null || echo '')"
  fi
  [[ -n "$root" ]] || return 0

  for env_file in "$root/.env" "$root/env/search.env"; do
    [[ -f "$env_file" ]] || continue
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$line" =~ ^[[:space:]]*# ]] && continue
      if [[ "$line" =~ ^ERGO_SEARCH_ENABLED=(.*)$ ]]; then
        value="${BASH_REMATCH[1]}"
        value="${value//\"/}"
        value="${value//\'/}"
        value="$(_ergoms_trim "$value")"
        value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
        case "$value" in
          1|true|yes|on) explicit=1 ;;
          *) explicit=0 ;;
        esac
      fi
    done < "$env_file"
  done

  if [[ -n "$explicit" ]]; then
    [[ "$explicit" == "1" ]] && return 0
    return 1
  fi
  return 0
}

export -f is_search_enabled
