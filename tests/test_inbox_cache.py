"""Tests for CRM inbox cache helpers."""

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
