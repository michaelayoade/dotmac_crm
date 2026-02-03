"""ERPNext/Frappe API client.

Provides HTTP client for ERPNext REST API with:
- API key + secret authentication
- Automatic pagination for list endpoints
- Error handling and retry logic
- Rate limiting respect

Usage:
    client = ERPNextClient(
        base_url="https://erp.example.com",
        api_key="your-api-key",
        api_secret="your-api-secret",
    )

    # Get all customers
    for customer in client.get_all("Customer"):
        print(customer["name"])

    # Get single document
    ticket = client.get_doc("HD Ticket", "HD-TICKET-00001")
"""

from __future__ import annotations

import time
from typing import Any, Iterator
from urllib.parse import urlencode

import httpx

from app.logging import get_logger

logger = get_logger(__name__)


class ERPNextError(Exception):
    """Base exception for ERPNext API errors."""

    def __init__(self, message: str, status_code: int | None = None, response: dict | None = None):
        self.message = message
        self.status_code = status_code
        self.response = response
        super().__init__(message)


class ERPNextAuthError(ERPNextError):
    """Authentication failed."""
    pass


class ERPNextNotFoundError(ERPNextError):
    """Document not found."""
    pass


class ERPNextRateLimitError(ERPNextError):
    """Rate limit exceeded."""
    pass


