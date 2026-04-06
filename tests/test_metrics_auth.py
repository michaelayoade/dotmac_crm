from app.monitoring import is_bearer_token_authorized


def test_metrics_auth_requires_matching_bearer_token():
    assert not is_bearer_token_authorized(None, "secret-token")
    assert not is_bearer_token_authorized("Bearer wrong", "secret-token")
    assert is_bearer_token_authorized("Bearer secret-token", "secret-token")


def test_metrics_auth_allows_public_access_when_token_empty():
    assert is_bearer_token_authorized(None, "")
