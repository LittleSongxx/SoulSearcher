# SoulSearcher Internal SDKs

This directory contains repository-local SDKs for calling SoulSearcher from scripts and services. The SDKs are private to this repository and are not published to external package registries.

## Supported API Surface

| Area | Methods / Endpoints |
| --- | --- |
| Research stream | `POST /api/research/sse` |
| Research cancellation | `/api/research/cancel/{thread_id}`, `/api/research/cancel-all` |
| Sessions | `/api/sessions`, `/api/sessions/{thread_id}` |
| Evidence | `/api/sessions/{thread_id}/evidence` |
| Run events | `/api/runs/{thread_id}/events`, `/api/runs/{thread_id}/events/sse` |
| Export | `/api/export/templates`, `/api/export/{thread_id}` |

There is no separate chat endpoint in the current backend. SDK examples use the research endpoint.

## Environment

SDK examples read `SOULSEARCHER_BASE_URL`, defaulting to `http://127.0.0.1:8001`.

## TypeScript SDK

Path: `sdk/typescript/`

```ts
import { SoulSearcherClient } from './sdk/typescript/dist/index.js'

const client = new SoulSearcherClient({
  baseUrl: process.env.SOULSEARCHER_BASE_URL || 'http://127.0.0.1:8001',
})

for await (const event of client.researchSse({ query: 'Summarize the current research plan.' })) {
  console.log(event.type, event.data)
}
```

Run the example:

```bash
SOULSEARCHER_BASE_URL=http://127.0.0.1:8001 node sdk/typescript/examples/research.mjs
```

Build the committed `dist/` output:

```bash
bash sdk/typescript/scripts/build.sh
```

## Python SDK

Path: `sdk/python/`

```python
from soulsearcher_sdk import SoulSearcherClient

client = SoulSearcherClient(base_url="http://127.0.0.1:8001")

for event in client.research_sse({"query": "Summarize the current research plan."}):
    print(event["type"], event.get("data"))
```

Install and run the example:

```bash
pip install -e ./sdk/python
SOULSEARCHER_BASE_URL=http://127.0.0.1:8001 python sdk/python/examples/research.py
```

## Regenerating Types

When backend schemas change, regenerate OpenAPI outputs and rebuild the TypeScript SDK:

```bash
DEBUG=false APP_ENV=prod ENABLE_FILE_LOGGING=false \
  python scripts/export_openapi.py --output /tmp/soulsearcher-openapi.json
npx --yes -p node@24 -p openapi-typescript openapi-typescript /tmp/soulsearcher-openapi.json -o web/lib/api-types.ts
npx --yes -p node@24 -p openapi-typescript openapi-typescript /tmp/soulsearcher-openapi.json -o sdk/typescript/src/openapi-types.ts
bash sdk/typescript/scripts/build.sh
```
