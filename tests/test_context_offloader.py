"""Tests for context offloading engine."""

import json
import os
import tempfile

import pytest
from unittest.mock import patch

from agent.core.context_offloader import (
    cleanup_offload_dir,
    load_all_offloaded,
    load_offloaded_content,
    offload_content,
    offload_content_list,
)


class TestContextOffloader:
    """Test the context offloading engine."""

    def test_offload_disabled(self):
        """When offloading is disabled, items pass through unchanged."""
        item = {"url": "http://x.com", "content": "x" * 5000}
        with patch("agent.core.context_offloader.settings") as mock_s:
            mock_s.context_offloading = False
            result = offload_content(item)
        assert result is item

    def test_below_threshold_passthrough(self):
        """Items below the threshold are not offloaded."""
        item = {"url": "http://x.com", "content": "short"}
        with patch("agent.core.context_offloader.settings") as mock_s:
            mock_s.context_offloading = True
            mock_s.context_offloading_threshold = 2000
            mock_s.context_offloading_dir = ""
            result = offload_content(item)
        assert result is item

    def test_offload_and_load(self):
        """Large content is offloaded and can be loaded back."""
        with tempfile.TemporaryDirectory() as tmpdir:
            item = {
                "url": "http://example.com",
                "title": "Test",
                "content": "x" * 3000,
                "query": "test query",
            }
            with patch("agent.core.context_offloader.settings") as mock_s:
                mock_s.context_offloading = True
                mock_s.context_offloading_threshold = 100
                mock_s.context_offloading_dir = tmpdir
                compact = offload_content(item, thread_id="t1")

            assert compact.get("offloaded") is True
            assert compact.get("url") == "http://example.com"
            assert compact.get("title") == "Test"
            assert compact.get("query") == "test query"
            assert "offloaded_path" in compact
            assert len(compact.get("snippet", "")) <= 200

            # Load back
            full = load_offloaded_content(compact)
            assert full.get("content") == "x" * 3000

    def test_offload_content_list(self):
        """List offloading works for mixed items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            items = [
                {"content": "short"},
                {"content": "y" * 3000, "url": "http://big.com"},
            ]
            with patch("agent.core.context_offloader.settings") as mock_s:
                mock_s.context_offloading = True
                mock_s.context_offloading_threshold = 100
                mock_s.context_offloading_dir = tmpdir
                result = offload_content_list(items, "t2")

            assert result[0] is items[0]  # small, not offloaded
            assert result[1].get("offloaded") is True

    def test_load_non_offloaded_passthrough(self):
        """Loading a non-offloaded item returns it as-is."""
        item = {"url": "http://x.com", "content": "data"}
        result = load_offloaded_content(item)
        assert result is item

    def test_load_all_offloaded(self):
        """load_all_offloaded correctly loads offloaded items and passes others."""
        with tempfile.TemporaryDirectory() as tmpdir:
            item = {"content": "z" * 3000, "url": "http://z.com"}
            with patch("agent.core.context_offloader.settings") as mock_s:
                mock_s.context_offloading = True
                mock_s.context_offloading_threshold = 100
                mock_s.context_offloading_dir = tmpdir
                compact = offload_content(item, "t3")

            normal = {"content": "normal"}
            result = load_all_offloaded([compact, normal])
            assert result[0].get("content") == "z" * 3000
            assert result[1] is normal

    def test_cleanup(self):
        """cleanup_offload_dir removes the directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            item = {"content": "a" * 3000}
            with patch("agent.core.context_offloader.settings") as mock_s:
                mock_s.context_offloading = True
                mock_s.context_offloading_threshold = 100
                mock_s.context_offloading_dir = tmpdir
                offload_content(item, "t4")

            thread_dir = os.path.join(tmpdir, "t4")
            assert os.path.exists(thread_dir)

            with patch("agent.core.context_offloader.settings") as mock_s:
                mock_s.context_offloading_dir = tmpdir
                cleanup_offload_dir("t4")

            assert not os.path.exists(thread_dir)

    def test_already_offloaded_passthrough(self):
        """Items that already have offloaded_path are not re-offloaded."""
        item = {"offloaded_path": "/some/path", "content": "x" * 5000}
        with patch("agent.core.context_offloader.settings") as mock_s:
            mock_s.context_offloading = True
            mock_s.context_offloading_threshold = 100
            result = offload_content(item)
        assert result is item
