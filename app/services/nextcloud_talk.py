"""Nextcloud Talk OCS API client."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0  # fallback when settings unavailable


def get_nextcloud_talk_timeout(db: Session | None = None) -> float:
    """Get the Nextcloud Talk API timeout from settings."""
    timeout = resolve_value(db, SettingDomain.comms, "nextcloud_talk_timeout_seconds") if db else None
    if isinstance(timeout, int | float):
        return float(timeout)
    if isinstance(timeout, str):
        try:
            return float(timeout)
        except ValueError:
            return _DEFAULT_TIMEOUT
    return _DEFAULT_TIMEOUT


class NextcloudTalkError(Exception):
    """Base exception for Nextcloud Talk client errors."""

    pass


class NextcloudTalkClient:
    """HTTP client for Nextcloud Talk OCS API (spreed)."""

    def __init__(
        self,
        base_url: str,
        username: str,
        app_password: str,
        timeout: float | None = None,
        db: Session | None = None,
    ) -> None:
        # Use configurable timeout if not explicitly provided
        if timeout is None:
            timeout = get_nextcloud_talk_timeout(db)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.auth = httpx.BasicAuth(username, app_password)
        self.headers = {"OCS-APIRequest": "true", "Accept": "application/json"}
        # Conversation management endpoints (v4)
        self.ocs_conversations_base_path = "/ocs/v2.php/apps/spreed/api/v4"
        # Chat endpoints (v1)
        self.ocs_chat_base_path = "/ocs/v2.php/apps/spreed/api/v1"

    def _parse_ocs(self, response: httpx.Response) -> Any:
        try:
            payload = response.json()
        except ValueError as exc:
            snippet = (response.text or "")[:500]
            logger.error(
                "Nextcloud Talk returned non-JSON response (status=%s, content_type=%s, snippet=%r)",
                response.status_code,
                response.headers.get("content-type"),
                snippet,
            )
            raise NextcloudTalkError("Invalid JSON response from Nextcloud Talk") from exc

        if not isinstance(payload, dict) or "ocs" not in payload:
            raise NextcloudTalkError("Invalid OCS response structure")

        meta = payload.get("ocs", {}).get("meta", {})
        statuscode = meta.get("statuscode")
        # Nextcloud deployments vary:
        # - Many OCS APIs return statuscode=100 for success
        # - Some Talk endpoints mirror HTTP status codes (200/201/etc)
        ok = False
        if statuscode in (100, "100") or (isinstance(statuscode, int) and 200 <= statuscode < 300):
            ok = True
        elif isinstance(statuscode, str) and statuscode.isdigit():
            try:
                ok = 200 <= int(statuscode) < 300
            except ValueError:
                ok = False

        if not ok:
            message = meta.get("message") or meta.get("status") or "Unknown error"
            logger.error("Nextcloud Talk OCS error statuscode=%s message=%r", statuscode, message)
            raise NextcloudTalkError(f"OCS error {statuscode}: {message}")

        return payload.get("ocs", {}).get("data")

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        data: dict | None = None,
        *,
        base_path: str | None = None,
        send_json: bool = False,
    ) -> Any:
        # Nextcloud OCS APIs often default to XML unless format=json is requested.
        # Accept: application/json is not always sufficient across deployments.
        params = dict(params or {})
        params.setdefault("format", "json")
        url = f"{self.base_url}{(base_path or self.ocs_conversations_base_path)}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(
                    method,
                    url,
                    params=params,
                    json=data if (send_json and data is not None) else None,
                    data=None if send_json else data,
                    headers=self.headers,
                    auth=self.auth,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Nextcloud Talk HTTP error: %s - %s",
                exc.response.status_code,
                exc.response.text,
            )
            raise NextcloudTalkError(f"HTTP error: {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            logger.error("Nextcloud Talk request error: %s", exc)
            raise NextcloudTalkError(f"Request error: {exc}") from exc

        return self._parse_ocs(response)

    def list_rooms(self) -> list[dict]:
        data = self._request("GET", "/room", base_path=self.ocs_conversations_base_path)
        if data is None:
            return []
        if isinstance(data, list):
            return data
        return [data]

    def create_room(
        self,
        room_name: str,
        room_type: str | int = "public",
        options: dict | None = None,
    ) -> dict:
        payload = {"roomName": room_name, "roomType": room_type}
        if options:
            payload.update(options)
        # v4 room endpoints commonly expect JSON (matches curl examples).
        data = self._request(
            "POST",
            "/room",
            data=payload,
            base_path=self.ocs_conversations_base_path,
            send_json=True,
        )
        if isinstance(data, dict):
            return data
        return {"data": data}

    def create_room_with_invite(
        self,
        invite: str,
        room_type: int = 1,
        options: dict | None = None,
    ) -> dict:
        payload = {"roomType": int(room_type), "invite": invite}
        if options:
            payload.update(options)
        data = self._request(
            "POST",
            "/room",
            data=payload,
            base_path=self.ocs_conversations_base_path,
            send_json=True,
        )
        if isinstance(data, dict):
            return data
        return {"data": data}

    def post_message(
        self,
        room_token: str,
        message: str,
        options: dict | None = None,
    ) -> dict:
        payload = {"message": message}
        if options:
            payload.update(options)
        # Chat API uses /chat/{token}
        data = self._request(
            "POST",
            f"/chat/{room_token}",
            data=payload,
            base_path=self.ocs_chat_base_path,
        )
        if isinstance(data, dict):
            return data
        return {"data": data}

    def list_messages(
        self,
        room_token: str,
        *,
        last_known_message_id: int = 0,
        limit: int = 100,
        timeout: int = 0,
    ) -> list[dict]:
        """List chat messages for a room.

        Uses the OCS chat API (v1): GET /chat/{token}
        """
        params: dict[str, Any] = {
            "lookIntoFuture": 0,
            "limit": int(limit),
            "timeout": int(timeout),
            "lastKnownMessageId": int(last_known_message_id),
            "includeLastKnown": 0,
        }
        data = self._request(
            "GET",
            f"/chat/{room_token}",
            params=params,
            base_path=self.ocs_chat_base_path,
        )
        if data is None:
            return []
        if isinstance(data, list):
            return [m for m in data if isinstance(m, dict)]
        if isinstance(data, dict):
            return [data]
        return []
