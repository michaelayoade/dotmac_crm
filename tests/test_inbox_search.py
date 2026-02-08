"""Tests for inbox search normalization."""

from app.services.crm.inbox.search import normalize_search


def test_normalize_search():
    assert normalize_search(None) is None
    assert normalize_search("") is None
    assert normalize_search("   ") is None
    assert normalize_search("  Test  Search ") == "test search"
