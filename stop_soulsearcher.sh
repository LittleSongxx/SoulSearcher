#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$PROJECT_ROOT/.run"
COMPOSE_FILE="$PROJECT_ROOT/docker/docker-compose.yml"
COMPOSE_ENV_FILE="$RUN_DIR/compose.env"
ENV_FILE="$PROJECT_ROOT/.env"
DOCKER_CONFIG_FALLBACK_DIR="$RUN_DIR/docker-config"

STOP_ALL=0
SILENT=0
APP_SERVICES=(backend frontend)

usage() {
  cat <<'EOF'
Usage: ./stop_soulsearcher.sh [--all|--docker] [--silent] [--help]

Options:
  --all      stop all Docker services with docker compose down
  --docker   alias of --all
  --silent   suppress non-essential output
  --help     show this help message

Default behavior stops only backend and frontend, keeping Postgres and Redis running.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --all|--docker)
        STOP_ALL=1
        ;;
      --silent)
        SILENT=1
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        echo "[error] unknown option: $1"
        usage
        exit 1
        ;;
    esac
    shift
  done
}

log() {
  if (( ! SILENT )); then
    echo "$1"
  fi
}

compose() {
  if [[ -f "$ENV_FILE" && -f "$COMPOSE_ENV_FILE" ]]; then
    docker compose --env-file "$ENV_FILE" --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" "$@"
  else
    docker compose -f "$COMPOSE_FILE" "$@"
  fi
}

prepare_docker_config() {
  local docker_config_dir config_path creds_store helper

  docker_config_dir="${DOCKER_CONFIG:-$HOME/.docker}"
  config_path="$docker_config_dir/config.json"
  if [[ ! -r "$config_path" ]]; then
    return 0
  fi

  creds_store="$(tr -d '\n' < "$config_path" | sed -nE 's/.*"credsStore"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p')"
  if [[ -z "$creds_store" ]]; then
    return 0
  fi

  helper="docker-credential-$creds_store"
  if command -v "$helper" >/dev/null 2>&1; then
    return 0
  fi

  mkdir -p "$DOCKER_CONFIG_FALLBACK_DIR"
  printf '{\n  "auths": {}\n}\n' > "$DOCKER_CONFIG_FALLBACK_DIR/config.json"
  export DOCKER_CONFIG="$DOCKER_CONFIG_FALLBACK_DIR"
  log "[warn] docker credsStore '$creds_store' is configured but helper '$helper' is unavailable"
  log "[warn] using temporary anonymous docker config for this project"
}

parse_args "$@"

cd "$PROJECT_ROOT"

if [[ -f "$COMPOSE_ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$COMPOSE_ENV_FILE"
  set +a
fi

if command -v docker >/dev/null 2>&1; then
  prepare_docker_config
  if (( STOP_ALL )); then
    log "stopping all SoulSearcher Docker services..."
    compose down >/dev/null 2>&1 || true
    log "soulsearcher containers stopped"
  else
    log "stopping SoulSearcher app containers..."
    if ! compose stop "${APP_SERVICES[@]}" >/dev/null 2>&1; then
      docker stop soulsearcher_backend soulsearcher_frontend >/dev/null 2>&1 || true
    fi
    log "soulsearcher app containers stopped (backend/frontend)"
    log "data containers left running (postgres/redis)"
    log "use ./stop_soulsearcher.sh --all to stop everything"
  fi
else
  log "[warn] docker command not found; skipped container stop"
fi

log ""
log "ports:"
log "- frontend : http://127.0.0.1:${FRONTEND_PORT:-3100}"
log "- backend  : http://127.0.0.1:${BACKEND_PORT:-8001}"
log "soulsearcher stop completed"
