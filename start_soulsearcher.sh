#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$PROJECT_ROOT/.run"
DATA_DIR="$PROJECT_ROOT/.docker-data"
LOG_DIR="$PROJECT_ROOT/logs"
COMPOSE_FILE="$PROJECT_ROOT/docker/docker-compose.yml"
COMPOSE_ENV_FILE="$RUN_DIR/compose.env"
ENV_FILE="$PROJECT_ROOT/.env"
ENV_EXAMPLE_FILE="$PROJECT_ROOT/.env.example"
WEB_ENV_FILE="$PROJECT_ROOT/web/.env.local"
WEB_ENV_EXAMPLE_FILE="$PROJECT_ROOT/web/.env.local.example"
CONFIG_FILE="$PROJECT_ROOT/config/config.toml"
CONFIG_EXAMPLE_FILE="$PROJECT_ROOT/config/config.example.toml"
INSIGHT_ENV_FILE="${SOULSEARCHER_SECRETS_ENV_FILE:-}"
DOCKER_CONFIG_FALLBACK_DIR="$RUN_DIR/docker-config"

DO_BUILD=0
CREATED_ENV=0
CURRENT_STEP=1
TOTAL_STEPS=6

usage() {
  cat <<'EOF'
Usage: ./start_soulsearcher.sh [--build] [--help]

Options:
  --build    rebuild Docker images before starting services
  --help     show this help message

The script keeps all Python/Node runtime dependencies inside Docker.
It records selected host ports in .run/compose.env and reuses them on later starts.
It scaffolds .env, web/.env.local, and config/config.toml from committed templates when missing.

Optional environment variables:
  SOULSEARCHER_SECRETS_ENV_FILE   path to another .env file whose compatible keys should be imported
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --build)
        DO_BUILD=1
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

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "[error] required command not found: $name"
    exit 1
  fi
}

check_prerequisites() {
  require_command docker
  require_command curl
  require_command awk
  require_command sed
  require_command mktemp

  if ! docker compose version >/dev/null 2>&1; then
    echo "[error] docker compose plugin is required"
    exit 1
  fi

  if ! command -v ss >/dev/null 2>&1 && ! command -v timeout >/dev/null 2>&1; then
    echo "[error] either 'ss' or 'timeout' is required for host port detection"
    exit 1
  fi
}

log_step() {
  local message="$1"
  echo "[$CURRENT_STEP/$TOTAL_STEPS] $message"
  CURRENT_STEP=$((CURRENT_STEP + 1))
}

compose() {
  docker compose --env-file "$ENV_FILE" --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" "$@"
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
  echo "[warn] docker credsStore '$creds_store' is configured but helper '$helper' is unavailable"
  echo "[warn] using temporary anonymous docker config for public image pulls in this project"
}

dotenv_get() {
  local file="$1"
  local key="$2"

  [[ -f "$file" ]] || return 0
  awk -v key="$key" '
    /^[[:space:]]*#/ { next }
    index($0, key "=") == 1 {
      sub("^[^=]*=", "")
      print
      exit
    }
  ' "$file"
}

dotenv_value_is_set() {
  local value="$1"
  value="${value%$'\r'}"
  value="${value#\"}"
  value="${value%\"}"
  value="${value#\'}"
  value="${value%\'}"
  [[ -n "${value//[[:space:]]/}" ]]
}

dotenv_has_value() {
  local file="$1"
  local key="$2"
  local value
  value="$(dotenv_get "$file" "$key" || true)"
  dotenv_value_is_set "$value"
}

upsert_env() {
  local file="$1"
  local key="$2"
  local value="$3"
  local tmp

  mkdir -p "$(dirname "$file")"
  touch "$file"
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { done = 0 }
    index($0, key "=") == 1 {
      print key "=" value
      done = 1
      next
    }
    { print }
    END {
      if (!done) {
        print key "=" value
      }
    }
  ' "$file" > "$tmp"
  mv "$tmp" "$file"
}

