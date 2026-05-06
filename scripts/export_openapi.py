from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def build_openapi_spec() -> dict[str, Any]:
    from main import app

    return app.openapi()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export Weaver OpenAPI spec as JSON.")
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="",
        help="Write JSON to this path (defaults to stdout).",
    )
    args = parser.parse_args(argv)

    spec = build_openapi_spec()

    output_path = (args.output or "").strip()
    if output_path:
        payload = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True)
        Path(output_path).write_text(payload, encoding="utf-8")
    else:
        # Keep stdout ASCII-only so it works under Windows locale encodings
        # (e.g. cp936/gbk) and in subprocess capture_output(text=True).
        payload = json.dumps(spec, ensure_ascii=True, indent=2, sort_keys=True)
        sys.stdout.write(payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
