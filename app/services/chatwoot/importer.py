"""Chatwoot data importer."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm import (
    ChannelType,
    Conversation,
    ConversationAssignment,
    ConversationStatus,
    CrmAgent,
    CrmTeam,
    Message,
    MessageDirection,
    MessageStatus,
)
from app.models.person import PartyStatus, Person
from app.services.chatwoot.client import ChatwootClient

logger = logging.getLogger(__name__)


@dataclass
class EntityStats:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass
class ImportResult:
    success: bool = True
    contacts: EntityStats = field(default_factory=EntityStats)
    agents: EntityStats = field(default_factory=EntityStats)
    teams: EntityStats = field(default_factory=EntityStats)
    conversations: EntityStats = field(default_factory=EntityStats)
    messages: EntityStats = field(default_factory=EntityStats)
    error_details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "contacts": {
                "created": self.contacts.created,
                "updated": self.contacts.updated,
                "skipped": self.contacts.skipped,
                "errors": self.contacts.errors,
            },
            "agents": {
                "created": self.agents.created,
                "updated": self.agents.updated,
                "skipped": self.agents.skipped,
                "errors": self.agents.errors,
            },
            "teams": {
                "created": self.teams.created,
                "updated": self.teams.updated,
                "skipped": self.teams.skipped,
                "errors": self.teams.errors,
            },
            "conversations": {
                "created": self.conversations.created,
                "updated": self.conversations.updated,
                "skipped": self.conversations.skipped,
                "errors": self.conversations.errors,
            },
            "messages": {
                "created": self.messages.created,
                "updated": self.messages.updated,
                "skipped": self.messages.skipped,
                "errors": self.messages.errors,
            },
            "error_details": self.error_details[:50],  # Limit error details
        }


def _parse_datetime(value: str | int | None) -> datetime | None:
    """Parse datetime from Chatwoot timestamp."""
    if not value:
        return None
    if isinstance(value, int):
        return datetime.fromtimestamp(value, tz=UTC)
    try:
        # Try ISO format
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _map_channel_type(inbox_channel: str | None) -> ChannelType:
    """Map Chatwoot inbox channel type to our ChannelType."""
    channel_map = {
        "Channel::WebWidget": ChannelType.chat_widget,
        "Channel::Email": ChannelType.email,
        "Channel::Whatsapp": ChannelType.whatsapp,
        "Channel::FacebookPage": ChannelType.facebook_messenger,
        "Channel::Instagram": ChannelType.instagram_dm,
        "Channel::TwitterProfile": ChannelType.chat_widget,  # Map to chat_widget
        "Channel::Api": ChannelType.chat_widget,
        "Channel::Sms": ChannelType.chat_widget,  # No SMS in our enum
        "Channel::TwilioSms": ChannelType.chat_widget,  # SMS via Twilio
        "Channel::Line": ChannelType.chat_widget,  # Line messenger
        "Channel::Telegram": ChannelType.chat_widget,  # Telegram
    }
    return channel_map.get(inbox_channel or "", ChannelType.chat_widget)


def _map_conversation_status(cw_status: str | None) -> ConversationStatus:
    """Map Chatwoot conversation status to our status."""
    status_map = {
        "open": ConversationStatus.open,
        "pending": ConversationStatus.pending,
        "resolved": ConversationStatus.resolved,
        "snoozed": ConversationStatus.snoozed,
    }
    return status_map.get(cw_status or "", ConversationStatus.open)


def _truncate(value: str | None, max_len: int) -> str | None:
    """Truncate string to max length."""
    if not value:
        return value
    return value[:max_len] if len(value) > max_len else value


def _parse_name(full_name: str | None, default_first: str = "Contact") -> tuple[str, str]:
    """Parse full name into first and last name, truncated to fit DB fields."""
    if not full_name or not full_name.strip():
        return default_first, ""

    name = full_name.strip()
    parts = name.split(" ", 1)
    first_name = _truncate(parts[0], 80) or default_first
    if len(parts) > 1:
        last_name = _truncate(parts[1], 80) or ""
    else:
        last_name = ""
    return first_name, last_name


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ChatwootImporter:
    """Import data from Chatwoot into our CRM."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        account_id: int = 1,
    ):
        self.client = ChatwootClient(
            base_url=base_url,
            access_token=access_token,
            account_id=account_id,
        )
        # Maps Chatwoot ID -> our UUID
        self._contact_map: dict[int, Person] = {}
        self._agent_map: dict[int, CrmAgent] = {}
        self._team_map: dict[int, CrmTeam] = {}
        self._conversation_map: dict[int, Conversation] = {}

    def close(self):
        self.client.close()

    def import_all(
        self,
        db: Session,
        max_conversations: int | None = None,
        skip_messages: bool = False,
    ) -> ImportResult:
        """Import all data from Chatwoot.

        Args:
            db: Database session
            max_conversations: Limit number of conversations to import (None for all)
            skip_messages: Skip importing messages (faster for initial sync)
        """
        result = ImportResult()

        try:
            # Import in order of dependencies
            logger.info("Starting Chatwoot import...")

            # 1. Import teams first
            self._import_teams(db, result)
            db.commit()

            # 2. Import agents (need teams)
            self._import_agents(db, result)
            db.commit()

            # 3. Import contacts
            self._import_contacts(db, result)
            db.commit()

            # 4. Import conversations (need contacts)
            self._import_conversations(db, result, max_conversations=max_conversations)
            db.commit()

            # 5. Import messages (need conversations)
            if not skip_messages:
                self._import_messages(db, result)
                db.commit()
            else:
                logger.info("Skipping messages import")

            logger.info(f"Chatwoot import complete: {result.to_dict()}")

        except Exception as e:
            result.success = False
            result.error_details.append(f"Import failed: {e!s}")
            logger.exception("Chatwoot import failed")
            db.rollback()

        finally:
            self.close()

        return result

    def _import_teams(self, db: Session, result: ImportResult):
        """Import Chatwoot teams."""
        logger.info("Importing teams...")
        teams = self.client.list_teams()

        for team_data in teams:
            try:
                cw_id = _coerce_int(team_data.get("id"))
                name = team_data.get("name", "").strip()

                if not name or cw_id is None:
                    if cw_id is None:
                        result.error_details.append("Team missing id")
                        result.teams.errors += 1
                    result.teams.skipped += 1
                    continue

                # Check if team exists by name
                existing = db.query(CrmTeam).filter(CrmTeam.name == name).first()

                if existing:
                    # Update existing team
                    existing.metadata_ = {
                        **(existing.metadata_ or {}),
                        "chatwoot_id": cw_id,
                    }
                    self._team_map[cw_id] = existing
                    result.teams.updated += 1
                else:
                    # Create new team
                    team = CrmTeam(
                        name=name,
                        is_active=True,
                        metadata_={"chatwoot_id": cw_id},
                    )
                    db.add(team)
                    db.flush()
                    self._team_map[cw_id] = team
                    result.teams.created += 1

            except Exception as e:
                result.teams.errors += 1
                result.error_details.append(f"Team error: {e}")

    def _import_agents(self, db: Session, result: ImportResult):
        """Import Chatwoot agents as Person + CrmAgent."""
        logger.info("Importing agents...")
        agents = self.client.list_agents()

        for agent_data in agents:
            try:
                cw_id = _coerce_int(agent_data.get("id"))
                email = agent_data.get("email", "").strip().lower()
                name = agent_data.get("name", "").strip()

                if not email or cw_id is None:
                    if cw_id is None:
                        result.error_details.append("Agent missing id")
                        result.agents.errors += 1
                    result.agents.skipped += 1
                    continue

                # Truncate email if needed
                email = _truncate(email, 255)

                # Parse name with truncation
                first_name, last_name = _parse_name(name, default_first="Agent")

                # Find or create Person
                person = db.query(Person).filter(Person.email == email).first()

                if not person:
                    person = Person(
                        email=email,
                        first_name=first_name,
                        last_name=last_name,
                        avatar_url=_truncate(agent_data.get("thumbnail"), 512),
                        party_status=PartyStatus.contact,
                        status="active",
                    )
                    db.add(person)
                    db.flush()

                # Find or create CrmAgent
                existing_agent = (
                    db.query(CrmAgent)
                    .filter(CrmAgent.person_id == person.id)
                    .first()
                )

                if existing_agent:
                    existing_agent.metadata_ = {
                        **(existing_agent.metadata_ or {}),
                        "chatwoot_id": cw_id,
                        "chatwoot_role": agent_data.get("role"),
                    }
                    self._agent_map[cw_id] = existing_agent
                    result.agents.updated += 1
                else:
                    agent = CrmAgent(
                        person_id=person.id,
                        is_active=agent_data.get("confirmed", True),
                        title=agent_data.get("role"),
                        metadata_={
                            "chatwoot_id": cw_id,
                            "chatwoot_role": agent_data.get("role"),
                        },
                    )
                    db.add(agent)
                    db.flush()
                    self._agent_map[cw_id] = agent
                    result.agents.created += 1

            except Exception as e:
                db.rollback()  # Reset failed transaction to continue processing other agents.
                result.agents.errors += 1
                result.error_details.append(f"Agent error ({agent_data.get('email')}): {e}")

    def _import_contacts(self, db: Session, result: ImportResult):
        """Import Chatwoot contacts as Person records."""
        logger.info("Importing contacts...")
        contacts = self.client.get_all_contacts()

        for contact_data in contacts:
            try:
                cw_id = _coerce_int(contact_data.get("id"))
                email = (contact_data.get("email") or "").strip().lower()
                phone = _truncate(contact_data.get("phone_number", ""), 40)
                name = contact_data.get("name", "").strip()

                if cw_id is None:
                    result.contacts.errors += 1
                    result.error_details.append("Contact missing id")
                    continue

                # Skip if no email and no phone
                if not email and not phone:
                    result.contacts.skipped += 1
                    continue

                # Generate placeholder email if missing
                if not email:
                    email = f"chatwoot-{cw_id}@placeholder.local"

                # Truncate email if needed
                email = _truncate(email, 255)

                # Parse name with truncation
                first_name, last_name = _parse_name(name, default_first="Contact")

                # Find existing by email
                person = db.query(Person).filter(Person.email == email).first()

                if person:
                    # Update with Chatwoot metadata
                    person.metadata_ = {
                        **(person.metadata_ or {}),
                        "chatwoot_id": cw_id,
                    }
                    if phone and not person.phone:
                        person.phone = phone
                    if contact_data.get("thumbnail") and not person.avatar_url:
                        person.avatar_url = _truncate(contact_data.get("thumbnail"), 512)
                    # Set display_name if missing
                    if not person.display_name:
                        person.display_name = _truncate(name, 120) if name else phone
                    self._contact_map[cw_id] = person
                    result.contacts.updated += 1
                else:
                    # Create new person with display_name
                    # Use name if available, otherwise phone for WhatsApp contacts
                    display_name = _truncate(name, 120) if name else phone
                    person = Person(
                        email=email,
                        first_name=first_name,
                        last_name=last_name,
                        display_name=display_name,
                        phone=phone or None,
                        avatar_url=_truncate(contact_data.get("thumbnail"), 512),
                        party_status=PartyStatus.contact,
                        status="active",
                        metadata_={"chatwoot_id": cw_id},
                    )
                    db.add(person)
                    db.flush()
                    self._contact_map[cw_id] = person
                    result.contacts.created += 1

            except Exception as e:
                result.contacts.errors += 1
                result.error_details.append(f"Contact error ({contact_data.get('id')}): {e}")

    def _import_conversations(
        self,
        db: Session,
        result: ImportResult,
        max_conversations: int | None = None,
    ):
        """Import Chatwoot conversations."""
        logger.info(f"Importing conversations (max: {max_conversations or 'all'})...")
        conversations = self.client.get_all_conversations(max_records=max_conversations)

        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSONB

        for conv_data in conversations:
            try:
                cw_id = _coerce_int(conv_data.get("id"))
                if cw_id is None:
                    result.conversations.errors += 1
                    result.error_details.append("Conversation missing id")
                    continue

                # Get contact from conversation
                meta = conv_data.get("meta", {})
                sender = meta.get("sender", {})
                contact_id = _coerce_int(sender.get("id"))

                person: Person | None = None
                if contact_id is not None and contact_id in self._contact_map:
                    person = self._contact_map[contact_id]
                else:
                    # Try to find by contact email
                    email = (sender.get("email") or "").strip().lower()
                    if email:
                        person = db.query(Person).filter(Person.email == email).first()
                        if person:
                            if contact_id is not None:
                                self._contact_map[contact_id] = person
                        else:
                            result.conversations.skipped += 1
                            continue
                    else:
                        result.conversations.skipped += 1
                        continue
                if person is None:
                    result.conversations.skipped += 1
                    continue

                # Check if conversation exists by chatwoot_id in metadata
                existing = (
                    db.query(Conversation)
                    .filter(
                        cast(Conversation.metadata_, JSONB).contains({"chatwoot_id": cw_id})
                    )
                    .first()
                )

                # Get channel info - channel is in meta.channel, not inbox.channel_type
                meta = conv_data.get("meta", {})
                channel_type = _map_channel_type(meta.get("channel"))

                # Parse dates
                created_at = _parse_datetime(conv_data.get("created_at"))
                last_message_at = _parse_datetime(conv_data.get("last_activity_at"))

                if existing:
                    # Update existing
                    existing.status = _map_conversation_status(conv_data.get("status"))
                    existing.last_message_at = last_message_at
                    self._conversation_map[cw_id] = existing
                    result.conversations.updated += 1
                    conversation = existing
                else:
                    # Create new conversation
                    conv = Conversation(
                        person_id=person.id,
                        status=_map_conversation_status(conv_data.get("status")),
                        subject=_truncate(
                            conv_data.get("additional_attributes", {}).get("subject"),
                            200
                        ),
                        last_message_at=last_message_at,
                        is_active=True,
                        metadata_={
                            "chatwoot_id": cw_id,
                            "chatwoot_inbox_id": conv_data.get("inbox_id"),
                            "chatwoot_channel": meta.get("channel"),
                            "chatwoot_team": meta.get("team", {}).get("name") if meta.get("team") else None,
                            "channel_type": channel_type.value,
                        },
                    )
                    if created_at:
                        conv.created_at = created_at
                    db.add(conv)
                    db.flush()
                    self._conversation_map[cw_id] = conv
                    result.conversations.created += 1
                    conversation = conv

                # Import assignment only when no active local assignment exists.
                has_active_assignment = (
                    db.query(ConversationAssignment)
                    .filter(ConversationAssignment.conversation_id == conversation.id)
                    .filter(ConversationAssignment.is_active.is_(True))
                    .first()
                )
                if has_active_assignment:
                    continue

                assignee_id = _coerce_int(conv_data.get("assignee_id"))
                team_id = _coerce_int(conv_data.get("team_id"))
                assigned_at = _parse_datetime(conv_data.get("assignee_last_assigned_at"))

                if assignee_id is None and team_id is None:
                    detail = self.client.get_conversation(cw_id)
                    assignee_id = _coerce_int(detail.get("assignee_id"))
                    team_id = _coerce_int(detail.get("team_id"))
                    assigned_at = _parse_datetime(detail.get("assignee_last_assigned_at"))

                agent = self._agent_map.get(assignee_id) if assignee_id else None
                team = self._team_map.get(team_id) if team_id else None

                if agent or team:
                    assignment = ConversationAssignment(
                        conversation_id=conversation.id,
                        agent_id=agent.id if agent else None,
                        team_id=team.id if team else None,
                        assigned_at=assigned_at or datetime.now(UTC),
                        is_active=True,
                    )
                    db.add(assignment)
                    db.flush()

            except Exception as e:
                db.rollback()  # Rollback failed transaction to continue
                result.conversations.errors += 1
                result.error_details.append(f"Conversation error ({conv_data.get('id')}): {e}")

    def _import_messages(self, db: Session, result: ImportResult):
        """Import messages for all conversations."""
        logger.info("Importing messages...")

        for cw_conv_id, conversation in self._conversation_map.items():
            try:
                messages = self.client.get_conversation_messages(cw_conv_id)

                for msg_data in messages:
                    self._import_single_message(db, conversation, msg_data, result)

            except Exception as e:
                result.messages.errors += 1
                result.error_details.append(f"Messages fetch error (conv {cw_conv_id}): {e}")

    def _import_single_message(
        self,
        db: Session,
        conversation: Conversation,
        msg_data: dict[str, Any],
        result: ImportResult,
    ):
        """Import a single message."""
        try:
            cw_id = msg_data.get("id")
            external_id = f"chatwoot-{cw_id}"

            # Check if message already exists
            existing = (
                db.query(Message)
                .filter(Message.external_id == external_id)
                .first()
            )

            if existing:
                result.messages.skipped += 1
                return

            # Determine direction based on Chatwoot message_type:
            # 0 = incoming (from customer)
            # 1 = outgoing (from agent)
            # 2 = activity (system messages like "assigned to", "resolved")
            # 3 = template (outgoing template messages)
            message_type = msg_data.get("message_type")
            if message_type == 0:  # Incoming from customer
                direction = MessageDirection.inbound
            elif message_type == 1:  # Outgoing from agent
                direction = MessageDirection.outbound
            elif message_type == 2:  # Activity/system message
                direction = MessageDirection.internal
            elif message_type == 3:  # Template (outgoing)
                direction = MessageDirection.outbound
            else:
                direction = MessageDirection.inbound

            # Get channel type from conversation metadata
            conv_meta = conversation.metadata_ or {}
            channel_type_str = conv_meta.get("channel_type", "chat_widget")
            try:
                channel_type = ChannelType(channel_type_str)
            except ValueError:
                channel_type = ChannelType.chat_widget

            # Parse timestamps
            created_at = _parse_datetime(msg_data.get("created_at"))

            # Get author (for outbound messages)
            author_id = None
            sender = msg_data.get("sender", {})
            if direction == MessageDirection.outbound and sender:
                sender_id = sender.get("id")
                if sender_id in self._agent_map:
                    agent = self._agent_map[sender_id]
                    author_id = agent.person_id

            # Determine status based on direction
            if direction == MessageDirection.outbound:
                status = MessageStatus.sent
            elif direction == MessageDirection.internal:
                status = MessageStatus.received  # System/activity messages
            else:
                status = MessageStatus.received

            message = Message(
                conversation_id=conversation.id,
                channel_type=channel_type,
                direction=direction,
                status=status,
                body=msg_data.get("content"),
                external_id=external_id,
                author_id=author_id,
                sent_at=created_at if direction == MessageDirection.outbound else None,
                received_at=created_at if direction != MessageDirection.outbound else None,
                metadata_={
                    "chatwoot_id": cw_id,
                    "chatwoot_message_type": message_type,
                    "chatwoot_private": msg_data.get("private", False),
                    "chatwoot_sender_type": msg_data.get("sender_type"),
                },
            )
            if created_at:
                message.created_at = created_at

            db.add(message)
            result.messages.created += 1

        except Exception as e:
            result.messages.errors += 1
            result.error_details.append(f"Message error: {e}")