copy_env_from_insight() {
  local source_key="$1"
  local target_key="$2"
  local overwrite="${3:-0}"
  local value

  [[ -f "$INSIGHT_ENV_FILE" ]] || return 0
  value="$(dotenv_get "$INSIGHT_ENV_FILE" "$source_key" || true)"
  if ! dotenv_value_is_set "$value"; then
    return 0
  fi

  if [[ "$overwrite" == "1" ]] || ! dotenv_has_value "$ENV_FILE" "$target_key"; then
    upsert_env "$ENV_FILE" "$target_key" "$value"
  fi
}

ensure_env_files() {
  if [[ ! -f "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_EXAMPLE_FILE" ]]; then
      echo "[error] .env missing and .env.example not found"
      exit 1
    fi
    cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
    CREATED_ENV=1
    echo "[info] created .env from .env.example"
  fi

  if [[ ! -f "$WEB_ENV_FILE" ]]; then
    if [[ -f "$WEB_ENV_EXAMPLE_FILE" ]]; then
      cp "$WEB_ENV_EXAMPLE_FILE" "$WEB_ENV_FILE"
      echo "[info] created web/.env.local from example"
    else
      touch "$WEB_ENV_FILE"
    fi
  fi

  if [[ ! -f "$CONFIG_FILE" ]]; then
    if [[ -f "$CONFIG_EXAMPLE_FILE" ]]; then
      cp "$CONFIG_EXAMPLE_FILE" "$CONFIG_FILE"
      echo "[info] created config/config.toml from config/config.example.toml"
    else
      echo "[warn] config/config.example.toml not found; skipping config/config.toml scaffold"
    fi
  fi

  if ! dotenv_value_is_set "$INSIGHT_ENV_FILE"; then
    INSIGHT_ENV_FILE="$(dotenv_get "$ENV_FILE" "SOULSEARCHER_SECRETS_ENV_FILE" || true)"
  fi
}

sync_insightvault_env() {
  local overwrite="$CREATED_ENV"
  local openai_base qwen_base openai_key

  if ! dotenv_value_is_set "$INSIGHT_ENV_FILE"; then
    return 0
  fi

  if [[ ! -f "$INSIGHT_ENV_FILE" ]]; then
    echo "[warn] optional secrets source .env not found: $INSIGHT_ENV_FILE"
    return 0
  fi

  copy_env_from_insight "OPENAI_API_KEY" "OPENAI_API_KEY" "$overwrite"
  copy_env_from_insight "OPENAI_BASE_URL" "OPENAI_BASE_URL" "$overwrite"
  copy_env_from_insight "DEEPSEEK_API_KEY" "OPENAI_API_KEY" "0"
  copy_env_from_insight "DEEPSEEK_BASE_URL" "OPENAI_BASE_URL" "0"
  copy_env_from_insight "DASHSCOPE_API_KEY" "DASHSCOPE_API_KEY" "$overwrite"

  copy_env_from_insight "LLM_DEFAULT_MODEL" "PRIMARY_MODEL" "$overwrite"
  copy_env_from_insight "LLM_DEFAULT_MODEL" "REASONING_MODEL" "$overwrite"
  copy_env_from_insight "DEEPSEEK_MODEL" "PRIMARY_MODEL" "0"
  copy_env_from_insight "DEEPSEEK_MODEL" "REASONING_MODEL" "0"
  copy_env_from_insight "QWEN_MODEL" "PRIMARY_MODEL" "0"
  copy_env_from_insight "QWEN_MODEL" "REASONING_MODEL" "0"

  openai_base="$(dotenv_get "$ENV_FILE" "OPENAI_BASE_URL" || true)"
  qwen_base="$(dotenv_get "$INSIGHT_ENV_FILE" "QWEN_BASE_URL" || true)"
  if [[ "$openai_base$qwen_base" == *"dashscope"* || "$openai_base$qwen_base" == *"aliyuncs"* ]]; then
    local qwen_model current_model
    qwen_model="$(dotenv_get "$INSIGHT_ENV_FILE" "QWEN_MODEL" || true)"
    current_model="$(dotenv_get "$ENV_FILE" "PRIMARY_MODEL" || true)"
    if dotenv_value_is_set "$qwen_model" && [[ "${current_model,,}" == "deepseek-chat" || "${current_model,,}" == "deepseek-v4-flash" ]]; then
      upsert_env "$ENV_FILE" "PRIMARY_MODEL" "$qwen_model"
      upsert_env "$ENV_FILE" "REASONING_MODEL" "$qwen_model"
    fi
  fi

  if ! dotenv_has_value "$ENV_FILE" "DASHSCOPE_API_KEY"; then
    if [[ "$openai_base$qwen_base" == *"dashscope"* || "$openai_base$qwen_base" == *"aliyuncs"* ]]; then
      openai_key="$(dotenv_get "$ENV_FILE" "OPENAI_API_KEY" || true)"
      if dotenv_value_is_set "$openai_key"; then
        upsert_env "$ENV_FILE" "DASHSCOPE_API_KEY" "$openai_key"
      fi
    fi
  fi

  local engines
  engines="$(dotenv_get "$ENV_FILE" "SEARCH_ENGINES" || true)"
  if ! dotenv_value_is_set "$engines"; then
    if dotenv_has_value "$ENV_FILE" "TAVILY_API_KEY"; then
      upsert_env "$ENV_FILE" "SEARCH_ENGINES" "tavily"
    else
      upsert_env "$ENV_FILE" "SEARCH_ENGINES" "duckduckgo,tavily"
    fi
  fi

  echo "[info] synced compatible local secrets from the optional source env file without printing values"
}

