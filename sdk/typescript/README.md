# Weaver Internal SDK (TypeScript)

Internal-only TypeScript client for Weaver (not published).

The client targets the current research API: `researchSse()`, research cancellation,
sessions, evidence, run events, and export. There is no separate chat endpoint.

## Build

This repo keeps a compiled `dist/` so Node can use the SDK directly.

From repo root:

```bash
bash sdk/typescript/scripts/build.sh
```

## Example

```bash
WEAVER_BASE_URL=http://127.0.0.1:8001 node sdk/typescript/examples/research.mjs
```
