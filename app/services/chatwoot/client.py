"""Chatwoot API client."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ChatwootError(Exception):
    """Chatwoot API error."""

    def __init__(self, message: str, status_code: int | None = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class ChatwootClient:
    """Client for Chatwoot API v1."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        account_id: int = 1,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.account_id = account_id
        self.timeout = timeout
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                headers={
                    "api_access_token": self.access_token,
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        return self._client

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make API request."""
        client = self._get_client()
        url = f"/api/v1/accounts/{self.account_id}{path}"

        try:
            response = client.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Chatwoot API error: {e.response.status_code} - {e.response.text}")
            raise ChatwootError(
                f"API error: {e.response.status_code}",
                status_code=e.response.status_code,
            )
        except httpx.RequestError as e:
            logger.error(f"Chatwoot request error: {e}")
            raise ChatwootError(f"Request failed: {e}")

    def test_connection(self) -> bool:
        """Test API connection by fetching account info."""
        try:
            # Try to get agents list as a connection test
            self._request("GET", "/agents")
            return True
        except ChatwootError:
            return False

    # ==================== Contacts ====================

    def list_contacts(self, page: int = 1, per_page: int = 15) -> dict[str, Any]:
        """List contacts with pagination."""
        return self._request(
            "GET",
            "/contacts",
            params={"page": page, "per_page": per_page},
        )

    def get_contact(self, contact_id: int) -> dict[str, Any]:
        """Get a single contact."""
        return self._request("GET", f"/contacts/{contact_id}")

    def get_all_contacts(self, per_page: int = 100) -> list[dict[str, Any]]:
        """Get all contacts with pagination."""
        all_contacts = []
        page = 1

        while True:
            result = self.list_contacts(page=page, per_page=per_page)
            payload = result.get("payload", [])
            if not payload:
                break
            all_contacts.extend(payload)

            # Check if there are more pages
            meta = result.get("meta", {})
            total_count = meta.get("count", 0)
            if len(all_contacts) >= total_count:
                break
            page += 1

        logger.info(f"Fetched {len(all_contacts)} contacts from Chatwoot")
        return all_contacts

    # ==================== Conversations ====================

    def list_conversations(
        self,
        status: str = "all",
        page: int = 1,
        per_page: int = 25,
    ) -> dict[str, Any]:
        """List conversations with pagination."""
        return self._request(
            "GET",
            "/conversations",
            params={
                "status": status,
                "page": page,
                "per_page": per_page,
            },
        )

    def get_conversation(self, conversation_id: int) -> dict[str, Any]:
        """Get a single conversation."""
        return self._request("GET", f"/conversations/{conversation_id}")

    def get_conversation_messages(
        self,
        conversation_id: int,
        before: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get messages for a conversation."""
        params = {}
        if before:
            params["before"] = before
        result = self._request(
            "GET",
            f"/conversations/{conversation_id}/messages",
            params=params if params else None,
        )
        return result.get("payload", [])

    def get_all_conversations(
        self,
        status: str = "all",
        per_page: int = 100,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get all conversations with pagination.

        Args:
            status: Filter by status (all, open, resolved, pending, snoozed)
            per_page: Records per page
            max_records: Maximum records to fetch (None for all)
        """
        all_conversations = []
        page = 1

        while True:
            result = self.list_conversations(
                status=status,
                page=page,
                per_page=per_page,
            )
            data = result.get("data", {})
            payload = data.get("payload", [])
            if not payload:
                break
            all_conversations.extend(payload)

            # Check limits
            meta = data.get("meta", {})
            total_count = meta.get("all_count", 0)

            if max_records and len(all_conversations) >= max_records:
                all_conversations = all_conversations[:max_records]
                break
            if len(all_conversations) >= total_count or not payload:
                break
            page += 1

        logger.info(f"Fetched {len(all_conversations)} conversations from Chatwoot")
        return all_conversations

    # ==================== Agents ====================

    def list_agents(self) -> list[dict[str, Any]]:
        """List all agents."""
        result = self._request("GET", "/agents")
        return result if isinstance(result, list) else result.get("payload", [])

    # ==================== Teams ====================

    def list_teams(self) -> list[dict[str, Any]]:
        """List all teams."""
        result = self._request("GET", "/teams")
        return result if isinstance(result, list) else result.get("payload", [])

    # ==================== Inboxes ====================

    def list_inboxes(self) -> list[dict[str, Any]]:
        """List all inboxes."""
        result = self._request("GET", "/inboxes")
        return result.get("payload", []) if isinstance(result, dict) else result

    # ==================== Labels ====================

    def list_labels(self) -> list[dict[str, Any]]:
        """List all labels."""
        result = self._request("GET", "/labels")
        return result.get("payload", []) if isinstance(result, dict) else result
