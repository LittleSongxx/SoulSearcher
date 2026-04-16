#!/usr/bin/env sh
set -eu

APP_DIR="/app"
LOCKFILE="$APP_DIR/pnpm-lock.yaml"
NODE_MODULES_DIR="$APP_DIR/node_modules"
STAMP_FILE="$NODE_MODULES_DIR/.pnpm-lock.sha256"
FRONTEND_PORT="${FRONTEND_PORT:-3100}"

current_hash=""
installed_hash=""

if [ -f "$LOCKFILE" ]; then
  current_hash="$(sha256sum "$LOCKFILE" | awk '{print $1}')"
fi

if [ -f "$STAMP_FILE" ]; then
  installed_hash="$(cat "$STAMP_FILE" 2>/dev/null || true)"
fi

if [ ! -x "$NODE_MODULES_DIR/.bin/next" ] || [ "$current_hash" != "$installed_hash" ]; then
  echo "[frontend] syncing dependencies with pnpm..."
  cd "$APP_DIR"
  pnpm install --frozen-lockfile
  mkdir -p "$NODE_MODULES_DIR"
  echo "$current_hash" > "$STAMP_FILE"
fi

cd "$APP_DIR"
exec pnpm exec next dev -H 0.0.0.0 -p "$FRONTEND_PORT"