port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltnH 2>/dev/null | awk '{print $4}' | sed 's/.*://' | grep -qx "$port"
    return $?
  fi

  timeout 1 bash -c "</dev/tcp/127.0.0.1/$port" >/dev/null 2>&1
}

docker_port_bindings() {
  command -v docker >/dev/null 2>&1 || return 1
 
  local ids=()
  mapfile -t ids < <(docker ps -aq 2>/dev/null)
  [[ ${#ids[@]} -gt 0 ]] || return 1
 
  docker inspect \
    -f '{{ $name := .Name }}{{ range $containerPort, $bindings := .HostConfig.PortBindings }}{{ range $bindings }}{{ printf "%s %s\n" $name .HostPort }}{{ end }}{{ end }}' \
    "${ids[@]}" 2>/dev/null
}

docker_port_reserved() {
  local port="$1"
  docker_port_bindings | awk -v port="$port" '$2 == port { found = 1 } END { exit(found ? 0 : 1) }'
}

port_owned_by_soulsearcher() {
  local port="$1"
  docker_port_bindings \
    | awk -v port="$port" '
        $2 == port {
          found = 1
          if ($1 !~ /^\/soulsearcher_/) {
            non_soulsearcher = 1
          }
        }
        END { exit(found && !non_soulsearcher ? 0 : 1) }
      '
}

port_available_or_soulsearcher() {
  local port="$1"
  if ! port_in_use "$port" && ! docker_port_reserved "$port"; then
    return 0
  fi
  port_owned_by_soulsearcher "$port"
}

find_available_port() {
  local port="$1"
  while ! port_available_or_soulsearcher "$port"; do
    port=$((port + 1))
  done
  echo "$port"
}

load_compose_env() {
  if [[ -f "$COMPOSE_ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$COMPOSE_ENV_FILE"
    set +a
  fi
}

write_compose_env() {
  local frontend_port backend_port postgres_port redis_port

  if [[ -f "$COMPOSE_ENV_FILE" ]]; then
    load_compose_env
  fi

  frontend_port="${FRONTEND_PORT:-}"
  backend_port="${BACKEND_PORT:-}"
  postgres_port="${POSTGRES_PORT:-}"
  redis_port="${REDIS_PORT:-}"

  if [[ -z "$frontend_port" ]] || ! port_available_or_soulsearcher "$frontend_port"; then
    frontend_port="$(find_available_port 3100)"
  fi
  if [[ -z "$backend_port" ]] || ! port_available_or_soulsearcher "$backend_port"; then
    backend_port="$(find_available_port 8001)"
  fi
  if [[ -z "$postgres_port" ]] || ! port_available_or_soulsearcher "$postgres_port"; then
    postgres_port="$(find_available_port 5432)"
  fi
  if [[ -z "$redis_port" ]] || ! port_available_or_soulsearcher "$redis_port"; then
    redis_port="$(find_available_port 6379)"
  fi

  cat > "$COMPOSE_ENV_FILE" <<EOF
FRONTEND_PORT=$frontend_port
BACKEND_PORT=$backend_port
POSTGRES_PORT=$postgres_port
REDIS_PORT=$redis_port
EOF

  export FRONTEND_PORT="$frontend_port"
  export BACKEND_PORT="$backend_port"
  export POSTGRES_PORT="$postgres_port"
  export REDIS_PORT="$redis_port"
}

sync_runtime_urls() {
  upsert_env "$ENV_FILE" "PORT" "$BACKEND_PORT"
  upsert_env "$ENV_FILE" "SOULSEARCHER_BASE_URL" "http://127.0.0.1:$BACKEND_PORT"
  upsert_env "$ENV_FILE" "CORS_ORIGINS" "http://localhost:$FRONTEND_PORT,http://127.0.0.1:$FRONTEND_PORT"
  upsert_env "$WEB_ENV_FILE" "NEXT_PUBLIC_API_URL" "http://127.0.0.1:$BACKEND_PORT"
  upsert_env "$WEB_ENV_FILE" "NEXT_PUBLIC_CHAT_STREAM_PROTOCOL" "sse"
  upsert_env "$WEB_ENV_FILE" "NEXT_PUBLIC_RESEARCH_STREAM_PROTOCOL" "sse"
}

wait_http() {
  local name="$1"
  local url="$2"
  local max_retry="${3:-120}"
  local i code

  for ((i=1; i<=max_retry; i++)); do
    code="$(curl --noproxy '*' -s -o /dev/null -m 5 -w '%{http_code}' "$url" 2>/dev/null || true)"
    if [[ "$code" =~ ^(200|204|301|302|307|308)$ ]]; then
      echo "[ok] $name ready: $url"
      return 0
    fi
    sleep 1
  done

  echo "[error] $name health check failed: $url"
  return 1
}

wait_container_healthy() {
  local service="$1"
  local container="soulsearcher_$service"
  local max_retry="${2:-90}"
  local i status

  for ((i=1; i<=max_retry; i++)); do
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)"
    if [[ "$status" == "healthy" || "$status" == "running" ]]; then
      echo "[ok] $service ready"
      return 0
    fi
    sleep 1
  done

  echo "[error] $service did not become healthy"
  compose logs --tail=60 "$service" || true
  return 1
}

start_services() {
  if (( DO_BUILD )); then
    log_step "building Docker images..."
    docker compose --progress plain --env-file "$ENV_FILE" --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" build
  fi

  log_step "starting Docker services..."
  compose up -d --remove-orphans
}

parse_args "$@"

if (( DO_BUILD )); then
  TOTAL_STEPS=$((TOTAL_STEPS + 1))
fi

mkdir -p "$RUN_DIR" "$DATA_DIR/data" "$LOG_DIR"

cd "$PROJECT_ROOT"

log_step "checking host prerequisites..."
check_prerequisites

log_step "preparing local env/config files..."
ensure_env_files
sync_insightvault_env

log_step "selecting available host ports..."
write_compose_env
sync_runtime_urls
echo "[info] ports: frontend=$FRONTEND_PORT backend=$BACKEND_PORT postgres=$POSTGRES_PORT redis=$REDIS_PORT"

prepare_docker_config
start_services

log_step "waiting for data services..."
wait_container_healthy "postgres" 120
wait_container_healthy "redis" 120

log_step "waiting for backend and frontend..."
wait_http "backend" "http://127.0.0.1:$BACKEND_PORT/health" 180
wait_http "frontend" "http://127.0.0.1:$FRONTEND_PORT/" 300

echo ""
echo "soulsearcher started successfully"
echo "- frontend : http://127.0.0.1:$FRONTEND_PORT"
echo "- backend  : http://127.0.0.1:$BACKEND_PORT"
echo "- OpenAPI  : http://127.0.0.1:$BACKEND_PORT/docs"
echo "- Metrics  : http://127.0.0.1:$BACKEND_PORT/metrics"
echo "- logs     : docker compose --env-file .env --env-file .run/compose.env -f docker/docker-compose.yml logs -f"
echo "- stop app : ./stop_soulsearcher.sh"
echo "- stop all : ./stop_soulsearcher.sh --all"
