"""Query functions for inbox statistics and conversation listing."""

from datetime import datetime

from sqlalchemy import case, func, or_, select, true
from sqlalchemy.orm import Session, aliased, selectinload
from sqlalchemy.sql import lateral

from app.models.crm.conversation import (
    Conversation,
    ConversationAssignment,
    Message,
    MessageAttachment,
)
from app.models.crm.enums import (
    ChannelType,
    ConversationPriority,
    ConversationStatus,
    MessageDirection,
    MessageStatus,
)
from app.models.crm.outbox import OutboxMessage
from app.models.crm.team import CrmAgent
from app.models.integration import IntegrationTarget
from app.models.person import Person, PersonChannel
from app.services.common import coerce_uuid
from app.services.crm.inbox import outbox as outbox_service


def list_inbox_conversations(
    db: Session,
    channel: ChannelType | None = None,
    status: ConversationStatus | None = None,
    statuses: list[ConversationStatus] | None = None,
    priority: ConversationPriority | None = None,
    outbox_status: str | None = None,
    search: str | None = None,
    assignment: str | None = None,
    assigned_person_id: str | None = None,
    channel_target_id: str | None = None,
    exclude_superseded_resolved: bool = True,
    filter_agent_id: str | None = None,
    assigned_from: datetime | None = None,
    assigned_to: datetime | None = None,
    sort_by: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[tuple]:
    """List inbox conversations with latest message and unread count.

    Returns a list of tuples: (Conversation, latest_message_dict, unread_count, failed_outbox_summary)
    where latest_message_dict contains: body, channel_type, received_at, sent_at, created_at,
    last_message_at, message_type, has_attachments
    """
    query = (
        db.query(Conversation)
        .options(
            selectinload(Conversation.contact).selectinload(Person.channels),
            selectinload(Conversation.assignments),
        )
        .select_from(Conversation)
        .filter(Conversation.is_active.is_(True))
    )

    if status:
        query = query.filter(Conversation.status == status)
    elif statuses:
        query = query.filter(Conversation.status.in_(statuses))

    if priority:
        query = query.filter(Conversation.priority == priority)

    if channel:
        subq = db.query(Message.conversation_id).filter(Message.channel_type == channel).distinct()
        query = query.filter(Conversation.id.in_(subq))

    if channel_target_id:
        try:
            target_uuid = coerce_uuid(channel_target_id)
        except Exception:
            target_uuid = None
        if target_uuid:
            target_subq = db.query(Message.conversation_id).filter(Message.channel_target_id == target_uuid)
            if channel:
                target_subq = target_subq.filter(Message.channel_type == channel)
            query = query.filter(Conversation.id.in_(target_subq.distinct()))

    assignment_filter = (assignment or "").strip().lower()
    if assignment_filter in ("assigned", "assigned_to_me", "mine"):
        if not assigned_person_id:
            return []
        agent_ids = [
            row[0]
            for row in (
                db.query(CrmAgent.id)
                .filter(CrmAgent.person_id == coerce_uuid(assigned_person_id))
                .filter(CrmAgent.is_active.is_(True))
                .all()
            )
        ]
        if not agent_ids:
            return []
        assigned_subq = (
            db.query(ConversationAssignment.conversation_id)
            .filter(ConversationAssignment.is_active.is_(True))
            .filter(ConversationAssignment.agent_id.in_(agent_ids))
            .distinct()
        )
        query = query.filter(Conversation.id.in_(assigned_subq))
    elif assignment_filter == "unassigned":
        # Treat team-only assignments as unassigned (agent_id is NULL).
        assigned_subq = (
            db.query(ConversationAssignment.conversation_id)
            .filter(ConversationAssignment.is_active.is_(True))
            .filter(ConversationAssignment.agent_id.isnot(None))
            .distinct()
        )
        query = query.filter(~Conversation.id.in_(assigned_subq))
    elif assignment_filter == "my_team":
        if not assigned_person_id:
            return []
        from app.models.crm.team import CrmTeam
        from app.models.service_team import ServiceTeamMember

        person_uuid = coerce_uuid(assigned_person_id)
        # Find ServiceTeams the person belongs to
        team_ids_subq = (
            db.query(ServiceTeamMember.team_id)
            .filter(ServiceTeamMember.person_id == person_uuid)
            .filter(ServiceTeamMember.is_active.is_(True))
        )
        # Find CrmTeams linked to those ServiceTeams
        crm_team_ids_subq = (
            db.query(CrmTeam.id).filter(CrmTeam.service_team_id.in_(team_ids_subq)).filter(CrmTeam.is_active.is_(True))
        )
        # Find conversations assigned to those CRM teams
        team_conv_subq = (
            db.query(ConversationAssignment.conversation_id)
            .filter(ConversationAssignment.is_active.is_(True))
            .filter(ConversationAssignment.team_id.in_(crm_team_ids_subq))
            .distinct()
        )
        query = query.filter(Conversation.id.in_(team_conv_subq))
    elif assignment_filter == "agent":
        if not filter_agent_id:
            return []
        agent_uuid = coerce_uuid(filter_agent_id)
        agent_subq = (
            db.query(ConversationAssignment.conversation_id)
            .filter(ConversationAssignment.is_active.is_(True))
            .filter(ConversationAssignment.agent_id == agent_uuid)
        )
        if assigned_from:
            agent_subq = agent_subq.filter(ConversationAssignment.assigned_at >= assigned_from)
        if assigned_to:
            agent_subq = agent_subq.filter(ConversationAssignment.assigned_at <= assigned_to)
        query = query.filter(Conversation.id.in_(agent_subq.distinct()))

    if search:
        raw_search = search.strip()
        search_term = f"%{raw_search}%"
        phone_digits = "".join(ch for ch in raw_search if ch.isdigit())

        def _normalize_phone_sql(expr):
            # Database-agnostic normalization by stripping common phone punctuation.
            return func.replace(
                func.replace(
                    func.replace(
                        func.replace(
                            func.replace(
                                func.replace(expr, " ", ""),
                                "-",
                                "",
                            ),
                            "(",
                            "",
                        ),
                        ")",
                        "",
                    ),
                    "+",
                    "",
                ),
                ".",
                "",
            )

        person_channel_like_exists = (
            db.query(PersonChannel.id)
            .filter(PersonChannel.person_id == Conversation.person_id)
            .filter(PersonChannel.address.ilike(search_term))
            .exists()
        )

        search_filters = [
            Person.display_name.ilike(search_term),
            Person.email.ilike(search_term),
            Person.phone.ilike(search_term),
            Conversation.subject.ilike(search_term),
            person_channel_like_exists,
        ]

        if phone_digits:
            digits_term = f"%{phone_digits}%"
            person_channel_phone_digits_exists = (
                db.query(PersonChannel.id)
                .filter(PersonChannel.person_id == Conversation.person_id)
                .filter(_normalize_phone_sql(PersonChannel.address).ilike(digits_term))
                .exists()
            )
            search_filters.extend(
                [
                    _normalize_phone_sql(Person.phone).ilike(digits_term),
                    person_channel_phone_digits_exists,
                ]
            )

        query = query.join(Conversation.contact).filter(or_(*search_filters))

    if exclude_superseded_resolved and (not status or status != ConversationStatus.resolved):
        other = aliased(Conversation)
        newer_open = (
            db.query(other.id)
            .filter(other.person_id == Conversation.person_id)
            .filter(other.status.in_([ConversationStatus.open, ConversationStatus.pending]))
            .filter(other.is_active.is_(True))
            .filter(other.updated_at > Conversation.updated_at)
            .exists()
        )
        query = query.filter(~((Conversation.status == ConversationStatus.resolved) & newer_open))

    outbox_status_filter = (outbox_status or "").strip().lower() or None
    failed_outbox_subq = None
    if outbox_status_filter == outbox_service.STATUS_FAILED:
        failed_exists = (
            db.query(OutboxMessage.id)
            .filter(OutboxMessage.conversation_id == Conversation.id)
            .filter(OutboxMessage.status == outbox_service.STATUS_FAILED)
            .exists()
        )
        query = query.filter(failed_exists)

    # Priority sort expression: urgent=0, high=1, medium=2, low=3, none=4
    priority_sort_expr = case(
        (Conversation.priority == ConversationPriority.urgent, 0),
        (Conversation.priority == ConversationPriority.high, 1),
        (Conversation.priority == ConversationPriority.medium, 2),
        (Conversation.priority == ConversationPriority.low, 3),
        else_=4,
    )

    if db.bind is not None and db.bind.dialect.name == "sqlite":
        if sort_by == "priority":
            query = query.order_by(
                priority_sort_expr,
                Conversation.last_message_at.desc(),
                Conversation.updated_at.desc(),
            )
        else:
            query = query.order_by(
                Conversation.last_message_at.desc(),
                Conversation.updated_at.desc(),
            )
        conversations = query.limit(limit).offset(offset).all()
        if not conversations:
            return []

        conversation_ids = [c.id for c in conversations]
        unread_rows = (
            db.query(
                Message.conversation_id,
                func.count(Message.id),
            )
            .filter(Message.conversation_id.in_(conversation_ids))
            .filter(Message.direction == MessageDirection.inbound)
            .filter(Message.status == MessageStatus.received)
            .filter(Message.read_at.is_(None))
            .group_by(Message.conversation_id)
            .all()
        )
        unread_map = {row[0]: int(row[1] or 0) for row in unread_rows}

        result: list[tuple] = []
        for conv in conversations:
            has_attachments = db.query(MessageAttachment.id).filter(MessageAttachment.message_id == Message.id).exists()
            msg_row = (
                db.query(
                    Message.body,
                    Message.channel_type,
                    Message.channel_target_id,
                    IntegrationTarget.name,
                    Message.received_at,
                    Message.sent_at,
                    Message.created_at,
                    func.coalesce(Message.received_at, Message.sent_at, Message.created_at),
                    Message.metadata_,
                    has_attachments,
                )
                .outerjoin(IntegrationTarget, IntegrationTarget.id == Message.channel_target_id)
                .filter(Message.conversation_id == conv.id)
                .order_by(func.coalesce(Message.received_at, Message.sent_at, Message.created_at).desc())
                .first()
            )
            latest_message = None
            if msg_row is not None:
                metadata = msg_row[8] if isinstance(msg_row[8], dict) else None
                latest_message = {
                    "body": msg_row[0],
                    "channel_type": msg_row[1],
                    "channel_target_id": msg_row[2],
                    "channel_target_name": msg_row[3],
                    "received_at": msg_row[4],
                    "sent_at": msg_row[5],
                    "created_at": msg_row[6],
                    "last_message_at": msg_row[7],
                    "metadata": metadata,
                    "message_type": metadata.get("type") if metadata else None,
                    "has_attachments": bool(msg_row[9]) if msg_row[9] is not None else False,
                }

            failed_outbox = None
            if outbox_status_filter == outbox_service.STATUS_FAILED:
                fo = (
                    db.query(OutboxMessage.last_error, OutboxMessage.attempts, OutboxMessage.last_attempt_at)
                    .filter(OutboxMessage.conversation_id == conv.id)
                    .filter(OutboxMessage.status == outbox_service.STATUS_FAILED)
                    .order_by(
                        (OutboxMessage.last_attempt_at.is_(None)).asc(),
                        OutboxMessage.last_attempt_at.desc(),
                        OutboxMessage.updated_at.desc(),
                    )
                    .first()
                )
                if fo is not None:
                    failed_outbox = {
                        "last_error": fo[0],
                        "attempts": int(fo[1] or 0),
                        "last_attempt_at": fo[2],
                    }

            result.append((conv, latest_message, unread_map.get(conv.id, 0), failed_outbox))
        return result

    last_message_ts = func.coalesce(
        Message.received_at,
        Message.sent_at,
        Message.created_at,
    )
    has_attachments = db.query(MessageAttachment.id).filter(MessageAttachment.message_id == Message.id).exists()

    # LATERAL subquery to fetch only the latest message per conversation.
    latest_message_subq = lateral(
        select(
            Message.conversation_id.label("conv_id"),
            Message.body.label("body"),
            Message.channel_type.label("channel_type"),
            Message.channel_target_id.label("channel_target_id"),
            IntegrationTarget.name.label("channel_target_name"),
            Message.received_at.label("received_at"),
            Message.sent_at.label("sent_at"),
            Message.created_at.label("created_at"),
            last_message_ts.label("last_message_at"),
            Message.metadata_.label("metadata"),
            has_attachments.label("has_attachments"),
        )
        .select_from(Message)
        .outerjoin(IntegrationTarget, IntegrationTarget.id == Message.channel_target_id)
        .where(Message.conversation_id == Conversation.id)
        .order_by(last_message_ts.desc())
        .limit(1)
    ).alias("latest_message")

    unread_subq = (
        db.query(
            Message.conversation_id.label("conv_id"),
            func.count(Message.id).label("unread_count"),
        )
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.status == MessageStatus.received)
        .filter(Message.read_at.is_(None))
        .group_by(Message.conversation_id)
        .subquery()
    )

    query = query.outerjoin(
        latest_message_subq,
        true(),
    ).outerjoin(
        unread_subq,
        unread_subq.c.conv_id == Conversation.id,
    )

    if outbox_status_filter == outbox_service.STATUS_FAILED:
        # LATERAL: pick the most recent failed outbox row for each conversation (deterministic).
        failed_outbox_subq = lateral(
            select(
                OutboxMessage.last_error.label("last_error"),
                OutboxMessage.attempts.label("attempts"),
                OutboxMessage.last_attempt_at.label("last_attempt_at"),
            )
            .select_from(OutboxMessage)
            .where(OutboxMessage.conversation_id == Conversation.id)
            .where(OutboxMessage.status == outbox_service.STATUS_FAILED)
            .order_by(
                (OutboxMessage.last_attempt_at.is_(None)).asc(),
                OutboxMessage.last_attempt_at.desc(),
                OutboxMessage.updated_at.desc(),
            )
            .limit(1)
        ).alias("failed_outbox")
        query = query.outerjoin(
            failed_outbox_subq,
            true(),
        )

    if sort_by == "priority":
        query = query.order_by(
            priority_sort_expr,
            Conversation.last_message_at.desc().nullslast(),
            Conversation.updated_at.desc(),
        )
    else:
        query = query.order_by(
            Conversation.last_message_at.desc().nullslast(),
            Conversation.updated_at.desc(),
        )

    cols = [
        latest_message_subq.c.body,
        latest_message_subq.c.channel_type,
        latest_message_subq.c.channel_target_id,
        latest_message_subq.c.channel_target_name,
        latest_message_subq.c.received_at,
        latest_message_subq.c.sent_at,
        latest_message_subq.c.created_at,
        latest_message_subq.c.last_message_at,
        latest_message_subq.c.metadata,
        latest_message_subq.c.has_attachments,
        unread_subq.c.unread_count,
    ]
    if failed_outbox_subq is not None:
        cols.extend(
            [
                failed_outbox_subq.c.last_error,
                failed_outbox_subq.c.attempts,
                failed_outbox_subq.c.last_attempt_at,
            ]
        )

    conversations_raw = query.add_columns(*cols).limit(limit).offset(offset).all()

    result = []
    for row in conversations_raw:
        conv = row[0]
        failed_outbox = None
        if failed_outbox_subq is not None and len(row) >= 15:
            # cols has 11 entries (indices 1..11); failed_outbox starts right after
            fo_offset = 12  # 1 (conv) + 11 (cols)
            failed_outbox = {
                "last_error": row[fo_offset],
                "attempts": int(row[fo_offset + 1] or 0),
                "last_attempt_at": row[fo_offset + 2],
            }

        latest_message = None
        body, channel_type, channel_target_id, channel_target_name = row[1:5]
        received_at, sent_at, created_at, last_message_at = row[5:9]
        raw_metadata, has_attachments, unread_count = row[9:12]
        if body is not None or channel_type is not None or raw_metadata is not None:
            metadata = raw_metadata if isinstance(raw_metadata, dict) else None
            latest_message = {
                "body": body,
                "channel_type": channel_type,
                "channel_target_id": channel_target_id,
                "channel_target_name": channel_target_name,
                "received_at": received_at,
                "sent_at": sent_at,
                "created_at": created_at,
                "last_message_at": last_message_at,
                "metadata": metadata,
                "message_type": metadata.get("type") if metadata else None,
                "has_attachments": bool(has_attachments) if has_attachments is not None else False,
            }
        unread_count_value = unread_count or 0
        result.append((conv, latest_message, unread_count_value, failed_outbox))

    return result


