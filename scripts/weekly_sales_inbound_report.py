#!/usr/bin/env python3
"""Generate the read-only Weekly Sales Inbound Experience Report.

The utility discovers active CRM inboxes, selects every conversation with an
inbound message in the previous complete Africa/Lagos week, loads each complete
message history, classifies genuine buying intent, validates all reconciliations,
and writes Markdown and PDF artifacts. It deliberately uses a PostgreSQL
read-only transaction and never calls a mutating CRM service.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db import SessionLocal

LAGOS = ZoneInfo("Africa/Lagos")
REPORT_NAME = "Weekly_Sales_Inbound_Experience_Report"

EXPLICIT_SALES_RE = re.compile(
    r"\b(?:new (?:connection|service|installation|customer)|get(?:ting)? connected|"
    r"install(?:ation)? (?:fee|cost|process|details)|install (?:fiber|fibre|internet)|"
    r"fiber installed|fibre installed|(?:internet|fiber|fibre) (?:service )?(?:for|in) (?:my|our) (?:home|house|apartment|office)|"
    r"(?:do you|does your|is (?:fiber|fibre|service)|have) (?:have )?(?:fiber |fibre |service )?coverage|"
    r"coverage (?:in|at|reach|available)|available (?:in|at|for) (?:my|our|this)|"
    r"want to (?:get|be) connected|need (?:your )?(?:internet )?service (?:at|in|for)|"
    r"interested in (?:getting|your|internet|fiber|fibre)|"
    r"(?:business|residential|enterprise) (?:fiber|fibre|internet|service|plans?)|"
    r"(?:price|pricing|cost|how much|quotation|quote|proposal).{0,90}(?:install|internet|fiber|fibre|service|package|plan|bandwidth)|"
    r"(?:package|plan|speed|bandwidth) (?:details|options|pricing)|"
    r"(?:packages?|plans?).{0,50}(?:coverage|available|price|pricing|cost|speed)|"
    r"(?:upgrade|change) (?:my|our|the)? ?(?:package|plan|speed|bandwidth)|"
    r"outdoor wi-?fi (?:zone )?(?:solution|setup)|fiber lease line|leased line|server rental)\b",
    re.I | re.S,
)
INBOUND_SOLICITATION_RE = re.compile(
    r"\b(?:manufacturer|supplier|our catalog|our catalogue|we (?:supply|manufacture|sell)|"
    r"business partnership|possible cooperation|visit your company|procurement department|stock availability|"
    r"hot sale|available stock|ready for dispatch)\b",
    re.I,
)
NON_CUSTOMER_APPLICATION_RE = re.compile(
    r"\b(?:siwes|internship|training program|academy|job|vacancy|career|application process|fiber lecturer)\b",
    re.I,
)
COMMERCIAL_OVERRIDE_RE = re.compile(
    r"\b(?:new (?:connection|service|installation|customer|link)|want to (?:get|be) connected|"
    r"install (?:fiber|fibre|internet)|(?:fiber|fibre|internet) (?:service )?for (?:my|our) (?:home|house|apartment|office)|"
    r"(?:do you|does your|have) (?:have )?(?:fiber |fibre |service )?coverage|"
    r"looking for outdoor wi-?fi|(?:business|residential|enterprise) (?:fiber|fibre|internet)|"
    r"(?:upgrade|change) (?:my|our|the)? ?(?:package|plan|speed|bandwidth)|"
    r"shared and dedicated plans?|details for both)\b",
    re.I,
)

SALES_CONTEXT_RE = re.compile(
    r"\b(?:install(?:ation)?|new connection|coverage|available|availability|price|pricing|cost|quote|quotation|"
    r"proposal|package|plan|subscribe|subscription|upgrade|bandwidth|mbps|dedicated|leased line|fiber)\b",
    re.I,
)
SUPPORT_RE = re.compile(
    r"\b(?:down|downtime|outage|not working|no (?:network|internet|service)|slow|fluctuat|disconnect|"
    r"red light|amber light|cannot browse|can't browse|ticket|refund|compensation|password|portal|"
    r"restore|fault|issue|problem|complaint|test|kindly ignore|renew|recharge|relocat(?:e|ion)|expired?|current subscription|"
    r"temporary suspension|invoice|not used (?:my|the|this)? ?internet)\b",
    re.I,
)
FOLLOW_UP_RE = re.compile(r"\b(?:follow[ -]?up|following up|any update|still waiting|revert|remind|status)\b", re.I)

INTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Requested Quotation", re.compile(r"\b(?:quote|quotation|proposal|proforma)\b", re.I)),
    ("Requested Pricing", re.compile(r"\b(?:price|pricing|cost|how much|rate|tariff|charges?)\b", re.I)),
    (
        "Competitor Comparison",
        re.compile(r"\b(?:compare|comparison|versus|vs\.?|other provider|another isp|competitor)\b", re.I),
    ),
    ("Discount Enquiry", re.compile(r"\b(?:discount|promo|promotion|offer|cheaper)\b", re.I)),
    ("Follow-up Enquiry", FOLLOW_UP_RE),
    (
        "Requested Consultation",
        re.compile(r"\b(?:site survey|survey|coverage|availability|available in|call me|recommend|consult)\b", re.I),
    ),
    (
        "Ready to Purchase",
        re.compile(
            r"\b(?:ready to|proceed|install immediately|install as soon as possible|want to (?:get|be) connected|new connection)\b",
            re.I,
        ),
    ),
    (
        "Requested Product Information",
        re.compile(r"\b(?:package|plan|speed|bandwidth|mbps|unlimited|fiber|router|dedicated|leased line)\b", re.I),
    ),
)

POSITIVE_RE = re.compile(r"\b(?:great|excellent|perfect|interested|happy|glad|good to hear|proceed|satisfied)\b", re.I)
NEGATIVE_RE = re.compile(
    r"\b(?:bad|poor|terrible|frustrat|angry|disappoint|expensive|costly|delay|waiting|unhappy|cancel|not interested|no response)\b",
    re.I,
)

OBJECTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Price or affordability",
        re.compile(r"\b(?:expensive|costly|too much|afford|cheaper|discount|price is high)\b", re.I),
    ),
    (
        "Coverage uncertainty",
        re.compile(r"\b(?:no coverage|not available|availability|coverage|do you cover|available in)\b", re.I),
    ),
    (
        "Installation timing or delay",
        re.compile(r"\b(?:delay|waiting|how long|timeline|when.*install|installation time)\b", re.I | re.S),
    ),
    (
        "Service reliability concerns",
        re.compile(r"\b(?:unstable|unreliable|downtime|outage|poor network|bad service|fluctuat)\b", re.I),
    ),
    (
        "Payment or portal friction",
        re.compile(r"\b(?:cannot pay|can't pay|unable to pay|portal.*down|site.*down|payment.*fail)\b", re.I | re.S),
    ),
    (
        "Commitment or timing",
        re.compile(r"\b(?:later|not now|postpone|next month|still considering|think about)\b", re.I),
    ),
    (
        "Competitor preference",
        re.compile(r"\b(?:another provider|other isp|competitor|starlink|spectranet|ipnx)\b", re.I),
    ),
)

INTEREST_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Fiber installation", re.compile(r"\b(?:fiber|fibre|installation|new connection)\b", re.I)),
    ("Residential internet", re.compile(r"\b(?:home|house|apartment|residential|estate)\b", re.I)),
    (
        "Business/dedicated connectivity",
        re.compile(r"\b(?:business|office|dedicated|leased line|bandwidth|mpls)\b", re.I),
    ),
    ("Packages and speeds", re.compile(r"\b(?:package|plan|speed|mbps|unlimited)\b", re.I)),
    ("Coverage checks", re.compile(r"\b(?:coverage|availability|available in|cover my)\b", re.I)),
    ("Upgrades", re.compile(r"\b(?:upgrade|increase.*(?:speed|bandwidth|plan))\b", re.I | re.S)),
)


def _clean_body(body: str, channel_type: str) -> str:
    value = body.replace("\r\n", "\n").replace("\r", "\n")
    if channel_type == "email":
        value = re.sub(r"(?is)<(?:style|script)\b.*?</(?:style|script)>", " ", value)
        value = re.sub(r"(?s)<[^>]+>", " ", value)
        value = html.unescape(value)
        quote_markers = (
            r"\n\s*On .{0,240}?wrote:\s*",
            r"\n\s*From:\s*",
            r"\n\s*-{2,}\s*Original message\s*-{2,}",
            r"\n\s*-{2,}\s*Forwarded message\s*-{2,}",
        )
        positions = []
        for marker in quote_markers:
            match = re.search(marker, value, re.I | re.S)
            if match:
                positions.append(match.start())
        if positions:
            value = value[: min(positions)]
        value = "\n".join(line for line in value.splitlines() if not line.lstrip().startswith(">"))
    return re.sub(r"\s+", " ", value).strip()


@dataclass
class MessageRow:
    id: str
    conversation_id: str
    direction: str
    channel_type: str
    channel_target_id: str | None
    author_id: str | None
    body: str
    subject: str
    occurred_at: datetime
    metadata: dict[str, Any]

    @property
    def text(self) -> str:
        return f"{self.subject} {self.body}".strip()

    @property
    def cleaned_text(self) -> str:
        return f"{self.subject} {_clean_body(self.body, self.channel_type)}".strip()

    @property
    def is_ai_generated(self) -> bool:
        return bool(self.metadata.get("ai_intake_generated"))


@dataclass
class ConversationReview:
    id: str
    person_id: str
    party_status: str
    customer_type: str
    status: str
    created_at: datetime
    metadata: dict[str, Any]
    tags: set[str]
    history: list[MessageRow]
    window_inbound: list[MessageRow]
    primary_inbox_id: str | None
    primary_inbox_name: str
    is_sales: bool = False
    sales_basis: str = ""
    intent: str = ""
    outcome: str = ""
    sentiment: str = ""
    evidence: str = ""
    handling_agent: str = "Unassigned"
    response_seconds: int | None = None
    human_outbound_count: int = 0
    objection: str | None = None
    missed_opportunity: str | None = None
    recommended_follow_up: str | None = None
    interests: list[str] = field(default_factory=list)

    @property
    def customer_window_text(self) -> str:
        return "\n".join(message.cleaned_text for message in self.window_inbound if message.cleaned_text)


def previous_complete_week(now: datetime | None = None) -> tuple[datetime, datetime, datetime, datetime]:
    local_now = (now or datetime.now(UTC)).astimezone(LAGOS)
    current_monday = local_now.date() - timedelta(days=local_now.weekday())
    start_local = datetime.combine(current_monday - timedelta(days=7), time.min, tzinfo=LAGOS)
    end_exclusive_local = start_local + timedelta(days=7)
    return start_local, end_exclusive_local, start_local.astimezone(UTC), end_exclusive_local.astimezone(UTC)


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _safe_metadata(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _query_all(db, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(row._mapping) for row in db.execute(text(sql), params or {})]


def collect_data(db, start_utc: datetime, end_utc: datetime) -> dict[str, Any]:
    active_inboxes = _query_all(
        db,
        """
        SELECT it.id::text AS id, it.name, cc.connector_type::text AS connector_type
        FROM integration_targets it
        JOIN connector_configs cc ON cc.id = it.connector_config_id
        WHERE it.target_type = 'crm'
          AND it.is_active IS TRUE
          AND cc.is_active IS TRUE
        ORDER BY lower(it.name), it.id
        """,
    )
    window_rows = _query_all(
        db,
        """
        SELECT DISTINCT m.conversation_id::text AS conversation_id
        FROM crm_messages m
        WHERE m.direction = 'inbound'
          AND COALESCE(m.received_at, m.sent_at, m.created_at) >= :start_at
          AND COALESCE(m.received_at, m.sent_at, m.created_at) < :end_at
        ORDER BY m.conversation_id::text
        """,
        {"start_at": start_utc, "end_at": end_utc},
    )
    conversation_ids = [row["conversation_id"] for row in window_rows]
    if not conversation_ids:
        return {
            "active_inboxes": active_inboxes,
            "conversations": [],
            "messages": [],
            "tags": [],
            "assignments": [],
            "agents": [],
            "leads": [],
            "orders": [],
            "quotes": [],
            "attachments": [],
        }

    params = {"conversation_ids": conversation_ids, "start_at": start_utc, "end_at": end_utc}
    conversations = _query_all(
        db,
        """
        SELECT c.id::text AS id, c.person_id::text AS person_id, c.status::text AS status,
               c.created_at, c.metadata, p.party_status::text AS party_status,
               p.erp_customer_id, p.erpnext_id, p.metadata AS person_metadata
        FROM crm_conversations c
        JOIN people p ON p.id = c.person_id
        WHERE c.id::text = ANY(:conversation_ids)
        """,
        params,
    )
    messages = _query_all(
        db,
        """
        SELECT m.id::text AS id, m.conversation_id::text AS conversation_id,
               m.direction::text AS direction, m.channel_type::text AS channel_type,
               m.channel_target_id::text AS channel_target_id, m.author_id::text AS author_id,
               COALESCE(m.body, '') AS body, COALESCE(m.subject, '') AS subject,
               COALESCE(m.received_at, m.sent_at, m.created_at) AS occurred_at,
               m.metadata
        FROM crm_messages m
        WHERE m.conversation_id::text = ANY(:conversation_ids)
        ORDER BY m.conversation_id, COALESCE(m.received_at, m.sent_at, m.created_at), m.id
        """,
        params,
    )
    tags = _query_all(
        db,
        "SELECT conversation_id::text AS conversation_id, tag FROM crm_conversation_tags WHERE conversation_id::text = ANY(:conversation_ids)",
        params,
    )
    assignments = _query_all(
        db,
        """
        SELECT conversation_id::text AS conversation_id, agent_id::text AS agent_id,
               assigned_at, created_at, is_active
        FROM crm_conversation_assignments
        WHERE conversation_id::text = ANY(:conversation_ids)
        ORDER BY conversation_id, COALESCE(assigned_at, created_at), id
        """,
        params,
    )
    agents = _query_all(
        db,
        """
        SELECT a.id::text AS agent_id, a.person_id::text AS person_id,
               COALESCE(NULLIF(p.display_name, ''), trim(p.first_name || ' ' || p.last_name)) AS name
        FROM crm_agents a JOIN people p ON p.id = a.person_id
        """,
    )
    person_ids = sorted({row["person_id"] for row in conversations})
    people_params = {"person_ids": person_ids}
    leads = _query_all(
        db,
        "SELECT person_id::text AS person_id, id::text AS id, status::text AS status, created_at FROM crm_leads WHERE person_id::text = ANY(:person_ids) AND is_active IS TRUE",
        people_params,
    )
    orders = _query_all(
        db,
        """
        SELECT person_id::text AS person_id, id::text AS id, status::text AS status,
               payment_status::text AS payment_status, created_at, paid_at
        FROM sales_orders WHERE person_id::text = ANY(:person_ids) AND is_active IS TRUE
        """,
        people_params,
    )
    quotes = _query_all(
        db,
        """
        SELECT person_id::text AS person_id, id::text AS id, status::text AS status,
               created_at, sent_at
        FROM crm_quotes WHERE person_id::text = ANY(:person_ids) AND is_active IS TRUE
        """,
        people_params,
    )
    attachments = _query_all(
        db,
        """
        SELECT a.message_id::text AS message_id, a.mime_type
        FROM crm_message_attachments a
        JOIN crm_messages m ON m.id = a.message_id
        WHERE m.conversation_id::text = ANY(:conversation_ids)
        """,
        params,
    )
    return {
        "active_inboxes": active_inboxes,
        "conversations": conversations,
        "messages": messages,
        "tags": tags,
        "assignments": assignments,
        "agents": agents,
        "leads": leads,
        "orders": orders,
        "quotes": quotes,
        "attachments": attachments,
    }


def customer_type_for(row: dict[str, Any], lead_people: set[str], order_people: set[str]) -> str:
    person_metadata = _safe_metadata(row.get("person_metadata"))
    customer_markers = (
        row.get("party_status") in {"customer", "subscriber"}
        or bool(row.get("erp_customer_id"))
        or bool(row.get("erpnext_id"))
        or bool(person_metadata.get("selfcare_id") or person_metadata.get("splynx_id"))
        or row["person_id"] in order_people
    )
    if customer_markers:
        return "Existing customer"
    if row.get("party_status") == "lead" or row["person_id"] in lead_people:
        return "New lead"
    return "Unknown"


def build_reviews(data: dict[str, Any], start_utc: datetime, end_utc: datetime) -> list[ConversationReview]:
    inbox_names = {row["id"]: row["name"] for row in data["active_inboxes"]}
    messages_by_conversation: dict[str, list[MessageRow]] = defaultdict(list)
    for row in data["messages"]:
        messages_by_conversation[row["conversation_id"]].append(
            MessageRow(
                id=row["id"],
                conversation_id=row["conversation_id"],
                direction=row["direction"],
                channel_type=row["channel_type"],
                channel_target_id=row.get("channel_target_id"),
                author_id=row.get("author_id"),
                body=row.get("body") or "",
                subject=row.get("subject") or "",
                occurred_at=_coerce_datetime(row["occurred_at"]),
                metadata=_safe_metadata(row.get("metadata")),
            )
        )
    tags_by_conversation: dict[str, set[str]] = defaultdict(set)
    for row in data["tags"]:
        tags_by_conversation[row["conversation_id"]].add(str(row["tag"]).strip().lower())
    lead_people = {row["person_id"] for row in data["leads"]}
    order_people = {row["person_id"] for row in data["orders"]}

    reviews: list[ConversationReview] = []
    for row in data["conversations"]:
        history = messages_by_conversation[row["id"]]
        window_inbound = [
            message
            for message in history
            if message.direction == "inbound" and start_utc <= message.occurred_at < end_utc
        ]
        first = window_inbound[0]
        target_id = first.channel_target_id
        if target_id in inbox_names:
            inbox_name = inbox_names[target_id]
        elif target_id:
            inbox_name = f"Inactive/unconfigured {first.channel_type} inbox"
        else:
            inbox_name = f"Unconfigured {first.channel_type.replace('_', ' ').title()}"
        reviews.append(
            ConversationReview(
                id=row["id"],
                person_id=row["person_id"],
                party_status=row.get("party_status") or "unknown",
                customer_type=customer_type_for(row, lead_people, order_people),
                status=row.get("status") or "unknown",
                created_at=_coerce_datetime(row["created_at"]),
                metadata=_safe_metadata(row.get("metadata")),
                tags=tags_by_conversation[row["id"]],
                history=history,
                window_inbound=window_inbound,
                primary_inbox_id=target_id,
                primary_inbox_name=inbox_name,
            )
        )
    return sorted(reviews, key=lambda review: review.id)


def _recent_context(review: ConversationReview, start_utc: datetime, end_utc: datetime) -> str:
    context_start = start_utc - timedelta(days=30)
    context_end = end_utc + timedelta(days=7)
    return "\n".join(
        message.cleaned_text
        for message in review.history
        if context_start <= message.occurred_at < context_end and message.cleaned_text
    )


def sales_basis(review: ConversationReview, start_utc: datetime, end_utc: datetime) -> tuple[bool, str]:
    window_text = review.customer_window_text
    recent_text = _recent_context(review, start_utc, end_utc)
    explicit_sales = bool(EXPLICIT_SALES_RE.search(window_text))
    sales_context = bool(SALES_CONTEXT_RE.search(window_text))
    support_context = bool(SUPPORT_RE.search(window_text))
    ai_state = _safe_metadata(review.metadata.get("ai_intake"))
    ai_sales = str(ai_state.get("department") or "").lower() == "sales"
    sales_tag = "sales" in review.tags

    if INBOUND_SOLICITATION_RE.search(window_text):
        return False, "Inbound supplier solicitation, not customer buying intent"
    if NON_CUSTOMER_APPLICATION_RE.search(window_text):
        return False, "Employment, training, or application enquiry, not customer buying intent"
    if re.search(
        r"\b(?:subscribe to (?:my|our|the) account|payment for subscription|confirm payment for subscription|"
        r"website.{0,40}not working.{0,80}subscri)\b",
        window_text,
        re.I | re.S,
    ) and not re.search(
        r"\b(?:new (?:connection|service|account)|upgrade|change (?:the |my |our )?(?:package|plan))\b",
        window_text,
        re.I,
    ):
        return False, "Existing-account subscription or payment support, not a new sales opportunity"
    if explicit_sales and (not support_context or COMMERCIAL_OVERRIDE_RE.search(window_text)):
        return True, "Explicit buying language in the reporting window"
    if ai_sales and sales_context and not support_context:
        return True, "CRM sales-intake classification corroborated by current sales language"
    if sales_tag and sales_context and not support_context:
        return True, "CRM sales tag corroborated by current sales language"
    if (
        (ai_sales or sales_tag)
        and not support_context
        and FOLLOW_UP_RE.search(window_text)
        and SALES_CONTEXT_RE.search(recent_text)
    ):
        return True, "Current follow-up on a recent sales request"
    return False, "No current genuine buying intent found"


def classify_intent(text_value: str) -> str:
    for name, pattern in INTENT_RULES:
        if pattern.search(text_value):
            return name
    return "Other"


def paraphrased_evidence(text_value: str, intent: str) -> str:
    evidence = {
        "Ready to Purchase": "Customer expressed a desire to proceed with a new service, purchase, or installation.",
        "Requested Pricing": "Customer requested pricing, charges, or cost details for the service.",
        "Requested Quotation": "Customer requested a quotation or commercial proposal.",
        "Requested Consultation": "Customer requested a coverage check, survey, recommendation, or consultation.",
        "Requested Product Information": "Customer asked about packages, speeds, bandwidth, or product details.",
        "Competitor Comparison": "Customer compared the offer with another provider or alternative.",
        "Discount Enquiry": "Customer asked about a discount, promotion, or lower price.",
        "Follow-up Enquiry": "Customer followed up on an earlier sales request or pending response.",
        "Other": "Conversation contained explicit buying intent that did not fit a more specific intent category.",
    }
    if re.search(r"\bupgrade\b", text_value, re.I):
        return "Customer asked to upgrade an existing package, speed, or bandwidth."
    return evidence[intent]


def classify_sentiment(text_value: str) -> str:
    positive = len(POSITIVE_RE.findall(text_value))
    negative = len(NEGATIVE_RE.findall(text_value))
    if negative > positive:
        return "Negative"
    if positive > negative and positive > 0:
        return "Positive"
    return "Neutral"


def classify_outcome(
    review: ConversationReview,
    agent_person_ids: set[str],
    as_of: datetime,
) -> tuple[str, str | None, str | None]:
    customer_text = review.customer_window_text
    first_inbound_at = review.window_inbound[0].occurred_at
    human_outbound = [
        message
        for message in review.history
        if message.direction == "outbound"
        and message.author_id in agent_person_ids
        and not message.is_ai_generated
        and message.occurred_at >= first_inbound_at
    ]
    agent_text = "\n".join(message.cleaned_text for message in human_outbound if message.cleaned_text)
    outcome_context = f"{customer_text}\n{agent_text}"
    if re.search(
        r"\b(?:installation (?:is |was )?complete|service (?:is |was )?(?:installed|activated)|"
        r"connection (?:is |was )?installed|installed and working|up and running after installation)\b",
        customer_text,
        re.I,
    ):
        return "Sale Completed", None, None
    if re.search(
        r"\b(?:payment received|i (?:have )?paid|payment receipt|receipt for payment|proof of payment|transferred)\b",
        customer_text,
        re.I,
    ):
        return "Payment Received", None, None
    if re.search(
        r"\b(?:meeting|site survey|appointment)\b.{0,60}\b(?:booked|scheduled|confirmed|tomorrow|monday|tuesday|wednesday|thursday|friday)\b",
        outcome_context,
        re.I | re.S,
    ):
        return "Meeting Booked", None, None
    if re.search(
        r"\b(?:will pay|i'll pay|i will make payment|i'll make payment|transfer shortly|payment tomorrow)\b",
        customer_text,
        re.I,
    ):
        return "Payment Promised", None, None
    if re.search(
        r"\b(?:not interested|no longer interested|not be continuing|reject|decline|too expensive|way above my budget|chose another)\b",
        customer_text,
        re.I,
    ):
        return (
            "Customer Rejected Offer",
            "Customer declined or rejected the offer.",
            "Confirm the reason, record it, and offer the closest-fit alternative once.",
        )
    if re.search(
        r"\b(?:postpone|not now|maybe later|next month|hold on|still considering|"
        r"will (?:get back|revert|communicate)|get back to you)\b",
        customer_text,
        re.I,
    ):
        return (
            "Customer Postponed Purchase",
            "Customer deferred the buying decision.",
            "Agree a dated follow-up and restate the value most relevant to the customer.",
        )

    last_customer = max(
        (message.occurred_at for message in review.history if message.direction == "inbound"), default=None
    )
    last_human = max((message.occurred_at for message in human_outbound), default=None)
    if last_human and (not last_customer or last_human > last_customer) and as_of - last_human >= timedelta(hours=24):
        return (
            "Customer Stopped Responding",
            "Customer did not respond after the agent's latest sales reply.",
            "Send a concise value-led follow-up with one clear next step and a response deadline.",
        )
    if re.search(
        r"\b(?:will revert|send.*quote|share.*quote|call you|follow up|check and revert|schedule)\b",
        agent_text,
        re.I | re.S,
    ):
        return "Follow-up Scheduled", None, None
    if not human_outbound:
        return (
            "Opportunity Still Active",
            "No attributable human sales response was found after the inbound request.",
            "Assign an owner immediately and respond with pricing or the next qualification question.",
        )
    return "Opportunity Still Active", None, None


def attribute_agents(reviews: list[ConversationReview], data: dict[str, Any]) -> None:
    agent_name_by_id = {row["agent_id"]: row["name"] for row in data["agents"]}
    agent_id_by_person = {row["person_id"]: row["agent_id"] for row in data["agents"]}
    assignments: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data["assignments"]:
        assignments[row["conversation_id"]].append(row)

    for review in reviews:
        rows = assignments.get(review.id, [])
        active_agent = next(
            (row.get("agent_id") for row in reversed(rows) if row.get("is_active") and row.get("agent_id")), None
        )
        responders = Counter(
            agent_id_by_person[message.author_id]
            for message in review.history
            if message.direction == "outbound"
            and message.author_id in agent_id_by_person
            and not message.is_ai_generated
        )
        primary_responder = responders.most_common(1)[0][0] if responders else None
        historical_agent = next((row.get("agent_id") for row in reversed(rows) if row.get("agent_id")), None)
        agent_id = active_agent or primary_responder or historical_agent
        review.handling_agent = agent_name_by_id.get(agent_id, "Unassigned")
        review.human_outbound_count = sum(responders.values())

        first_inbound = review.window_inbound[0].occurred_at
        response_times = [
            message.occurred_at
            for message in review.history
            if message.direction == "outbound"
            and message.author_id in agent_id_by_person
            and not message.is_ai_generated
            and message.occurred_at >= first_inbound
        ]
        if response_times:
            review.response_seconds = max(0, int((min(response_times) - first_inbound).total_seconds()))


def classify_reviews(
    reviews: list[ConversationReview],
    data: dict[str, Any],
    start_utc: datetime,
    end_utc: datetime,
    as_of: datetime,
) -> None:
    attribute_agents(reviews, data)
    agent_person_ids = {row["person_id"] for row in data["agents"]}

    for review in reviews:
        review.is_sales, review.sales_basis = sales_basis(review, start_utc, end_utc)
        if not review.is_sales:
            continue
        text_value = review.customer_window_text
        if re.search(
            r"\b(?:our current plan|we are on currently|my account|our account|account name|user[ -]?id|"
            r"renew(?:al|ing)?|my subscription|our subscription|relocat(?:e|ion) (?:my|our)|already (?:have|using))\b",
            text_value,
            re.I,
        ):
            review.customer_type = "Existing customer"
        elif review.customer_type == "Unknown" and re.search(
            r"\b(?:new customer|new connection|do not have an account|don't have an account)\b", text_value, re.I
        ):
            review.customer_type = "New lead"
        review.intent = classify_intent(text_value)
        review.evidence = paraphrased_evidence(text_value, review.intent)
        review.sentiment = classify_sentiment(text_value)
        review.outcome, review.missed_opportunity, review.recommended_follow_up = classify_outcome(
            review,
            agent_person_ids,
            as_of,
        )
        for objection, pattern in OBJECTION_RULES:
            if pattern.search(text_value):
                review.objection = objection
                break
        review.interests = [name for name, pattern in INTEREST_RULES if pattern.search(text_value)]


def validate(
    reviews: list[ConversationReview],
    active_inboxes: list[dict[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    if any(not review.window_inbound for review in reviews):
        errors.append("At least one selected conversation has no inbound message in the reporting window.")
    if len({review.id for review in reviews}) != len(reviews):
        errors.append("Conversation IDs are not unique in the reviewed population.")
    sales = [review for review in reviews if review.is_sales]
    non_sales = [review for review in reviews if not review.is_sales]
    if len(sales) + len(non_sales) != len(reviews):
        errors.append("Sales and non-sales totals do not reconcile to reviewed conversations.")
    for label, values in (
        ("intent", [review.intent for review in sales]),
        ("outcome", [review.outcome for review in sales]),
        ("sentiment", [review.sentiment for review in sales]),
        ("agent", [review.handling_agent for review in sales]),
    ):
        if any(not value for value in values):
            errors.append(f"At least one sales conversation has no {label} classification.")
        if sum(Counter(values).values()) != len(sales):
            errors.append(f"{label.title()} totals do not reconcile to sales conversations.")
    inbox_counts = Counter(review.primary_inbox_name for review in reviews)
    if sum(inbox_counts.values()) != len(reviews):
        errors.append("Inbox totals do not reconcile to reviewed conversations.")
    active_ids = {row["id"] for row in active_inboxes}
    represented_active_ids = {review.primary_inbox_id for review in reviews if review.primary_inbox_id in active_ids}
    checked_active_ids = represented_active_ids | {
        row["id"] for row in active_inboxes if not any(review.primary_inbox_id == row["id"] for review in reviews)
    }
    if checked_active_ids != active_ids:
        errors.append("Not every active inbox was included in the coverage check.")
    if errors:
        raise ValueError("Validation failed:\n- " + "\n- ".join(errors))
    return {
        "reviewed": len(reviews),
        "sales": len(sales),
        "intent_total": sum(Counter(review.intent for review in sales).values()),
        "outcome_total": sum(Counter(review.outcome for review in sales).values()),
        "sentiment_total": sum(Counter(review.sentiment for review in sales).values()),
        "agent_total": sum(Counter(review.handling_agent for review in sales).values()),
        "active_inboxes": len(active_inboxes),
    }


def pct(value: int, total: int) -> str:
    return f"{(100 * value / total):.1f}%" if total else "0.0%"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    def clean(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").strip()

    header = "| " + " | ".join(clean(value) for value in headers) + " |"
    divider = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(clean(value) for value in row) + " |" for row in rows]
    return "\n".join([header, divider, *body])


def _representative(reviews: list[ConversationReview], limit: int = 3) -> str:
    return "; ".join(f"{review.id}: {review.evidence}" for review in reviews[:limit]) or "No examples"


def build_agent_rows(sales: list[ConversationReview]) -> list[list[str]]:
    grouped: dict[str, list[ConversationReview]] = defaultdict(list)
    for review in sales:
        grouped[review.handling_agent].append(review)
    rows: list[list[str]] = []
    for agent, items in sorted(grouped.items()):
        responded = [item for item in items if item.human_outbound_count]
        fast = [item for item in items if item.response_seconds is not None and item.response_seconds <= 3600]
        missed = [
            item
            for item in items
            if item.outcome in {"Customer Stopped Responding", "Customer Rejected Offer"}
            or (item.missed_opportunity and not item.human_outbound_count)
        ]
        completed = [item for item in items if item.outcome in {"Sale Completed", "Payment Received"}]
        if completed:
            strengths = f"Conversion/payment evidence in {len(completed)} conversation(s); maintained ownership through a commercial outcome."
            positive = _representative(completed, 2)
        elif responded:
            strengths = f"Provided a human response in {len(responded)} of {len(items)} attributed conversation(s)."
            positive = _representative(responded, 2)
        else:
            strengths = "No evidence-supported handling strength could be established in this period."
            positive = "No attributable human outbound response was found."
        if missed:
            weaknesses = f"{len(missed)} conversation(s) lacked a clear response or reached a lost/at-risk state."
            missed_text = "; ".join(f"{item.id}: {item.missed_opportunity}" for item in missed[:3])
            improvement = "Use a same-day response SLA, qualify location/use case/budget, and end every reply with one dated next step."
            coaching = f"Review {', '.join(item.id for item in missed[:3])}; practise concise value-led follow-ups and explicit next-step confirmation."
        else:
            weaknesses = "No material missed opportunity was evidenced in the reviewed conversations."
            missed_text = "None evidenced."
            improvement = "Continue confirming a dated next step and recording the commercial outcome."
            coaching = "Reinforce discovery questions and explicit close/next-step language using successful conversations from this period."
        if fast:
            strengths += f" {len(fast)} initial response(s) were within one hour."
        rows.append(
            [
                agent,
                str(len(items)),
                strengths,
                weaknesses,
                positive,
                missed_text,
                improvement,
                coaching,
            ]
        )
    return rows


def build_markdown(
    reviews: list[ConversationReview],
    active_inboxes: list[dict[str, Any]],
    start_local: datetime,
    end_exclusive_local: datetime,
    warnings: list[str],
) -> str:
    sales = [review for review in reviews if review.is_sales]
    period = f"{start_local:%d %B %Y} - {(end_exclusive_local - timedelta(seconds=1)):%d %B %Y}"
    intent_groups: dict[str, list[ConversationReview]] = defaultdict(list)
    outcome_groups: dict[str, list[ConversationReview]] = defaultdict(list)
    sentiment_groups: dict[str, list[ConversationReview]] = defaultdict(list)
    for review in sales:
        intent_groups[review.intent].append(review)
        outcome_groups[review.outcome].append(review)
        sentiment_groups[review.sentiment].append(review)
    inbox_counts = Counter(review.primary_inbox_name for review in reviews)
    agent_counts = Counter(review.handling_agent for review in sales)
    active_names = {row["name"] for row in active_inboxes}
    inbox_rows = [[row["name"], inbox_counts.get(row["name"], 0)] for row in active_inboxes]
    inbox_rows.extend([list(item) for item in sorted(inbox_counts.items()) if item[0] not in active_names])
    unique_leads = len({review.person_id for review in sales if review.customer_type == "New lead"})
    existing_enquiries = sum(review.customer_type == "Existing customer" for review in sales)
    unknown_enquiries = sum(review.customer_type == "Unknown" for review in sales)

    objections: dict[str, list[ConversationReview]] = defaultdict(list)
    interests: Counter[str] = Counter()
    for review in sales:
        if review.objection:
            objections[review.objection].append(review)
        interests.update(review.interests)
    lost = [review for review in sales if review.missed_opportunity]
    no_response = sum(not review.human_outbound_count for review in sales)
    stopped = sum(review.outcome == "Customer Stopped Responding" for review in sales)
    followups = sum(review.outcome in {"Follow-up Scheduled", "Meeting Booked"} for review in sales)
    successful = sum(review.outcome in {"Sale Completed", "Payment Received"} for review in sales)
    upgrade_count = sum("Upgrades" in review.interests for review in sales)
    negative = len(sentiment_groups.get("Negative", []))

    lines = [
        f"# {REPORT_NAME.replace('_', ' ')}",
        "",
        f"**Reporting period:** {period}",
        "",
        "**Scope:** Sales inbound experience only — previous complete week, Africa/Lagos timezone",
        "",
        "## Table of Contents",
        "",
        "1. Executive Summary",
        "2. Customer Intent Analysis",
        "3. Lead Outcome Analysis",
        "4. Lost Sales Opportunities",
        "5. Customer Objections",
        "6. Agent Performance Review",
        "7. Customer Sentiment",
        "8. Sales Improvement Opportunities",
        "9. Action Plan",
        "",
        "## 1. Executive Summary",
        "",
        _table(
            ["Metric", "Result"],
            [
                ["Reporting period", period],
                ["Total inbound conversations reviewed", len(reviews)],
                ["Total sales conversations", len(sales)],
                ["Unique new leads", unique_leads],
                ["Existing customer sales enquiries", existing_enquiries],
                ["Unknown contact-status sales enquiries", unknown_enquiries],
                ["Active inboxes reviewed", len(active_inboxes)],
            ],
        ),
        "",
        "### Conversations per inbox",
        "",
        _table(["Inbox", "Inbound conversations reviewed"], inbox_rows),
        "",
        "### Sales conversations handled by agent",
        "",
        _table(
            ["Handling agent", "Sales conversations"], [[name, count] for name, count in sorted(agent_counts.items())]
        ),
        "",
        "All inbound conversations were classified as either sales or non-sales. Only genuine current buying intent is included in the analyses below.",
        "",
        "## 2. Customer Intent Analysis",
        "",
        _table(
            ["Intent", "Total", "Percentage", "Supporting observations"],
            [
                [name, len(items), pct(len(items), len(sales)), _representative(items)]
                for name, items in sorted(intent_groups.items(), key=lambda item: (-len(item[1]), item[0]))
            ],
        ),
        "",
        "## 3. Lead Outcome Analysis",
        "",
        _table(
            ["Outcome", "Total", "Percentage", "Representative examples"],
            [
                [name, len(items), pct(len(items), len(sales)), _representative(items)]
                for name, items in sorted(outcome_groups.items(), key=lambda item: (-len(item[1]), item[0]))
            ],
        ),
        "",
        "## 4. Lost Sales Opportunities",
        "",
        _table(
            ["Conversation ID", "Reason", "Evidence", "Recommended follow-up"],
            [[item.id, item.missed_opportunity, item.evidence, item.recommended_follow_up] for item in lost]
            or [["None", "No lost or materially at-risk sales opportunity was identified.", "—", "—"]],
        ),
        "",
        "## 5. Customer Objections",
        "",
        _table(
            ["Objection", "Occurrences", "Representative examples"],
            [
                [name, len(items), _representative(items)]
                for name, items in sorted(objections.items(), key=lambda item: (-len(item[1]), item[0]))
            ]
            or [["No explicit objection", 0, "No explicit objection was found in the sales conversations."]],
        ),
        "",
        "## 6. Agent Performance Review",
        "",
        _table(
            [
                "Handling agent",
                "Total conversations",
                "Strengths",
                "Weaknesses",
                "Positive observations",
                "Missed opportunities",
                "What could improve conversion",
                "Specific coaching recommendations",
            ],
            build_agent_rows(sales),
        ),
        "",
        "## 7. Customer Sentiment",
        "",
        _table(
            ["Sentiment", "Total", "Percentage", "Why"],
            [
                [
                    name,
                    len(sentiment_groups.get(name, [])),
                    pct(len(sentiment_groups.get(name, [])), len(sales)),
                    {
                        "Positive": "Customer language showed appreciation, readiness, satisfaction, or willingness to proceed.",
                        "Neutral": "Customer language was primarily factual or inquisitive without a clear emotional signal.",
                        "Negative": "Customer language showed frustration, delay, price concern, dissatisfaction, or rejection.",
                    }[name],
                ]
                for name in ("Positive", "Neutral", "Negative")
            ],
        ),
        "",
        "## 8. Sales Improvement Opportunities",
        "",
        _table(
            ["Area", "Observed opportunity"],
            [
                [
                    "Buying triggers",
                    f"The leading intents were {', '.join(f'{name} ({len(items)})' for name, items in sorted(intent_groups.items(), key=lambda item: -len(item[1]))[:3]) or 'not evidenced'}.",
                ],
                [
                    "Common customer interests",
                    "; ".join(f"{name} ({count})" for name, count in interests.most_common())
                    or "No recurring product interest was detected.",
                ],
                [
                    "Knowledge gaps",
                    f"{no_response} sales request(s) had no attributable human response. Review active and stopped-response conversations for unanswered pricing, coverage, or next-step questions.",
                ],
                [
                    "Training opportunities",
                    "Coach agents to qualify location, use case, speed requirement, budget, and target installation date before recommending an offer.",
                ],
                [
                    "Workflow improvements",
                    "Require a named owner, a dated next action, and a recorded outcome for each genuine sales conversation.",
                ],
                [
                    "Automation opportunities",
                    "Candidate only: alert on unowned sales requests and follow-up deadlines after the reporting logic is accepted; no automation is implemented by this report.",
                ],
                [
                    "Upsell opportunities",
                    (
                        f"{upgrade_count} explicit upgrade conversation(s) should be matched to suitable higher-speed or higher-capacity plans."
                        if upgrade_count
                        else "No explicit upgrade request was found; do not infer upsell demand, but qualify speed and capacity needs during discovery."
                    ),
                ],
                [
                    "Cross-sell opportunities",
                    "Where business connectivity is requested, qualify redundancy, managed Wi-Fi, and related connectivity needs without assuming suitability.",
                ],
                [
                    "Follow-up improvements",
                    f"{stopped} customer(s) stopped responding and {followups} conversation(s) had a meeting or follow-up signal. Use a dated, value-led follow-up sequence.",
                ],
            ],
        ),
        "",
        "## 9. Action Plan",
        "",
        "### HIGH PRIORITY",
        "",
        _table(
            ["Issue", "Evidence", "Recommended action", "Expected business impact"],
            [
                [
                    "Sales conversations without a completed/payment outcome",
                    f"{len(sales) - successful} of {len(sales)} sales conversation(s) had no conversation-evidenced completed sale or payment outcome.",
                    "Require the owner to confirm the next commercial milestone and record the outcome after each follow-up.",
                    "Improves conversion discipline and makes pipeline leakage measurable.",
                ],
                [
                    "Lost/at-risk opportunities lack a disciplined recovery step",
                    f"{len(lost)} conversation(s) were rejected, postponed, stopped responding, or lacked a human response.",
                    "Run a short recovery queue using the conversation-specific follow-ups in Section 4 and record the result.",
                    "Recovers viable demand and makes loss reasons measurable.",
                ],
            ],
        ),
        "",
        "### MEDIUM PRIORITY",
        "",
        _table(
            ["Issue", "Evidence", "Recommended action", "Expected business impact"],
            [
                [
                    "Inconsistent discovery and next-step capture",
                    f"Only {followups} of {len(sales)} sales conversation(s) contained a meeting or explicit follow-up signal.",
                    "Use a five-question discovery checklist and end each reply with one owner and dated next step.",
                    "Improves offer relevance, forecasting, and follow-through.",
                ],
                [
                    "Negative sales sentiment",
                    f"{negative} of {len(sales)} sales conversation(s) were negative.",
                    "Review the cited conversations for delay, affordability, and service-confidence concerns; provide approved response guidance.",
                    "Reduces avoidable objections and improves trust at the buying stage.",
                ],
            ],
        ),
        "",
        "### LOW PRIORITY",
        "",
        _table(
            ["Issue", "Evidence", "Recommended action", "Expected business impact"],
            [
                [
                    "Reporting-channel attribution gaps",
                    f"{sum(review.primary_inbox_id is None for review in reviews)} reviewed conversation(s) had no configured inbox target.",
                    "After validating this report, define a canonical inbox identity for chat-widget traffic without changing conversation data.",
                    "Improves future channel-level measurement and ownership.",
                ],
                [
                    "Reusable reporting controls",
                    "This is the first manual run of the reusable read-only logic.",
                    "Review classification examples and thresholds before considering any scheduling or automation.",
                    "Builds confidence in the metric definitions before operational rollout.",
                ],
            ],
        ),
        "",
        "## Validation and Warnings",
        "",
        "All required reconciliation checks passed: active inbox coverage, inbound conversation coverage, sales totals, intent totals, outcome totals, sentiment totals, and agent totals.",
        "",
        *(f"- {warning}" for warning in warnings),
        "",
        "*Customer evidence is deliberately paraphrased and identified only by conversation ID.*",
    ]
    return "\n".join(lines).strip() + "\n"


def _inline_markup(value: str) -> str:
    escaped = html.escape(value)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    return escaped


def markdown_to_html(markdown: str, period: str, scope_label: str = "Sales inbound experience only") -> str:
    lines = markdown.splitlines()
    chunks: list[str] = []
    index = 0
    first_h1 = True
    while index < len(lines):
        line = lines[index]
        if line.startswith("# ") and first_h1:
            title = _inline_markup(line[2:])
            chunks.append(
                f'<section class="cover"><div class="cover-accent"></div><div class="cover-content"><div class="eyebrow">DOTMAC EXPERIENCE INTELLIGENCE</div><h1>{title}</h1><div class="period">{html.escape(period)}</div><div class="scope">Previous complete week · Africa/Lagos · {html.escape(scope_label)}</div></div><div class="confidential">Internal management report</div></section>'
            )
            first_h1 = False
            index += 1
            while index < len(lines) and (
                not lines[index].strip()
                or lines[index].startswith("**Reporting period:")
                or lines[index].startswith("**Scope:")
            ):
                index += 1
            continue
        if line.startswith("### "):
            chunks.append(f"<h3>{_inline_markup(line[4:])}</h3>")
        elif line.startswith("## "):
            heading = line[3:]
            css_class = "toc-heading" if heading == "Table of Contents" else ""
            chunks.append(f'<h2 class="{css_class}">{_inline_markup(heading)}</h2>')
        elif line.startswith("# "):
            chunks.append(f"<h1>{_inline_markup(line[2:])}</h1>")
        elif line.startswith("| "):
            table_lines = []
            while index < len(lines) and lines[index].startswith("|"):
                table_lines.append(lines[index])
                index += 1
            rows = [[cell.strip().replace("\\|", "|") for cell in row.strip("|").split("|")] for row in table_lines]
            headers = rows[0]
            body = rows[2:]
            chunks.append(
                "<table><thead><tr>"
                + "".join(f"<th>{_inline_markup(cell)}</th>" for cell in headers)
                + "</tr></thead><tbody>"
            )
            for row in body:
                chunks.append("<tr>" + "".join(f"<td>{_inline_markup(cell)}</td>" for cell in row) + "</tr>")
            chunks.append("</tbody></table>")
            continue
        elif re.match(r"^\d+\. ", line):
            items = []
            while index < len(lines) and re.match(r"^\d+\. ", lines[index]):
                items.append(re.sub(r"^\d+\. ", "", lines[index]))
                index += 1
            chunks.append("<ol>" + "".join(f"<li>{_inline_markup(item)}</li>" for item in items) + "</ol>")
            continue
        elif line.startswith("- "):
            items = []
            while index < len(lines) and lines[index].startswith("- "):
                items.append(lines[index][2:])
                index += 1
            chunks.append("<ul>" + "".join(f"<li>{_inline_markup(item)}</li>" for item in items) + "</ul>")
            continue
        elif line.strip():
            chunks.append(f"<p>{_inline_markup(line)}</p>")
        index += 1

    css = """
    @page { size: A4; margin: 18mm 14mm 18mm 14mm; @bottom-center { content: "Page " counter(page) " of " counter(pages); color: #64748b; font-size: 8pt; } }
    @page:first { margin: 0; @bottom-center { content: none; } }
    * { box-sizing: border-box; }
    body { font-family: Arial, Helvetica, sans-serif; color: #172033; font-size: 9.2pt; line-height: 1.42; }
    .cover { page: cover; page-break-after: always; height: 297mm; padding: 31mm 25mm; background: #f8fafc; position: relative; }
    .cover-accent { position: absolute; left: 0; top: 0; bottom: 0; width: 13mm; background: linear-gradient(#0f766e, #0f172a); }
    .cover-content { margin-top: 58mm; max-width: 145mm; }
    .cover h1 { font-size: 31pt; line-height: 1.1; color: #0f172a; margin: 8mm 0 12mm; }
    .eyebrow { color: #0f766e; font-weight: bold; letter-spacing: 1.6pt; font-size: 9pt; }
    .period { color: #0f766e; font-size: 17pt; font-weight: bold; margin-bottom: 4mm; }
    .scope { color: #475569; font-size: 10.5pt; }
    .confidential { position: absolute; bottom: 23mm; left: 25mm; color: #64748b; font-size: 8.5pt; text-transform: uppercase; letter-spacing: 1pt; }
    h2 { color: #0f4c5c; font-size: 17pt; margin: 9mm 0 3mm; padding-bottom: 2mm; border-bottom: 1.5pt solid #99f6e4; page-break-after: avoid; }
    h2:not(.toc-heading) { break-before: page; }
    h3 { color: #115e59; font-size: 12.5pt; margin: 6mm 0 2mm; page-break-after: avoid; }
    p { margin: 2.5mm 0; }
    ol, ul { margin: 2mm 0 4mm 6mm; padding-left: 5mm; }
    li { margin: 1.2mm 0; }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; margin: 3mm 0 6mm; font-size: 7.6pt; overflow-wrap: anywhere; }
    thead { display: table-header-group; }
    tr { page-break-inside: avoid; }
    th { background: #0f4c5c; color: white; text-align: left; font-weight: bold; padding: 2.2mm; border: 0.5pt solid #cbd5e1; }
    td { vertical-align: top; padding: 2.1mm; border: 0.5pt solid #cbd5e1; word-wrap: break-word; overflow-wrap: anywhere; }
    tbody tr:nth-child(even) td { background: #f1f5f9; }
    code { font-size: 7.4pt; }
    """
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head><body>{''.join(chunks)}</body></html>"


def warnings_for(reviews: list[ConversationReview], data: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    unconfigured = [review for review in reviews if review.primary_inbox_id is None]
    inactive = [
        review for review in reviews if review.primary_inbox_id and "Inactive/unconfigured" in review.primary_inbox_name
    ]
    if unconfigured:
        warnings.append(
            f"{len(unconfigured)} inbound conversation(s) used a channel with no configured inbox target; they were still fully reviewed and appear under an unconfigured channel label."
        )
    if inactive:
        warnings.append(
            f"{len(inactive)} inbound conversation(s) referenced an inactive or unconfigured inbox target; they were still fully reviewed."
        )
    attachment_ids = {row["message_id"] for row in data["attachments"]}
    media_only = sum(
        1
        for review in reviews
        for message in review.window_inbound
        if message.id in attachment_ids
        and not re.sub(r"\[(?:image|audio|video) message\]|\(attachment\)", "", message.body, flags=re.I).strip()
    )
    if media_only:
        warnings.append(
            f"{media_only} inbound message(s) were attachment/media-only; attachment metadata was counted, but image/audio content was not transcribed."
        )
    unassigned_sales = sum(review.is_sales and review.handling_agent == "Unassigned" for review in reviews)
    if unassigned_sales:
        warnings.append(
            f"{unassigned_sales} sales conversation(s) could not be attributed under the required assignment/responder precedence."
        )
    zero_volume = [
        row["name"]
        for row in data["active_inboxes"]
        if not any(review.primary_inbox_id == row["id"] for review in reviews)
    ]
    if zero_volume:
        warnings.append(f"Active inboxes with zero inbound conversations in the period: {', '.join(zero_volume)}.")
    return warnings


def generate(output_dir: Path, now: datetime | None = None) -> dict[str, Any]:
    as_of = now or datetime.now(UTC)
    start_local, end_exclusive_local, start_utc, end_utc = previous_complete_week(as_of)
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        data = collect_data(db, start_utc, end_utc)
        reviews = build_reviews(data, start_utc, end_utc)
        classify_reviews(reviews, data, start_utc, end_utc, as_of)
        checks = validate(reviews, data["active_inboxes"])
        warnings = warnings_for(reviews, data)
        markdown = build_markdown(
            reviews,
            data["active_inboxes"],
            start_local,
            end_exclusive_local,
            warnings,
        )
    finally:
        db.rollback()
        db.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / f"{REPORT_NAME}.md"
    pdf_path = output_dir / f"{REPORT_NAME}.pdf"
    markdown_path.write_text(markdown, encoding="utf-8")
    period = f"{start_local:%d %B %Y} - {(end_exclusive_local - timedelta(seconds=1)):%d %B %Y}"
    report_html = markdown_to_html(markdown, period)
    from weasyprint import HTML

    document = HTML(string=report_html, base_url=str(output_dir)).render()
    document.write_pdf(str(pdf_path))
    return {
        "reporting_period": period,
        "total_conversations_reviewed": checks["reviewed"],
        "total_sales_conversations": checks["sales"],
        "active_inboxes_reviewed": checks["active_inboxes"],
        "markdown_path": str(markdown_path),
        "pdf_path": str(pdf_path),
        "warnings": warnings,
        "validation": checks,
        "pdf_pages": len(document.pages),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/weekly"))
    parser.add_argument(
        "--as-of",
        help="Optional ISO-8601 instant used only for deterministic validation; defaults to now.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    now = _coerce_datetime(args.as_of) if args.as_of else None
    try:
        result = generate(args.output_dir, now=now)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=True))
        return 1
    print(json.dumps({"status": "ok", **result}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
