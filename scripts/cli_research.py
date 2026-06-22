#!/usr/bin/env python
"""CLI helper to call /api/research/sse and stream events.
Usage:
  python scripts/cli_research.py "your question" --host http://localhost:8001
"""

import argparse
import json
import sys

import httpx


def stream_research(query: str, host: str):
    url = f"{host.rstrip('/')}/api/research/sse"
    headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
    with httpx.stream(
        "POST",
        url,
        json={"query": query},
        headers=headers,
        timeout=None,
    ) as r:
        r.raise_for_status()
        event_name = ""
        data_lines: list[str] = []
        for line in r.iter_lines():
            if line == "":
                if not data_lines:
                    event_name = ""
                    continue
                raw_data = "\n".join(data_lines)
                data_lines = []
                try:
                    payload = json.loads(raw_data)
                    print({"type": event_name or payload.get("type"), "data": payload})
                except Exception as e:
                    print(f"[parse error] {e}: {raw_data}", file=sys.stderr)
                event_name = ""
                continue
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="Research query text")
    parser.add_argument("--host", default="http://localhost:8001", help="Backend host")
    args = parser.parse_args()
    stream_research(args.query, args.host)


if __name__ == "__main__":
    main()
