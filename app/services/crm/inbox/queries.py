"""Query functions for inbox statistics and conversation listing."""

from sqlalchemy import func, select, true
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
    ConversationStatus,
    MessageDirection,
    MessageStatus,
)
from app.models.crm.team import CrmAgent
from app.models.integration import IntegrationTarget
from app.models.person import Person
from app.services.common import coerce_uuid


def list_inbox_conversations(
    db: Session,
    channel: ChannelType | None = None,
    status: ConversationStatus | None = None,
    statuses: list[ConversationStatus] | None = None,
    search: str | None = None,
    assignment: str | None = None,
    assigned_person_id: str | None = None,
    channel_target_id: str | None = None,
    exclude_superseded_resolved: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[tuple]:
    """List inbox conversations with latest message and unread count.

    Returns a list of tuples: (Conversation, latest_message_dict, unread_count)
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

    if search:
        search_term = f"%{search.strip()}%"
        query = query.join(Conversation.contact).filter(
            (Person.display_name.ilike(search_term))
            | (Person.email.ilike(search_term))
            | (Person.phone.ilike(search_term))
            | (Conversation.subject.ilike(search_term))
        )

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

    query = query.order_by(
        Conversation.last_message_at.desc().nullslast(),
        Conversation.updated_at.desc(),
    )

    conversations_raw = (
        query.add_columns(
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
        )
        .limit(limit)
        .offset(offset)
        .all()
    )

    result = []
    for row in conversations_raw:
        conv = row[0]
        latest_message = None
        if row[1] is not None or row[2] is not None or row[9] is not None:
            metadata = row[9] if isinstance(row[9], dict) else None
            latest_message = {
                "body": row[1],
                "channel_type": row[2],
                "channel_target_id": row[3],
                "channel_target_name": row[4],
                "received_at": row[5],
                "sent_at": row[6],
                "created_at": row[7],
                "last_message_at": row[8],
                "metadata": metadata,
                "message_type": metadata.get("type") if metadata else None,
                "has_attachments": bool(row[10]) if row[10] is not None else False,
            }
        unread_count = row[11] or 0
        result.append((conv, latest_message, unread_count))

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
        search: str | None = None,
        assignment: str | None = None,
        assigned_person_id: str | None = None,
        channel_target_id: str | None = None,
        exclude_superseded_resolved: bool = True,
        limit: int = 50,
    ) -> list[tuple]:
        return list_inbox_conversations(
            db=db,
            channel=channel,
            status=status,
            search=search,
            assignment=assignment,
            assigned_person_id=assigned_person_id,
            channel_target_id=channel_target_id,
            exclude_superseded_resolved=exclude_superseded_resolved,
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
