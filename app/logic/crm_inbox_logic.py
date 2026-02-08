from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

ChannelType = Literal["email", "whatsapp", "facebook_messenger", "instagram_dm"]
DecisionStatus = Literal["allow", "deny"]


@dataclass(frozen=True)
class MessageContext:
    conversation_id: str
    person_id: str
    requested_channel_type: ChannelType
    requested_channel_target_id: str | None
    last_inbound_channel_type: ChannelType | None
    last_inbound_channel_target_id: str | None
    last_inbound_received_at_iso: str | None
    now_iso: str


@dataclass(frozen=True)
class SendMessageDecision:
    status: DecisionStatus
    channel_type: ChannelType
    channel_target_id: str | None
    reason: str | None = None


@dataclass(frozen=True)
class InboundSelfMessageContext:
    channel_type: ChannelType
    sender_address: str | None
    metadata: dict | None
    self_email_addresses: set[str] | None = None
    business_number: str | None = None


@dataclass(frozen=True)
class InboundDedupeContext:
    channel_type: ChannelType
    contact_address: str
    subject: str | None
    body: str | None
    received_at_iso: str | None
    message_id: str | None
    source_id: str | None = None


@dataclass(frozen=True)
class InboundDedupeDecision:
    message_id: str | None
    dedupe_across_targets: bool = False


class LogicService:
    """Pure decision logic for CRM inbox actions."""

    def decide_send_message(self, ctx: MessageContext) -> SendMessageDecision:
        if (
            ctx.last_inbound_channel_type
            and ctx.requested_channel_type != ctx.last_inbound_channel_type
        ):
            return SendMessageDecision(
                status="deny",
                channel_type=ctx.requested_channel_type,
                channel_target_id=ctx.requested_channel_target_id,
                reason="Reply channel does not match the originating channel",
            )

        if ctx.last_inbound_channel_target_id:
            if (
                ctx.requested_channel_target_id
                and ctx.requested_channel_target_id != ctx.last_inbound_channel_target_id
            ):
                return SendMessageDecision(
                    status="deny",
                    channel_type=ctx.requested_channel_type,
                    channel_target_id=ctx.requested_channel_target_id,
                    reason="Reply channel target does not match the originating channel",
                )
            if not ctx.requested_channel_target_id:
                ctx = MessageContext(
                    conversation_id=ctx.conversation_id,
                    person_id=ctx.person_id,
                    requested_channel_type=ctx.requested_channel_type,
                    requested_channel_target_id=ctx.last_inbound_channel_target_id,
                    last_inbound_channel_type=ctx.last_inbound_channel_type,
                    last_inbound_channel_target_id=ctx.last_inbound_channel_target_id,
                    last_inbound_received_at_iso=ctx.last_inbound_received_at_iso,
                    now_iso=ctx.now_iso,
                )

        if ctx.requested_channel_type in ("facebook_messenger", "instagram_dm"):
            if not ctx.last_inbound_received_at_iso:
                return SendMessageDecision(
                    status="deny",
                    channel_type=ctx.requested_channel_type,
                    channel_target_id=ctx.requested_channel_target_id,
                    reason="Meta reply window expired",
                )
            try:
                last_inbound = datetime.fromisoformat(ctx.last_inbound_received_at_iso)
                now = datetime.fromisoformat(ctx.now_iso)
            except Exception:
                return SendMessageDecision(
                    status="deny",
                    channel_type=ctx.requested_channel_type,
                    channel_target_id=ctx.requested_channel_target_id,
                    reason="Meta reply window expired",
                )
            if last_inbound.tzinfo is None:
                last_inbound = last_inbound.replace(tzinfo=timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            if (now - last_inbound).total_seconds() > 24 * 3600:
                return SendMessageDecision(
                    status="deny",
                    channel_type=ctx.requested_channel_type,
                    channel_target_id=ctx.requested_channel_target_id,
                    reason="Meta reply window expired",
                )

        return SendMessageDecision(
            status="allow",
            channel_type=ctx.requested_channel_type,
            channel_target_id=ctx.requested_channel_target_id,
        )

    def decide_inbound_self_message(self, ctx: InboundSelfMessageContext) -> bool:
        if self._metadata_indicates_self(ctx.metadata):
            return True

        if ctx.channel_type == "email":
            sender = self._normalize_email_address(ctx.sender_address)
            if not sender:
                return False
            self_addresses = ctx.self_email_addresses or set()
            if not self_addresses:
                return False
            return sender in self_addresses

        if ctx.channel_type == "whatsapp":
            business_number = self._normalize_phone_address(ctx.business_number)
            if not business_number:
                return False
            sender = self._normalize_phone_address(ctx.sender_address)
            if not sender:
                return False
            return sender == business_number

        return False

    def decide_inbound_dedupe(self, ctx: InboundDedupeContext) -> InboundDedupeDecision:
        if ctx.channel_type == "email":
            external_id = self._normalize_external_id(ctx.message_id)
            if not external_id:
                received_at = self._parse_iso_datetime(ctx.received_at_iso)
                external_id = self._build_inbound_dedupe_id(
                    channel_type=ctx.channel_type,
                    contact_address=ctx.contact_address,
                    subject=ctx.subject,
                    body=ctx.body,
                    received_at=received_at,
                    source_id=ctx.source_id,
                )
            return InboundDedupeDecision(
                message_id=external_id,
                dedupe_across_targets=True,
            )

        return InboundDedupeDecision(
            message_id=ctx.message_id,
            dedupe_across_targets=False,
        )

    @staticmethod
    def _normalize_email_address(address: str | None) -> str | None:
        if not address:
            return None
        candidate = address.strip().lower()
        return candidate or None

    @staticmethod
    def _normalize_phone_address(value: str | None) -> str | None:
        if not value:
            return None
        digits = "".join(ch for ch in value if ch.isdigit())
        if not digits:
            return None
        return f"+{digits}"

    @staticmethod
    def _normalize_external_id(raw_id: str | None) -> str | None:
        if not raw_id:
            return None
        candidate = raw_id.strip()
        if not candidate:
            return None
        if len(candidate) > 120:
            import hashlib

            return hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        return candidate

    @staticmethod
    def _parse_iso_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None

    @staticmethod
    def _build_inbound_dedupe_id(
        channel_type: ChannelType,
        contact_address: str,
        subject: str | None,
        body: str | None,
        received_at: datetime | None,
        source_id: str | None = None,
    ) -> str:
        import hashlib

        address = contact_address.strip()
        if channel_type == "email":
            address = address.lower()
        received_at_str = ""
        if received_at:
            received_at_str = received_at.replace(microsecond=0).isoformat()
        key = "|".join(
            [
                channel_type,
                source_id or "",
                address,
                subject or "",
                body or "",
                received_at_str,
            ]
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @staticmethod
    def _metadata_indicates_self(metadata: dict | None) -> bool:
        if not isinstance(metadata, dict):
            return False
        if metadata.get("is_echo") or metadata.get("from_me") or metadata.get("sent_by_business"):
            return True
        sender_type = metadata.get("sender_type") or metadata.get("author_type")
        if isinstance(sender_type, str) and sender_type.lower() in {
            "business",
            "agent",
            "system",
            "page",
            "company",
        }:
            return True
        direction = metadata.get("direction")
        if isinstance(direction, str) and direction.lower() in {"outbound", "sent", "business"}:
            return True
        return False
