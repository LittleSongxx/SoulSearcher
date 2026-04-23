#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

pnpm -C "$ROOT_DIR/web" exec tsc -p "$ROOT_DIR/sdk/typescript/tsconfig.json"
