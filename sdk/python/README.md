# SoulSearcher Internal SDK (Python)

Internal-only Python client for SoulSearcher (no PyPI publishing).

The client targets the current research API: `research_sse()`, research cancellation,
sessions, evidence, run events, and export. There is no separate chat endpoint.

## Install (editable)

From repo root:

```bash
pip install -e ./sdk/python
```

## Example

```python
from soulsearcher_sdk import SoulSearcherClient

client = SoulSearcherClient(base_url="http://127.0.0.1:8001")  # or env SOULSEARCHER_BASE_URL
events = client.research_sse({"query": "Give me a 3-bullet summary of SoulSearcher."})
for ev in events:
    print(ev["type"], ev.get("data"))
```