def get_inbox_stats(db: Session) -> dict:
    """Get inbox statistics efficiently.

    Returns: {total, open, pending, snoozed, resolved, unread}
    Uses single GROUP BY query instead of loading all conversations.
    """
    # Single query with GROUP BY for status counts
    status_counts = (
        db.query(
            Conversation.status,
            func.count(Conversation.id).label("count"),
        )
        .filter(Conversation.is_active.is_(True))
        .group_by(Conversation.status)
        .all()
    )

    # Build stats dict from results
    stats = {
        "all": 0,
        "open": 0,
        "pending": 0,
        "snoozed": 0,
        "resolved": 0,
        "unread": 0,
    }

    for status, count in status_counts:
        stats["all"] += count
        if status == ConversationStatus.open:
            stats["open"] = count
        elif status == ConversationStatus.pending:
            stats["pending"] = count
        elif status == ConversationStatus.snoozed:
            stats["snoozed"] = count
        elif status == ConversationStatus.resolved:
            stats["resolved"] = count

    stats["unread"] = int(
        db.query(func.count(func.distinct(Message.conversation_id)))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Conversation.is_active.is_(True))
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.status == MessageStatus.received)
        .filter(Message.read_at.is_(None))
        .scalar()
        or 0
    )

    return stats


def get_waiting_queue_counts_by_channel(db: Session) -> dict[str, int]:
    """Get per-channel counts for the 'needs_action' queue (open + snoozed).

    This matches the inbox channel filter semantics (a conversation is included for a channel
    if it has *any* messages with that channel_type).

    Returns: {email: N, whatsapp: N, ...}
    """
    waiting_statuses = [ConversationStatus.open, ConversationStatus.snoozed]

    rows = (
        db.query(
            Message.channel_type,
            func.count(func.distinct(Conversation.id)).label("conv_count"),
        )
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Conversation.is_active.is_(True))
        .filter(Conversation.status.in_(waiting_statuses))
        .group_by(Message.channel_type)
        .all()
    )

    counts: dict[str, int] = {ct.value: 0 for ct in ChannelType}
    for channel_type, conv_count in rows:
        if channel_type:
            counts[channel_type.value] = int(conv_count or 0)

    return counts


