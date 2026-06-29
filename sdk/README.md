# Weaver Internal SDKs

Internal SDKs for calling Weaver from scripts and services. They are kept in this repository for internal consumption and are not published to npm or PyPI.

## Coverage

- Research streaming through `POST /api/research/sse`
- Research cancellation through `/api/research/cancel/*`
- Sessions and evidence through `/api/sessions/*`
- Run events through `/api/runs/{thread_id}/events` and `/api/runs/{thread_id}/events/sse`
- Report export through `/api/export/*`

There is no separate chat API in the current backend; SDK examples use the research endpoint.

## Environment

SDK examples read `WEAVER_BASE_URL`, defaulting to `http://127.0.0.1:8001`.

## TypeScript SDK

Path: `sdk/typescript/`

```bash
WEAVER_BASE_URL=http://127.0.0.1:8001 node sdk/typescript/examples/research.mjs
```

Build the committed `dist/` output:

```bash
bash sdk/typescript/scripts/build.sh
```

## Python SDK

Path: `sdk/python/`

```bash
pip install -e ./sdk/python
WEAVER_BASE_URL=http://127.0.0.1:8001 python sdk/python/examples/research.py
```

## When Backend APIs Change

Regenerate the OpenAPI TypeScript outputs and rebuild the TypeScript SDK:

```bash
DEBUG=false APP_ENV=prod ENABLE_FILE_LOGGING=false \
  /home/song/anaconda3/bin/conda run -n weaver python scripts/export_openapi.py --output /tmp/weaver-openapi.json
npx --yes -p node@24 -p openapi-typescript openapi-typescript /tmp/weaver-openapi.json -o web/lib/api-types.ts
npx --yes -p node@24 -p openapi-typescript openapi-typescript /tmp/weaver-openapi.json -o sdk/typescript/src/openapi-types.ts
bash sdk/typescript/scripts/build.sh
```
