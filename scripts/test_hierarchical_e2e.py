#!/usr/bin/env python
"""Quick E2E test for hierarchical coordinator mode."""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Hierarchical mode with minimal resource usage for testing
os.environ["USE_HIERARCHICAL_AGENTS"] = "true"
os.environ["DEEPSEARCH_MAX_EPOCHS"] = "2"
os.environ["MAX_REVISIONS"] = "1"
os.environ["TREE_MAX_DEPTH"] = "1"
os.environ["TREE_MAX_BRANCHES"] = "2"
os.environ["DEEPSEARCH_MAX_SECONDS"] = "240"
os.environ["DEEPSEARCH_RESULTS_PER_QUERY"] = "2"
os.environ["DEEPSEARCH_TREE_MAX_SEARCHES"] = "3"
os.environ["ENABLE_REPORT_CHARTS"] = "false"  # Skip chart gen to save time/tokens

import logging

LOG_FILE = "/tmp/hier_e2e_full.log"
file_handler = logging.FileHandler(LOG_FILE, mode="w")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
)

console_handler = logging.StreamHandler(sys.stderr)
console_handler.setLevel(logging.WARNING)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

from scripts.benchmark_deep_research import _execute_research_case


async def main():
    query = "What is LoRA fine-tuning"
    print(f"\n=== Testing hierarchical mode with: '{query}' ===")
    print(f"=== Logs: {LOG_FILE} ===\n", flush=True)
    t0 = time.time()
    result = await _execute_research_case(
        query,
        mode="auto",
        base_url="asgi",
        model="",
        timeout_s=360,
    )
    elapsed = time.time() - t0
    status = result.get("status", "unknown")
    chars = result.get("final_report_chars", 0)
    print(f"\n{'='*60}")
    print(f"STATUS:  {status}")
    print(f"CHARS:   {chars}")
    print(f"ELAPSED: {elapsed:.0f}s")
    print(f"{'='*60}")
    if chars > 500:
        print("PASS: Report has substantial content")
    elif chars > 0:
        print(f"WARN: Report exists but short ({chars} chars)")
    else:
        print("FAIL: No report generated")
    print(flush=True)


if __name__ == "__main__":
    asyncio.run(main())