class ERPNextClient:
    """HTTP client for ERPNext/Frappe REST API.

    Attributes:
        base_url: ERPNext instance URL (e.g., https://erp.example.com)
        api_key: API key for authentication
        api_secret: API secret for authentication
        timeout: Request timeout in seconds
        page_size: Number of records per page for list requests
    """

    DEFAULT_TIMEOUT = 30.0
    DEFAULT_PAGE_SIZE = 100
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        timeout: float = DEFAULT_TIMEOUT,
        page_size: int = DEFAULT_PAGE_SIZE,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self.page_size = page_size

        # Build auth header (token format: api_key:api_secret)
        self._auth_header = f"token {api_key}:{api_secret}"

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with authentication."""
        return {
            "Authorization": self._auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json: dict | None = None,
        retries: int = 0,
    ) -> dict[str, Any]:
        """Make HTTP request to ERPNext API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API path (e.g., /api/resource/Customer)
            params: Query parameters
            json: JSON body for POST/PUT
            retries: Current retry count

        Returns:
            Parsed JSON response

        Raises:
            ERPNextError: On API errors
        """
        url = f"{self.base_url}{path}"

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(
                    method,
                    url,
                    headers=self._get_headers(),
                    params=params,
                    json=json,
                )

                # Handle rate limiting
                if response.status_code == 429:
                    if retries < self.MAX_RETRIES:
                        retry_after = float(response.headers.get("Retry-After", self.RETRY_DELAY))
                        logger.warning(
                            "erpnext_rate_limited retry_after=%s retries=%s",
                            retry_after,
                            retries,
                        )
                        time.sleep(retry_after)
                        return self._request(method, path, params, json, retries + 1)
                    raise ERPNextRateLimitError(
                        "Rate limit exceeded",
                        status_code=429,
                    )

                # Handle auth errors
                if response.status_code == 401:
                    raise ERPNextAuthError(
                        "Authentication failed - check API key and secret",
                        status_code=401,
                    )

                if response.status_code == 403:
                    raise ERPNextAuthError(
                        "Access denied - check API permissions",
                        status_code=403,
                    )

                # Handle not found
                if response.status_code == 404:
                    raise ERPNextNotFoundError(
                        f"Document not found: {path}",
                        status_code=404,
                    )

                # Handle other errors
                if response.status_code >= 400:
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("message", response.text)
                    except Exception:
                        error_msg = response.text

                    raise ERPNextError(
                        f"API error: {error_msg}",
                        status_code=response.status_code,
                        response=error_data if "error_data" in locals() else None,
                    )

                return response.json()

        except httpx.TimeoutException as e:
            if retries < self.MAX_RETRIES:
                logger.warning("erpnext_timeout retries=%s", retries)
                time.sleep(self.RETRY_DELAY)
                return self._request(method, path, params, json, retries + 1)
            raise ERPNextError(f"Request timeout: {e}") from e

        except httpx.RequestError as e:
            raise ERPNextError(f"Request failed: {e}") from e

    def get_doc(self, doctype: str, name: str, fields: list[str] | None = None) -> dict[str, Any]:
        """Get a single document by name.

        Args:
            doctype: ERPNext doctype (e.g., "Customer", "HD Ticket")
            name: Document name/ID
            fields: Optional list of fields to return

        Returns:
            Document data dict
        """
        path = f"/api/resource/{doctype}/{name}"
        params = {}
        if fields:
            params["fields"] = '["' + '","'.join(fields) + '"]'

        result = self._request("GET", path, params=params)
        return result.get("data", result)

    def get_list(
        self,
        doctype: str,
        fields: list[str] | None = None,
        filters: dict | list | None = None,
        order_by: str | None = None,
        limit_start: int = 0,
        limit_page_length: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get a list of documents with pagination.

        Args:
            doctype: ERPNext doctype
            fields: Fields to return (default: ["name"])
            filters: Filter conditions
            order_by: Sort order (e.g., "creation desc")
            limit_start: Offset for pagination
            limit_page_length: Number of records to return

        Returns:
            List of document dicts
        """
        path = f"/api/resource/{doctype}"
        params: dict[str, Any] = {
            "limit_start": limit_start,
            "limit_page_length": limit_page_length or self.page_size,
        }

        if fields:
            params["fields"] = '["' + '","'.join(fields) + '"]'

        if filters:
            if isinstance(filters, dict):
                # Convert dict to Frappe filter format
                params["filters"] = str(filters).replace("'", '"')
            else:
                params["filters"] = str(filters).replace("'", '"')

        if order_by:
            params["order_by"] = order_by

        result = self._request("GET", path, params=params)
        return result.get("data", [])

    def get_all(
        self,
        doctype: str,
        fields: list[str] | None = None,
        filters: dict | list | None = None,
        order_by: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Get all documents with automatic pagination.

        Yields documents one at a time, handling pagination automatically.

        Args:
            doctype: ERPNext doctype
            fields: Fields to return
            filters: Filter conditions
            order_by: Sort order

        Yields:
            Document dicts
        """
        offset = 0
        while True:
            batch = self.get_list(
                doctype=doctype,
                fields=fields,
                filters=filters,
                order_by=order_by,
                limit_start=offset,
                limit_page_length=self.page_size,
            )

            if not batch:
                break

            for doc in batch:
                yield doc

            if len(batch) < self.page_size:
                break

            offset += self.page_size
            logger.debug("erpnext_pagination doctype=%s offset=%s", doctype, offset)

    def get_count(self, doctype: str, filters: dict | list | None = None) -> int:
        """Get count of documents matching filters.

        Args:
            doctype: ERPNext doctype
            filters: Filter conditions

        Returns:
            Count of matching documents
        """
        path = f"/api/resource/{doctype}"
        params: dict[str, Any] = {"limit_page_length": 0}

        if filters:
            if isinstance(filters, dict):
                params["filters"] = str(filters).replace("'", '"')
            else:
                params["filters"] = str(filters).replace("'", '"')

        # Use HEAD request or get with limit 0 to get count
        result = self._request("GET", path, params=params)
        # ERPNext returns total in the response
        return len(result.get("data", []))

    def run_method(
        self,
        doctype: str,
        name: str,
        method: str,
        args: dict | None = None,
    ) -> dict[str, Any]:
        """Run a whitelisted method on a document.

        Args:
            doctype: ERPNext doctype
            name: Document name
            method: Method name to call
            args: Method arguments

        Returns:
            Method response
        """
        path = f"/api/resource/{doctype}/{name}"
        params = {"run_method": method}

        result = self._request("POST", path, params=params, json=args or {})
        return result

    def call_method(self, method: str, args: dict | None = None) -> dict[str, Any]:
        """Call a whitelisted API method.

        Args:
            method: Full method path (e.g., "frappe.client.get_count")
            args: Method arguments

        Returns:
            Method response
        """
        path = f"/api/method/{method}"
        result = self._request("POST", path, json=args or {})
        return result.get("message", result)

    def test_connection(self) -> bool:
        """Test API connection and authentication.

        Returns:
            True if connection successful

        Raises:
            ERPNextError: If connection fails
        """
        try:
            # Try to get logged in user
            result = self.call_method("frappe.auth.get_logged_user")
            logger.info("erpnext_connected user=%s", result)
            return True
        except ERPNextError:
            raise
