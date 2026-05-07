"""Inbox page context builder for admin UI."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session, aliased

from app.logic import private_note_logic
from app.models.connector import ConnectorType
from app.models.crm.campaign import Campaign
from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import ChannelType, MessageDirection
from app.models.crm.team import CrmAgent, CrmTeam
from app.models.customer_retention import CustomerRetentionEngagement
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.models.subscriber import Subscriber
from app.services import crm as crm_service
from app.services.common import coerce_uuid
from app.services.crm import contact as contact_service
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox.agents import get_current_agent_id, list_active_agents_for_mentions
from app.services.crm.inbox.comments_context import list_comment_inboxes, load_comments_context
from app.services.crm.inbox.csat import get_conversation_csat_event
from app.services.crm.inbox.dashboard import load_inbox_stats
from app.services.crm.inbox.formatting import (
    _format_inbox_datetime_label,
    filter_messages_for_user,
    format_contact_for_template,
    format_conversation_for_template,
    format_message_for_template,
)
from app.services.crm.inbox.inboxes import get_email_channel_state, list_channel_targets
from app.services.crm.inbox.labels import enrich_formatted_conversations_with_labels
from app.services.crm.inbox.listing import load_inbox_list
from app.services.crm.inbox.macros import conversation_macros
from app.services.crm.inbox.queries import get_assignment_counts
from app.services.crm.inbox.templates import message_templates
from app.services.settings_spec import resolve_value
from app.services.time_preferences import resolve_company_time_prefs

logger = logging.getLogger(__name__)


def _load_retention_billing_risk_helpers():
    from app.web.admin.billing_risk import (
        RETENTION_PIPELINE_STEPS,
        _retention_engagement_payload,
        _retention_rep_options,
    )

    return RETENTION_PIPELINE_STEPS, _retention_engagement_payload, _retention_rep_options


def _person_label(person: Person | None) -> str | None:
    if not person:
        return None
    full_name = person.display_name or " ".join(part for part in [person.first_name, person.last_name] if part).strip()
    return full_name or person.email or None


def _annotate_conversation_time_labels(db: Session, conversations: list[dict]) -> None:
    """Ensure all inbox list items have a timezone-aware display label."""
    for item in conversations:
        if item.get("last_message_at_label"):
            continue
        value = item.get("last_message_at")
        if isinstance(value, datetime):
            try:
                from app.services.crm.inbox.formatting import _format_inbox_time_label

                item["last_message_at_label"] = _format_inbox_time_label(value, db)
            except Exception:
                logger.debug("Failed to annotate inbox conversation time label.", exc_info=True)


def _load_assignment_activity(
    db: Session,
    *,
    conversation_id: str,
    limit: int = 5,
) -> tuple[list[dict], dict | None]:
    assigner = aliased(Person)
    agent_person = aliased(Person)
    rows = (
        db.query(ConversationAssignment, assigner, CrmAgent, agent_person, CrmTeam)
        .outerjoin(assigner, assigner.id == ConversationAssignment.assigned_by_id)
        .outerjoin(CrmAgent, CrmAgent.id == ConversationAssignment.agent_id)
        .outerjoin(agent_person, agent_person.id == CrmAgent.person_id)
        .outerjoin(CrmTeam, CrmTeam.id == ConversationAssignment.team_id)
        .filter(ConversationAssignment.conversation_id == coerce_uuid(conversation_id))
        .order_by(
            ConversationAssignment.assigned_at.desc().nullslast(),
            ConversationAssignment.created_at.desc(),
        )
        .limit(limit)
        .all()
    )

    events: list[dict] = []
    latest_manual: dict | None = None
    for assignment, assigned_by, _agent, assigned_agent_person, assigned_team in rows:
        assigned_to_label = _person_label(assigned_agent_person)
        if not assigned_to_label and assigned_team:
            assigned_to_label = assigned_team.name or "Team"
        if not assigned_to_label:
            assigned_to_label = "Unassigned"

        assigned_by_label = _person_label(assigned_by) or "System"
        assigned_at = assignment.assigned_at or assignment.created_at
        event = {
            "assigned_to": assigned_to_label,
            "assigned_by": assigned_by_label,
            "assigned_at": assigned_at,
            "is_manual": bool(assignment.assigned_by_id),
        }
        events.append(event)
        if latest_manual is None and event["is_manual"]:
            latest_manual = event

    return events, latest_manual


def _conversation_retention_context(
    db: Session,
    *,
    conversation_id: str,
    contact: Person | None,
    current_user: dict | None = None,
    flash_message: str | None = None,
    error_message: str | None = None,
    open_panel: bool = False,
) -> dict | None:
    conversation = db.get(Conversation, coerce_uuid(conversation_id))
    if not conversation:
        return None

    conversation_metadata = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
    campaign_id = str(conversation_metadata.get("campaign_id") or "").strip()
    campaign_kind = str(conversation_metadata.get("campaign_kind") or "").strip().lower()
    source_report = str(conversation_metadata.get("source_report") or "").strip().lower()
    retention_customer_id = str(conversation_metadata.get("retention_customer_id") or "").strip()

    campaign = None
    if campaign_id:
        campaign = db.get(Campaign, coerce_uuid(campaign_id))
        if campaign and not source_report:
            campaign_metadata = campaign.metadata_ if isinstance(campaign.metadata_, dict) else {}
            source_report = str(campaign_metadata.get("source_report") or "").strip().lower()
            if not retention_customer_id and isinstance(campaign_metadata.get("audience_snapshot"), list):
                target_person_id = str(getattr(contact, "id", "") or "").strip()
                for row in campaign_metadata.get("audience_snapshot") or []:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("person_id") or "").strip() != target_person_id:
                        continue
                    retention_customer_id = str(
                        row.get("retention_customer_id") or row.get("subscriber_id") or ""
                    ).strip()
                    break

    if not retention_customer_id and contact and isinstance(contact.metadata_, dict):
        retention_customer_id = str(contact.metadata_.get("splynx_id") or "").strip()
    if not retention_customer_id and contact:
        subscriber = db.query(Subscriber).filter(Subscriber.person_id == contact.id).first()
        if subscriber:
            retention_customer_id = str(subscriber.external_id or subscriber.id or "").strip()

    is_billing_risk_retention = (
        campaign_kind == "outreach" and source_report == "billing_risk" and bool(retention_customer_id)
    )
    if not is_billing_risk_retention:
        return None

    latest_engagement = (
        db.query(CustomerRetentionEngagement)
        .filter(
            CustomerRetentionEngagement.customer_external_id == retention_customer_id,
            CustomerRetentionEngagement.is_active.is_(True),
        )
        .order_by(CustomerRetentionEngagement.created_at.desc())
        .first()
    )
    recent_engagements = (
        db.query(CustomerRetentionEngagement)
        .filter(
            CustomerRetentionEngagement.customer_external_id == retention_customer_id,
            CustomerRetentionEngagement.is_active.is_(True),
        )
        .order_by(CustomerRetentionEngagement.created_at.desc())
        .limit(3)
        .all()
    )
    inbound_reply_count = (
        db.query(func.count(Message.id))
        .filter(
            Message.conversation_id == conversation.id,
            Message.direction == MessageDirection.inbound,
        )
        .scalar()
        or 0
    )
    has_inbound_reply = inbound_reply_count > 0

    rep_person_id = ""
    if latest_engagement and latest_engagement.rep_person_id:
        rep_person_id = str(latest_engagement.rep_person_id)
    elif current_user:
        rep_person_id = str(current_user.get("person_id") or current_user.get("id") or "").strip()

    retention_pipeline_steps, retention_engagement_payload, retention_rep_options = (
        _load_retention_billing_risk_helpers()
    )

    button_label = "Retention outcome"
    if latest_engagement:
        button_label = "Update retention outcome"
    elif has_inbound_reply:
        button_label = "Customer replied, log outcome"

    return {
        "enabled": True,
        "campaign_id": campaign_id or None,
        "customer_id": retention_customer_id,
        "customer_name": (
            getattr(contact, "display_name", None)
            or getattr(contact, "email", None)
            or getattr(contact, "phone", None)
            or retention_customer_id
        ),
        "has_inbound_reply": has_inbound_reply,
        "inbound_reply_count": int(inbound_reply_count),
        "latest_engagement": retention_engagement_payload(latest_engagement) if latest_engagement else None,
        "engagement_history": [retention_engagement_payload(row) for row in recent_engagements],
        "pipeline_steps": list(retention_pipeline_steps),
        "rep_options": retention_rep_options(db),
        "default_rep_person_id": rep_person_id,
        "button_label": button_label,
        "button_emphasis": bool(has_inbound_reply and not latest_engagement),
        "flash_message": flash_message,
        "error_message": error_message,
        "open": bool(open_panel),
    }


async def build_inbox_page_context(
    db: Session,
    *,
    current_user: dict | None,
    sidebar_stats: dict,
    csrf_token: str,
    query_params: Mapping[str, str],
    channel: str | None = None,
    status: str | None = None,
    outbox_status: str | None = None,
    search: str | None = None,
    assignment: str | None = None,
    target_id: str | None = None,
    conversation_id: str | None = None,
    comment_id: str | None = None,
    filter_agent_id: str | None = None,
    assigned_from: datetime | None = None,
    assigned_to: datetime | None = None,
    offset: int | None = None,
    limit: int | None = None,
    page: int | None = None,
) -> dict:
    page_limit = max(int(limit or 150), 1)
    safe_page = max(int(page or 1), 1)
    safe_offset = max(int(offset or ((safe_page - 1) * page_limit)), 0)
    assigned_person_id = (current_user or {}).get("person_id")
    current_agent_id = get_current_agent_id(db, assigned_person_id) if db else None

    comments_mode = channel == "comments"
    force_refresh_thread = str(query_params.get("reply_sent") or "").strip() == "1"
    missing = str(query_params.get("missing") or "").strip() or None
    comments: list[dict] = []
    selected_comment = None
    comment_replies: list[dict] = []
    conversations: list[dict] = []
    selected_conversation = None
    messages: list[dict] = []
    contact_details = None

    if comments_mode:
        context = await load_comments_context(
            db,
            search=search,
            comment_id=comment_id,
            offset=safe_offset,
            limit=page_limit,
            fetch=True,
            target_id=target_id,
            force_refresh_thread=force_refresh_thread,
        )
        comments = context.grouped_comments
        selected_comment = context.selected_comment
        comment_replies = context.comment_replies
        if conversation_id:
            try:
                conv = conversation_service.Conversations.get(db, conversation_id)
                selected_conversation = format_conversation_for_template(conv, db, include_inbox_label=True)
                if conv.contact:
                    contact_detail_context = build_inbox_contact_detail_context(
                        db,
                        contact_id=str(conv.contact.id),
                        conversation_id=conversation_id,
                        current_user=current_user,
                    )
                    contact_details = (contact_detail_context or {}).get("contact") or format_contact_for_template(
                        conv.contact, db
                    )
            except Exception:
                logger.debug("Failed to format contact details for inbox context.", exc_info=True)

    inbox_timezone, _date_format, time_format, _week_start = resolve_company_time_prefs(db)
    inbox_time_hour12 = "%I" in time_format

    if not comments_mode:
        listing = await load_inbox_list(
            db,
            channel=channel,
            status=status,
            outbox_status=outbox_status,
            search=search,
            assignment=assignment,
            assigned_person_id=assigned_person_id,
            target_id=target_id,
            filter_agent_id=filter_agent_id,
            assigned_from=assigned_from,
            assigned_to=assigned_to,
            missing=missing,
            offset=0,
            limit=page_limit,
            include_thread=False,
            fetch_comments=False,
        )
        conversations = [
            format_conversation_for_template(
                conv,
                db,
                latest_message=latest_message,
                unread_count=unread_count,
                include_inbox_label=True,
            )
            for conv, latest_message, unread_count, _failed_outbox in listing.conversations_raw
        ]
        if outbox_status and str(outbox_status).strip().lower() == "failed":
            for idx, (_conv, _latest_message, _unread_count, failed_outbox) in enumerate(listing.conversations_raw):
                if failed_outbox and idx < len(conversations):
                    conversations[idx]["failed_outbox"] = failed_outbox
        if listing.comment_items:
            conversations = conversations + listing.comment_items

            def _sort_key(item: dict) -> datetime:
                value = item.get("last_message_at")
                if isinstance(value, str) and value:
                    try:
                        parsed = datetime.fromisoformat(value)
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=UTC)
                        return parsed
                    except ValueError:
                        return datetime.min.replace(tzinfo=UTC)
                if isinstance(value, datetime):
                    return value
                return datetime.min.replace(tzinfo=UTC)

            conversations.sort(key=_sort_key, reverse=True)
            conversations = conversations[:page_limit]
        _annotate_conversation_time_labels(db, conversations)
        enrich_formatted_conversations_with_labels(
            db,
            [item for item in conversations if item.get("kind") != "comment"],
        )

        if comment_id:
            comment_context = await load_comments_context(
                db,
                search=search,
                comment_id=comment_id,
                offset=0,
                limit=1,
                fetch=True,
                target_id=target_id,
                include_thread=True,
                force_refresh_thread=force_refresh_thread,
            )
            selected_comment = comment_context.selected_comment
            comment_replies = comment_context.comment_replies

        if conversation_id:
            try:
                current_roles = list((current_user or {}).get("roles") or [])
                detail_context = build_inbox_conversation_detail_context(
                    db,
                    conversation_id=conversation_id,
                    current_user=current_user,
                    current_roles=current_roles,
                )
                if detail_context:
                    selected_conversation = detail_context.get("conversation")
                    messages = detail_context.get("messages") or []
                else:
                    conv = conversation_service.Conversations.get(db, conversation_id)
                    selected_conversation = format_conversation_for_template(conv, db, include_inbox_label=True)
            except Exception:
                logger.debug("Failed to build selected conversation thread for inbox context.", exc_info=True)

    stats, channel_stats = load_inbox_stats(db, timezone=inbox_timezone)
    assignment_counts = get_assignment_counts(db, assigned_person_id=assigned_person_id)

    email_channel = get_email_channel_state(db)
    email_inboxes = list_channel_targets(db, ConnectorType.email)
    whatsapp_inboxes = list_channel_targets(db, ConnectorType.whatsapp)
    facebook_inboxes = list_channel_targets(db, ConnectorType.facebook)
    instagram_inboxes = list_channel_targets(db, ConnectorType.instagram)
    facebook_comment_inboxes, instagram_comment_inboxes = list_comment_inboxes(db)

    assignment_options = crm_service.get_agent_team_options(db)
    templates = []
    if db:
        templates = message_templates.list(
            db,
            channel_type=None,
            is_active=True,
            limit=200,
            offset=0,
        )
    notification_auto_dismiss_seconds = resolve_value(
        db, SettingDomain.notification, "crm_inbox_notification_auto_dismiss_seconds"
    )

    selected_comment_id = None
    if selected_comment is not None:
        selected_comment_id = getattr(selected_comment, "id", None)

    return {
        "current_user": current_user,
        "current_agent_id": current_agent_id,
        "sidebar_stats": sidebar_stats,
        "active_page": "inbox",
        "csrf_token": csrf_token,
        "conversations": conversations,
        "conversations_has_more": listing.has_more if not comments_mode else False,
        "conversations_next_offset": listing.next_offset if not comments_mode else None,
        "conversations_limit": listing.limit if not comments_mode else page_limit,
        "conversations_page": (safe_offset // page_limit) + 1 if not comments_mode else 1,
        "conversations_prev_page": (safe_page - 1) if not comments_mode and safe_page > 1 else None,
        "conversations_next_page": ((safe_offset // page_limit) + 2)
        if not comments_mode and listing.has_more
        else None,
        "comments_has_more": context.has_more if comments_mode else False,
        "comments_next_offset": context.next_offset if comments_mode else None,
        "comments_limit": context.limit if comments_mode else page_limit,
        "comments_page": (safe_offset // page_limit) + 1 if comments_mode else 1,
        "comments_prev_page": (safe_page - 1) if comments_mode and safe_page > 1 else None,
        "comments_next_page": ((safe_offset // page_limit) + 2) if comments_mode and context.has_more else None,
        "pagination_limit": page_limit,
        "selected_conversation": selected_conversation,
        "messages": messages,
        "contact_details": contact_details,
        "comments": comments,
        "selected_comment": selected_comment,
        "selected_comment_id": str(selected_comment_id) if selected_comment_id else None,
        "comment_replies": comment_replies,
        "stats": stats,
        "assignment_counts": assignment_counts,
        "channel_stats": channel_stats,
        "current_channel": channel,
        "current_status": status,
        "current_outbox_status": outbox_status,
        "current_assignment": assignment,
        "current_target_id": target_id,
        "search": search,
        "email_channel": email_channel,
        "email_inboxes": email_inboxes,
        "whatsapp_inboxes": whatsapp_inboxes,
        "facebook_inboxes": facebook_inboxes,
        "instagram_inboxes": instagram_inboxes,
        "facebook_comment_inboxes": facebook_comment_inboxes,
        "instagram_comment_inboxes": instagram_comment_inboxes,
        "email_setup": query_params.get("email_setup"),
        "email_error": query_params.get("email_error"),
        "email_error_detail": query_params.get("email_error_detail"),
        "new_error": query_params.get("new_error"),
        "new_error_detail": query_params.get("new_error_detail"),
        "reply_error": query_params.get("reply_error"),
        "reply_error_detail": query_params.get("reply_error_detail"),
        "agents": assignment_options.get("agents"),
        "teams": assignment_options.get("teams"),
        "agent_labels": assignment_options.get("agent_labels"),
        "current_filter_agent_id": filter_agent_id or "",
        "current_assigned_from": assigned_from.strftime("%Y-%m-%d") if assigned_from else "",
        "current_assigned_to": assigned_to.strftime("%Y-%m-%d") if assigned_to else "",
        "current_missing": missing or "",
        "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
        "notification_auto_dismiss_seconds": notification_auto_dismiss_seconds,
        "inbox_timezone": inbox_timezone,
        "inbox_time_hour12": inbox_time_hour12,
        "message_templates": templates,
        "macros": conversation_macros.list_for_agent(db, str(current_agent_id))
        if current_agent_id
        else conversation_macros.list(db, visibility="shared", is_active=True, limit=200),
    }


async def build_inbox_conversations_partial_context(
    db: Session,
    *,
    channel: str | None = None,
    status: str | None = None,
    outbox_status: str | None = None,
    search: str | None = None,
    assignment: str | None = None,
    assigned_person_id: str | None = None,
    target_id: str | None = None,
    filter_agent_id: str | None = None,
    assigned_from: datetime | None = None,
    assigned_to: datetime | None = None,
    missing: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
    page: int | None = None,
) -> tuple[str, dict]:
    page_limit = max(int(limit or 150), 1)
    safe_page = max(int(page or 1), 1)
    safe_offset = max(int(offset or ((safe_page - 1) * page_limit)), 0)
    listing = await load_inbox_list(
        db,
        channel=channel,
        status=status,
        outbox_status=outbox_status,
        search=search,
        assignment=assignment,
        assigned_person_id=assigned_person_id,
        target_id=target_id,
        filter_agent_id=filter_agent_id,
        assigned_from=assigned_from,
        assigned_to=assigned_to,
        missing=missing,
        offset=safe_offset,
        limit=page_limit,
        include_thread=False,
        fetch_comments=False,
    )
    conversations = [
        format_conversation_for_template(
            conv,
            db,
            latest_message=latest_message,
            unread_count=unread_count,
            include_inbox_label=True,
        )
        for conv, latest_message, unread_count, _failed_outbox in listing.conversations_raw
    ]
    if outbox_status and str(outbox_status).strip().lower() == "failed":
        for idx, (_conv, _latest_message, _unread_count, failed_outbox) in enumerate(listing.conversations_raw):
            if failed_outbox and idx < len(conversations):
                conversations[idx]["failed_outbox"] = failed_outbox
    if listing.comment_items and safe_offset == 0:
        conversations = conversations + listing.comment_items

        def _sort_key(item: dict) -> datetime:
            value = item.get("last_message_at")
            if isinstance(value, str) and value:
                try:
                    parsed = datetime.fromisoformat(value)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=UTC)
                    return parsed
                except ValueError:
                    return datetime.min.replace(tzinfo=UTC)
            if isinstance(value, datetime):
                return value
            return datetime.min.replace(tzinfo=UTC)

        conversations.sort(key=_sort_key, reverse=True)
        conversations = conversations[:page_limit]
    _annotate_conversation_time_labels(db, conversations)
    enrich_formatted_conversations_with_labels(
        db,
        [item for item in conversations if item.get("kind") != "comment"],
    )

    template_name = "admin/crm/_conversation_list_page.html" if safe_offset > 0 else "admin/crm/_conversation_list.html"
    context = {
        "conversations": conversations,
        "current_channel": channel,
        "current_status": status,
        "current_outbox_status": outbox_status,
        "current_assignment": assignment,
        "current_target_id": target_id,
        "current_filter_agent_id": filter_agent_id or "",
        "current_assigned_from": assigned_from.strftime("%Y-%m-%d") if assigned_from else "",
        "current_assigned_to": assigned_to.strftime("%Y-%m-%d") if assigned_to else "",
        "current_missing": missing or "",
        "search": search,
        "conversations_has_more": listing.has_more,
        "conversations_next_offset": listing.next_offset,
        "conversations_limit": listing.limit,
        "conversations_page": (safe_offset // page_limit) + 1,
        "conversations_prev_page": (safe_page - 1) if safe_page > 1 else None,
        "conversations_next_page": (safe_page + 1) if listing.has_more else None,
        "inbox_timezone": resolve_company_time_prefs(db)[0],
        "inbox_time_hour12": "%I" in resolve_company_time_prefs(db)[2],
    }
    return template_name, context


def build_inbox_contact_detail_context(
    db: Session,
    *,
    contact_id: str,
    conversation_id: str | None = None,
    current_user: dict | None = None,
    retention_flash_message: str | None = None,
    retention_error_message: str | None = None,
    open_retention_panel: bool = False,
) -> dict | None:
    try:
        contact_service.Contacts.get(db, contact_id)
        contact = contact_service.get_person_with_relationships(db, contact_id)
    except Exception:
        return None
    if not contact:
        return None

    contact_details = format_contact_for_template(contact, db)
    private_notes: list[dict] = []
    notes_query = (
        db.query(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Conversation.person_id == coerce_uuid(contact_id))
        .filter(Message.channel_type == ChannelType.note)
        .order_by(
            func.coalesce(
                Message.received_at,
                Message.sent_at,
                Message.created_at,
            ).desc()
        )
        .limit(10)
        .all()
    )
    for note in notes_query:
        payload = format_message_for_template(note, db)
        if payload.get("is_private_note"):
            private_notes.append(payload)
        if len(private_notes) >= 5:
            break
    assignment_options = crm_service.get_agent_team_options(db)
    return {
        "contact": contact_details,
        "conversation_id": conversation_id,
        "agents": assignment_options.get("agents"),
        "teams": assignment_options.get("teams"),
        "agent_labels": assignment_options.get("agent_labels"),
        "private_notes": private_notes,
        "retention_card": (
            _conversation_retention_context(
                db,
                conversation_id=conversation_id,
                contact=contact,
                current_user=current_user,
                flash_message=retention_flash_message,
                error_message=retention_error_message,
                open_panel=open_retention_panel,
            )
            if conversation_id
            else None
        ),
    }


def build_inbox_conversation_detail_context(
    db: Session,
    *,
    conversation_id: str,
    current_user: dict | None,
    current_roles: list[str],
) -> dict | None:
    from app.services.crm.inbox.thread import load_conversation_thread

    thread = load_conversation_thread(
        db,
        conversation_id,
        actor_person_id=(current_user or {}).get("person_id"),
        mark_read=True,
    )
    if thread.kind != "success" or not thread.conversation:
        return None

    conversation = format_conversation_for_template(thread.conversation, db, include_inbox_label=True)
    enrich_formatted_conversations_with_labels(db, [conversation])
    messages = [format_message_for_template(m, db) for m in (thread.messages or [])]
    assignment_events, latest_manual_assignment = _load_assignment_activity(
        db,
        conversation_id=conversation_id,
        limit=5,
    )
    csat_event = get_conversation_csat_event(db, conversation_id=conversation_id)
    csat_timestamp = csat_event.timestamp if csat_event and isinstance(csat_event.timestamp, datetime) else None
    if csat_event and csat_timestamp is not None:
        messages.append(
            {
                "id": f"csat-{csat_event.id}",
                "direction": "system",
                "timestamp": csat_timestamp,
                "timestamp_label": _format_inbox_datetime_label(csat_timestamp, db),
                "is_private_note": False,
                "is_csat": True,
                "sender": {"name": "CSAT", "initials": "CS"},
                "content": "Customer satisfaction submitted",
                "csat": {
                    "survey_name": csat_event.survey_name,
                    "rating": csat_event.rating,
                    "feedback": csat_event.feedback,
                },
            }
        )
    if latest_manual_assignment and isinstance(latest_manual_assignment.get("assigned_at"), datetime):
        messages.append(
            {
                "id": f"assignment-{conversation_id}-{latest_manual_assignment['assigned_at'].isoformat()}",
                "direction": "system",
                "timestamp": latest_manual_assignment["assigned_at"],
                "timestamp_label": _format_inbox_datetime_label(latest_manual_assignment["assigned_at"], db),
                "is_private_note": False,
                "is_assignment_event": True,
                "sender": {"name": "Assignment", "initials": "AS"},
                "assignment": {
                    "assigned_by": latest_manual_assignment.get("assigned_by") or "System",
                    "assigned_to": latest_manual_assignment.get("assigned_to") or "Unassigned",
                },
            }
        )
    messages.sort(key=lambda msg: msg["timestamp"].isoformat() if isinstance(msg.get("timestamp"), datetime) else "")
    current_person_id = (current_user or {}).get("person_id")
    current_agent_id = get_current_agent_id(db, current_person_id)
    messages = filter_messages_for_user(
        messages,
        current_person_id,
        current_roles,
    )
    templates_list = message_templates.list(
        db,
        channel_type=None,
        is_active=True,
        limit=200,
        offset=0,
    )
    mention_agents = list_active_agents_for_mentions(db)
    assignment_options = crm_service.get_agent_team_options(db)
    agent_labels = assignment_options.get("agent_labels") or {}
    talk_escalation_recipients: list[dict[str, str]] = []
    seen_person_ids: set[str] = set()
    for agent in assignment_options.get("agents") or []:
        person_id = str(getattr(agent, "person_id", "") or "").strip()
        agent_id = str(getattr(agent, "id", "") or "").strip()
        if not person_id or not agent_id or person_id in seen_person_ids:
            continue
        seen_person_ids.add(person_id)
        talk_escalation_recipients.append(
            {
                "person_id": person_id,
                "label": agent_labels.get(agent_id, "Agent"),
            }
        )
    talk_escalation_recipients.sort(key=lambda item: item.get("label", "").lower())
    macros = (
        conversation_macros.list_for_agent(db, str(current_agent_id))
        if current_agent_id
        else conversation_macros.list(db, visibility="shared", is_active=True, limit=200)
    )
    raw_metadata = thread.conversation.metadata_ if isinstance(thread.conversation.metadata_, dict) else {}
    raw_attribution_value = raw_metadata.get("attribution")
    raw_attribution = raw_attribution_value if isinstance(raw_attribution_value, dict) else {}
    conversation_attribution = {
        key: value
        for key in ("source", "ad_id", "campaign_id", "ctwa_clid", "source_url")
        if (value := raw_attribution.get(key)) not in (None, "")
    } or None
    return {
        "conversation": conversation,
        "conversation_attribution": conversation_attribution,
        "messages": messages,
        "current_user": current_user,
        "current_agent_id": current_agent_id,
        "current_roles": current_roles,
        "message_templates": templates_list,
        "mention_agents": mention_agents,
        "talk_escalation_recipients": talk_escalation_recipients,
        "assignment_events": assignment_events,
        "latest_manual_assignment": latest_manual_assignment,
        "macros": macros,
    }
