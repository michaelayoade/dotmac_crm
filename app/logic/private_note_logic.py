from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

DecisionStatus = Literal["allow", "deny"]
Visibility = Literal["author", "team", "admins"]

USE_PRIVATE_NOTE_LOGIC_SERVICE = os.getenv("USE_PRIVATE_NOTE_LOGIC_SERVICE", "0") == "1"


@dataclass(frozen=True)
class PrivateNoteContext:
    body: str | None
    is_system_conversation: bool
    author_is_admin: bool
    requested_visibility: Visibility | None = None


@dataclass(frozen=True)
class PrivateNoteDecision:
    status: DecisionStatus
    visibility: Visibility | None
    reason: str | None = None


class LogicService:
    """Pure decision logic for private notes."""

    def decide_create_note(self, ctx: PrivateNoteContext) -> PrivateNoteDecision:
        if not ctx.body or not ctx.body.strip():
            return PrivateNoteDecision(
                status="deny",
                visibility=None,
                reason="Private note body is empty",
            )

        if ctx.is_system_conversation:
            return PrivateNoteDecision(
                status="deny",
                visibility=None,
                reason="Private notes are not allowed for system conversations",
            )

        visibility = self._normalize_visibility(
            requested_visibility=ctx.requested_visibility,
            author_is_admin=ctx.author_is_admin,
        )

        return PrivateNoteDecision(
            status="allow",
            visibility=visibility,
        )

    @staticmethod
    def _normalize_visibility(
        requested_visibility: Visibility | None,
        author_is_admin: bool,
    ) -> Visibility:
        visibility: Visibility = requested_visibility or "team"
        if visibility not in ("author", "team", "admins"):
            visibility = "team"
        if not author_is_admin and visibility != "team":
            visibility = "team"
        return visibility
