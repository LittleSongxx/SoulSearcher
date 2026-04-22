import sys
import types
from pathlib import Path

# Ensure project root is on sys.path for direct test execution
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.search import search
from tools.search.tavily_key_pool import TavilyKeyPool


def test_tavily_search_only_summarizes_top_result(monkeypatch):
    class _DummyClient:
        def __init__(self, api_key):
            self.api_key = api_key

        def search(self, **kwargs):
            return {
                "results": [
                    {
                        "title": "Result 1",
                        "url": "https://example.com/1",
                        "content": "Snippet 1",
                        "raw_content": "Raw content 1",
                        "score": 0.9,
                    },
                    {
                        "title": "Result 2",
                        "url": "https://example.com/2",
                        "content": "Snippet 2",
                        "raw_content": "Raw content 2",
                        "score": 0.8,
                    },
                    {
                        "title": "Result 3",
                        "url": "https://example.com/3",
                        "content": "Snippet 3",
                        "raw_content": "Raw content 3",
                        "score": 0.7,
                    },
                ]
            }

    summarize_calls = []

    monkeypatch.setitem(
        sys.modules, "tavily", types.SimpleNamespace(TavilyClient=_DummyClient)
    )
    # Patch key pool to return a test key
    test_pool = TavilyKeyPool(["test-key"])
    monkeypatch.setattr(search, "get_tavily_key_pool", lambda: test_pool)
    monkeypatch.setattr(
        search,
        "_summarize_content",
        lambda raw: summarize_calls.append(raw) or f"summary:{raw}",
    )

    results = search.tavily_search.invoke(
        {"query": "capital of France", "max_results": 3}
    )

    assert summarize_calls == ["Raw content 1"]
    assert results[0]["summary"] == "summary:Raw content 1"
    assert results[1]["summary"] == "Snippet 2"
    assert results[2]["summary"] == "Snippet 3"


# ── TavilyKeyPool unit tests ──────────────────────────────────────────────


def test_key_pool_single_key():
    pool = TavilyKeyPool(["key-a"])
    assert pool.available_count == 1
    assert pool.get_key() == "key-a"


def test_key_pool_rotation():
    pool = TavilyKeyPool(["key-a", "key-b", "key-c"])
    assert pool.available_count == 3
    assert pool.get_key() == "key-a"

    # Exhaust first key -> rotates to second
    next_key = pool.mark_exhausted("key-a")
    assert next_key == "key-b"
    assert pool.available_count == 2
    assert pool.get_key() == "key-b"

    # Exhaust second key -> rotates to third
    next_key = pool.mark_exhausted("key-b")
    assert next_key == "key-c"
    assert pool.available_count == 1
    assert pool.get_key() == "key-c"


def test_key_pool_all_exhausted():
    pool = TavilyKeyPool(["key-a", "key-b"])
    pool.mark_exhausted("key-a")
    next_key = pool.mark_exhausted("key-b")
    assert next_key is None
    assert pool.available_count == 0
    assert pool.get_key() is None


def test_key_pool_reset():
    pool = TavilyKeyPool(["key-a", "key-b"])
    pool.mark_exhausted("key-a")
    pool.mark_exhausted("key-b")
    assert pool.available_count == 0

    pool.reset()
    assert pool.available_count == 2
    assert pool.get_key() == "key-a"


def test_key_pool_empty():
    pool = TavilyKeyPool([])
    assert pool.available_count == 0
    assert pool.get_key() is None


def test_key_pool_is_quota_error():
    assert TavilyKeyPool.is_quota_error(Exception("Rate limit exceeded"))
    assert TavilyKeyPool.is_quota_error(Exception("HTTP 429: Too Many Requests"))
    assert TavilyKeyPool.is_quota_error(Exception("Quota exceeded for this API key"))
    assert TavilyKeyPool.is_quota_error(Exception("Insufficient credits"))
    assert not TavilyKeyPool.is_quota_error(Exception("Connection timeout"))
    assert not TavilyKeyPool.is_quota_error(Exception("Invalid API key"))