def get_channel_stats(db: Session) -> dict[str, int]:
    """Get conversation counts per channel in a single query.

    Returns: {email: N, whatsapp: N, ...}
    """
    from app.models.crm.comments import SocialComment

    channel_stats = {}

    # Use a single query with group by for better performance
    channel_counts = (
        db.query(
            Message.channel_type,
            func.count(func.distinct(Message.conversation_id)).label("conv_count"),
        )
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Conversation.is_active.is_(True))
        .group_by(Message.channel_type)
        .all()
    )

    # Initialize all channel types to 0
    for ct in ChannelType:
        channel_stats[ct.value] = 0

    # Fill in actual counts
    for channel_type, count in channel_counts:
        if channel_type:
            channel_stats[channel_type.value] = count

    # Add comments count
    channel_stats["comments"] = db.query(SocialComment).filter(SocialComment.is_active.is_(True)).count()

    return channel_stats


class InboxQueries:
    @staticmethod
    def list_conversations(
        db: Session,
        channel: ChannelType | None = None,
        status: ConversationStatus | None = None,
        priority: ConversationPriority | None = None,
        outbox_status: str | None = None,
        search: str | None = None,
        assignment: str | None = None,
        assigned_person_id: str | None = None,
        channel_target_id: str | None = None,
        exclude_superseded_resolved: bool = True,
        filter_agent_id: str | None = None,
        assigned_from: datetime | None = None,
        assigned_to: datetime | None = None,
        sort_by: str | None = None,
        limit: int = 50,
    ) -> list[tuple]:
        return list_inbox_conversations(
            db=db,
            channel=channel,
            status=status,
            priority=priority,
            outbox_status=outbox_status,
            search=search,
            assignment=assignment,
            assigned_person_id=assigned_person_id,
            channel_target_id=channel_target_id,
            exclude_superseded_resolved=exclude_superseded_resolved,
            filter_agent_id=filter_agent_id,
            assigned_from=assigned_from,
            assigned_to=assigned_to,
            sort_by=sort_by,
            limit=limit,
        )

    @staticmethod
    def get_inbox_stats(db: Session) -> dict:
        return get_inbox_stats(db)

    @staticmethod
    def get_channel_stats(db: Session) -> dict[str, int]:
        return get_channel_stats(db)


# Singleton instance
inbox_queries = InboxQueries()
