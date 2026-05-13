"""Tests for CRM inbox cache helpers."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app.services.crm.inbox import cache as inbox_cache


def test_cache_set_get_roundtrip():
    inbox_cache.set("test:key", "value", 60)
    assert inbox_cache.get("test:key") == "value"


def test_cache_invalidate_prefix():
    inbox_cache.set("inbox_list:a", 1, 60)
    inbox_cache.set("inbox_list:b", 2, 60)
    inbox_cache.set("other:c", 3, 60)
    inbox_cache.invalidate_inbox_list()
    assert inbox_cache.get("inbox_list:a") is None
    assert inbox_cache.get("inbox_list:b") is None
    assert inbox_cache.get("other:c") == 3


def test_cache_get_or_set_coalesces_concurrent_loaders():
    barrier = Barrier(3)
    calls = {"count": 0}

    def loader():
        calls["count"] += 1
        time.sleep(0.05)
        return {"value": 42}

    def worker():
        barrier.wait()
        return inbox_cache.get_or_set("summary_counts:test", 60, loader)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker) for _ in range(2)]
            barrier.wait()
            results = [future.result() for future in futures]
    finally:
        inbox_cache.invalidate_prefix("summary_counts:test")

    assert results == [{"value": 42}, {"value": 42}]
    assert calls["count"] == 1
