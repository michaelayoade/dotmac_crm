"""Response handling for the DotMac ERP HTTP client."""

import httpx
import pytest

from app.services.dotmac_erp.client import (
    DotMacERPAuthError,
    DotMacERPClient,
    DotMacERPError,
)


@pytest.fixture
def client():
    return DotMacERPClient(base_url="https://erp.test", token="test-token")


def _response(status_code: int, json_body: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=json_body,
        request=httpx.Request("POST", "https://erp.test/api/v1/sync/crm/purchase-orders"),
    )


class TestHandleResponse:
    def test_auth_error_wins_over_expected_status_codes(self, client):
        """A 401/403 must raise DotMacERPAuthError even when the caller narrows
        expected_status_codes — task handlers classify errors on that type."""
        with pytest.raises(DotMacERPAuthError):
            client._handle_response(_response(401, {"detail": "bad key"}), expected_status_codes={200, 201})
        with pytest.raises(DotMacERPAuthError):
            client._handle_response(_response(403, {"detail": "forbidden"}), expected_status_codes={200, 201})

    def test_unexpected_success_status_raises(self, client):
        with pytest.raises(DotMacERPError) as exc:
            client._handle_response(_response(204), expected_status_codes={200, 201})
        assert exc.value.status_code == 204

    def test_expected_status_returns_payload(self, client):
        data = client._handle_response(_response(201, {"purchase_order_id": "PO-1"}), expected_status_codes={200, 201})
        assert data == {"purchase_order_id": "PO-1"}

    def test_204_without_expectation_returns_none(self, client):
        assert client._handle_response(_response(204)) is None
