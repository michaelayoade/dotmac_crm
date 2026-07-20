#!/usr/bin/env python3
"""Generate the read-only Weekly Support Inbound Experience Report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import weekly_sales_inbound_report as shared
from sqlalchemy import text

REPORT_NAME = "Weekly_Support_Inbound_Experience_Report"

SUPPORT_SIGNAL_RE = re.compile(
    r"\b(?:no (?:internet|network|service|connection)|internet (?:is )?(?:down|off|not working)|"
    r"network (?:is )?(?:down|bad|slow|not working)|down(?:time)?|outage|disconnect(?:ed|ion)?|"
    r"not working|cannot browse|can't browse|slow|latency|buffer|fluctuat|intermittent|packet loss|"
    r"router|modem|amber light|red light|los light|wifi|wi-fi|fault|technical|support|helpdesk|ticket|"
    r"complaint|issue|problem|resolve|restore|engineer|repair|relocat(?:e|ion)|installation delay|"
    r"billing|invoice|payment|renew|recharge|subscription|refund|compensation|credit.*days|extension|"
    r"portal|selfcare|login|password|account|profile|activate|suspend|cancel|fraud|abuse|security)\b",
    re.I,
)
NOISE_RE = re.compile(
    r"\b(?:undelivered mail returned to sender|delivery status notification|mail system at host|"
    r"newsletter|summit & expo|certified caregiver|stock availability|ready for dispatch|"
    r"manufacturer from china|hot sale|siwes|internship|training program|academy track|"
    r"weekly report of .* bts|this is a test, kindly ignore)\b",
    re.I,
)

COMPLAINT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Security or abuse report",
        re.compile(r"\b(?:fraud|phishing|spam server|abuse|attack|malware|security incident|extort)\b", re.I),
    ),
    (
        "Compensation, refund, or service-credit request",
        re.compile(
            r"\b(?:refund|compensation|credit.{0,40}days|extension|days lost|days without service)\b", re.I | re.S
        ),
    ),
    (
        "Portal, login, or account-access problem",
        re.compile(r"\b(?:portal|selfcare|login|log in|password|dashboard|account access|invalid username)\b", re.I),
    ),
    (
        "Billing, payment, invoice, or renewal problem",
        re.compile(r"\b(?:billing|invoice|payment|paid|renew|recharge|subscription|balance|receipt)\b", re.I),
    ),
    (
        "Slow, intermittent, or degraded connectivity",
        re.compile(
            r"\b(?:slow|latency|buffer|fluctuat|intermittent|packet loss|poor (?:network|speed|connection)|speed)\b",
            re.I,
        ),
    ),
    (
        "No connectivity or service outage",
        re.compile(
            r"\b(?:no (?:internet|network|service|connection)|down(?:time)?|outage|not working|"
            r"internet (?:is )?(?:off|down)|disconnect(?:ed|ion)?|cannot browse|can't browse)\b",
            re.I,
        ),
    ),
    (
        "Router, modem, or local equipment problem",
        re.compile(r"\b(?:router|modem|amber light|red light|los light|device|equipment|power cycle|reboot)\b", re.I),
    ),
    (
        "Installation, relocation, or field-visit delay",
        re.compile(
            r"\b(?:engineer|technician|site visit|installation|relocat(?:e|ion)|appointment|survey|field team)\b", re.I
        ),
    ),
    (
        "Poor communication or delayed response",
        re.compile(
            r"\b(?:no response|not responding|still waiting|any update|no update|nobody.*respond|ignored|revert)\b",
            re.I | re.S,
        ),
    ),
    (
        "Suspension, cancellation, or service-status request",
        re.compile(r"\b(?:suspend|suspension|cancel|close.*account|service status|expired|activate)\b", re.I | re.S),
    ),
)

PAIN_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Extended downtime",
        re.compile(
            r"\b(?:days?|week|weeks|hours).{0,45}\b(?:down|without|no internet|not working)\b|\b(?:down|without|no internet|not working).{0,45}\b(?:days?|week|weeks|hours)\b",
            re.I | re.S,
        ),
    ),
    (
        "Repeated or recurring faults",
        re.compile(r"\b(?:again|every day|daily|repeated|recurring|always|same problem|keeps)\b", re.I),
    ),
    (
        "Slow or unstable performance",
        re.compile(r"\b(?:slow|buffer|fluctuat|intermittent|poor speed|latency|packet loss)\b", re.I),
    ),
    (
        "Delayed human response",
        re.compile(
            r"\b(?:no response|not responding|still waiting|any update|no update|ignored|hours.*reply)\b", re.I | re.S
        ),
    ),
    (
        "Unclear restoration timeline",
        re.compile(r"\b(?:when.*(?:resolve|restore|fix)|how long|no etr|timeline|etr)\b", re.I | re.S),
    ),
    (
        "Lost paid subscription time",
        re.compile(r"\b(?:compensation|refund|extension|days lost|subscription.*wast|pay.*not enjoy)\b", re.I | re.S),
    ),
    (
        "Portal or payment friction",
        re.compile(
            r"\b(?:portal|selfcare|login|password|payment.*(?:fail|reflect)|cannot pay|unable to pay)\b", re.I | re.S
        ),
    ),
    (
        "Repeated troubleshooting burden",
        re.compile(r"\b(?:reboot|restart|power cycle|speed test|send.*picture|check.*router)\b", re.I | re.S),
    ),
    (
        "Missed or delayed field visit",
        re.compile(
            r"\b(?:engineer|technician|field team).{0,80}\b(?:waiting|not come|didn't come|delay|promise)\b",
            re.I | re.S,
        ),
    ),
)

POSITIVE_RE = re.compile(
    r"\b(?:thank|thanks|appreciat|excellent|great|satisfied|happy|working now|service is back|resolved|restored|well done)\b",
    re.I,
)
NEGATIVE_RE = re.compile(
    r"\b(?:frustrat|angry|unhappy|disappoint|terrible|bad|poor|unacceptable|ridiculous|tired|sick of|"
    r"regret|inhumane|crazy|worst|no response|still waiting|not working|no internet|down again|fed up)\b",
    re.I,
)
UNRESOLVED_RE = re.compile(
    r"\b(?:still (?:down|not working|no internet|unresolved|waiting)|not (?:resolved|restored|working)|"
    r"no (?:internet|network|service|response|update)|down again|issue persists?|problem persists?)\b",
    re.I,
)
PARTIAL_RE = re.compile(
    r"\b(?:working but|back but|restored but|still fluctuating|still intermittent|partially|somewhat working|"
    r"service is back.{0,80}(?:slow|fluctuat|intermittent))\b",
    re.I | re.S,
)
RESOLVED_RE = re.compile(
    r"\b(?:issue (?:is |has been )?resolved|problem (?:is |has been )?resolved|service is back|"
    r"internet is (?:back|working)|connection is (?:back|working)|working (?:fine|now)|fully restored|"
    r"up and running|can confirm.{0,50}(?:working|restored))\b",
    re.I | re.S,
)
FOLLOW_UP_RE = re.compile(
    r"\b(?:will (?:check|revert|follow up|escalate|update|call)|engineer.*(?:visit|assigned)|"
    r"ticket (?:has been |was )?(?:created|raised|escalated)|scheduled|awaiting|pending)\b",
    re.I | re.S,
)

RECURRING_GROUPS: dict[str, tuple[str, ...]] = {
    "Technical issues": (
        "No connectivity or service outage",
        "Slow, intermittent, or degraded connectivity",
        "Router, modem, or local equipment problem",
        "Security or abuse report",
    ),
    "Operational issues": ("Installation, relocation, or field-visit delay",),
    "Billing issues": (
        "Billing, payment, invoice, or renewal problem",
        "Compensation, refund, or service-credit request",
    ),
    "Service quality issues": (
        "No connectivity or service outage",
        "Slow, intermittent, or degraded connectivity",
    ),
    "Communication issues": ("Poor communication or delayed response",),
    "Process issues": (
        "Portal, login, or account-access problem",
        "Suspension, cancellation, or service-status request",
        "Installation, relocation, or field-visit delay",
    ),
}


@dataclass
class SupportReview:
    base: shared.ConversationReview
    complaint: str = ""
    complaint_evidence: str = ""
    resolution: str = ""
    resolution_evidence: str = ""
    happiness: str = ""
    sentiment: str = ""
    sentiment_evidence: str = ""
    pain_points: list[str] = field(default_factory=list)
    human_outbound_after_inbound: int = 0

    @property
    def id(self) -> str:
        return self.base.id

    @property
    def handling_agent(self) -> str:
        return self.base.handling_agent

    @property
    def inbox_name(self) -> str:
        return self.base.primary_inbox_name


def _inbound_context(review: shared.ConversationReview, start_utc: datetime, end_utc: datetime) -> str:
    context_start = start_utc - timedelta(days=30)
    context_end = end_utc + timedelta(days=7)
    return "\n".join(
        message.cleaned_text
        for message in review.history
        if message.direction == "inbound"
        and context_start <= message.occurred_at < context_end
        and message.cleaned_text
    )


def _is_support(review: shared.ConversationReview, start_utc: datetime, end_utc: datetime) -> tuple[bool, str]:
    current_text = review.customer_window_text
    recent_inbound = _inbound_context(review, start_utc, end_utc)
    ai_state = shared._safe_metadata(review.metadata.get("ai_intake"))
    department = str(ai_state.get("department") or "").lower()
    support_department = department == "support" or department.startswith("billing")
    support_tag = bool(
        review.tags.intersection(
            {
                "support",
                "billing-payment",
                "billing-renewal",
                "billing-adjustment",
                "billing-reactivation",
                "billing-general",
            }
        )
    )
    if NOISE_RE.search(current_text):
        return False, "Automated, marketing, recruitment, or delivery-failure traffic"
    is_sales, _ = shared.sales_basis(review, start_utc, end_utc)
    current_support = bool(SUPPORT_SIGNAL_RE.search(current_text))
    if is_sales and not current_support:
        return False, "Current buying intent without a support issue"
    if current_support:
        return True, "Explicit support issue in the reporting window"
    if support_department and SUPPORT_SIGNAL_RE.search(recent_inbound):
        return True, "CRM intake classification corroborated by recent support history"
    if support_tag and SUPPORT_SIGNAL_RE.search(recent_inbound):
        return True, "CRM support/billing tag corroborated by recent support history"
    return False, "No support intent found"


def _complaint_for(text_value: str) -> tuple[str, str]:
    evidence = {
        "Security or abuse report": "Inbound evidence reported a network-security, fraud, or abuse concern.",
        "Compensation, refund, or service-credit request": "Customer requested compensation, a refund, extension, or credit for lost service time.",
        "Portal, login, or account-access problem": "Customer reported difficulty accessing the portal, credentials, dashboard, or account.",
        "Billing, payment, invoice, or renewal problem": "Customer reported a payment, invoice, renewal, subscription, or balance problem.",
        "Slow, intermittent, or degraded connectivity": "Customer reported slow, buffering, intermittent, or unstable connectivity.",
        "No connectivity or service outage": "Customer reported unavailable, disconnected, or non-working connectivity.",
        "Router, modem, or local equipment problem": "Customer reported router, modem, indicator-light, or local equipment symptoms.",
        "Installation, relocation, or field-visit delay": "Customer requested or followed up on an installation, relocation, engineer, or field visit.",
        "Poor communication or delayed response": "Customer reported delayed, absent, or unclear communication.",
        "Suspension, cancellation, or service-status request": "Customer asked to suspend, cancel, activate, or confirm service status.",
        "Other support request": "The conversation contained support intent outside the named complaint categories.",
    }
    for name, pattern in COMPLAINT_RULES:
        if pattern.search(text_value):
            return name, evidence[name]
    return "Other support request", evidence["Other support request"]


def _human_messages_after_inbound(
    review: shared.ConversationReview,
    agent_people: set[str],
) -> list[shared.MessageRow]:
    first_at = review.window_inbound[0].occurred_at
    return [
        message
        for message in review.history
        if message.direction == "outbound"
        and message.author_id in agent_people
        and not message.is_ai_generated
        and message.occurred_at >= first_at
    ]


def _resolution_for(
    review: shared.ConversationReview,
    agent_people: set[str],
) -> tuple[str, str, int]:
    first_at = review.window_inbound[0].occurred_at
    later_customer = [
        message for message in review.history if message.direction == "inbound" and message.occurred_at >= first_at
    ]
    human = _human_messages_after_inbound(review, agent_people)
    latest_customer_text = later_customer[-1].cleaned_text if later_customer else ""
    customer_text = "\n".join(message.cleaned_text for message in later_customer if message.cleaned_text)
    agent_text = "\n".join(message.cleaned_text for message in human if message.cleaned_text)
    if PARTIAL_RE.search(latest_customer_text) or PARTIAL_RE.search(customer_text):
        return (
            "Partially Resolved",
            "Customer indicated restoration with continuing instability or another unresolved component.",
            len(human),
        )
    if UNRESOLVED_RE.search(latest_customer_text):
        return (
            "Unresolved",
            "The latest customer evidence said the issue persisted or service remained unavailable.",
            len(human),
        )
    if RESOLVED_RE.search(latest_customer_text):
        return (
            "Resolved",
            "Customer explicitly confirmed that service or the reported issue was restored or working.",
            len(human),
        )
    if review.status == "resolved":
        return "Resolved", "CRM status indicates that the support conversation was resolved.", len(human)
    if review.status == "resolved_to_ticket":
        return (
            "Pending Follow-up",
            "The conversation moved to a support ticket and requires ticket follow-through.",
            len(human),
        )
    if human and (FOLLOW_UP_RE.search(agent_text) or human[-1].occurred_at > later_customer[-1].occurred_at):
        return (
            "Pending Follow-up",
            "The latest handling evidence indicates an agent action, escalation, or customer follow-up is pending.",
            len(human),
        )
    if RESOLVED_RE.search(customer_text):
        return "Resolved", "Customer evidence indicates that the support issue was resolved.", len(human)
    if not human:
        return "Unresolved", "No attributable human support response was found after the inbound issue.", 0
    return (
        "Unresolved",
        "No clear resolution confirmation or pending next step was found in the complete history.",
        len(human),
    )


def _sentiment_for(text_value: str) -> tuple[str, str]:
    positive = len(POSITIVE_RE.findall(text_value))
    negative = len(NEGATIVE_RE.findall(text_value))
    if negative > positive:
        return (
            "Negative",
            "Customer language contained more frustration, service-failure, or delay signals than appreciation.",
        )
    if positive > negative and positive:
        return (
            "Positive",
            "Customer language contained more appreciation or restoration-confirmation signals than negative signals.",
        )
    return "Neutral", "Customer language was primarily factual or positive and negative signals were balanced."


def _happiness_for(text_value: str, resolution: str) -> str:
    negative = len(NEGATIVE_RE.findall(text_value))
    positive = len(POSITIVE_RE.findall(text_value))
    if negative >= 2 or (negative and resolution == "Unresolved"):
        return "Unhappy"
    if resolution == "Resolved" and positive:
        return "Happy"
    return "Neutral"


def classify_support(
    reviews: list[shared.ConversationReview],
    data: dict[str, Any],
    start_utc: datetime,
    end_utc: datetime,
) -> list[SupportReview]:
    shared.attribute_agents(reviews, data)
    agent_people = {row["person_id"] for row in data["agents"]}
    support: list[SupportReview] = []
    for review in reviews:
        selected, _ = _is_support(review, start_utc, end_utc)
        if not selected:
            continue
        context = _inbound_context(review, start_utc, end_utc)
        complaint, complaint_evidence = _complaint_for(review.customer_window_text)
        if complaint == "Other support request":
            complaint, complaint_evidence = _complaint_for(context)
        resolution, resolution_evidence, human_count = _resolution_for(review, agent_people)
        sentiment, sentiment_evidence = _sentiment_for(context)
        pain_points = [name for name, pattern in PAIN_RULES if pattern.search(context)]
        support.append(
            SupportReview(
                base=review,
                complaint=complaint,
                complaint_evidence=complaint_evidence,
                resolution=resolution,
                resolution_evidence=resolution_evidence,
                happiness=_happiness_for(context, resolution),
                sentiment=sentiment,
                sentiment_evidence=sentiment_evidence,
                pain_points=pain_points,
                human_outbound_after_inbound=human_count,
            )
        )
    return support


def validate(
    all_reviews: list[shared.ConversationReview],
    support: list[SupportReview],
    active_inboxes: list[dict[str, Any]],
) -> dict[str, int]:
    errors: list[str] = []
    if any(not review.window_inbound for review in all_reviews):
        errors.append("At least one reviewed conversation has no inbound message in the reporting window.")
    if len({review.id for review in all_reviews}) != len(all_reviews):
        errors.append("Inbound conversation IDs are not unique.")
    if len({review.id for review in support}) != len(support):
        errors.append("Support conversation IDs are not unique.")
    expected = len(support)
    dimensions = {
        "complaint": [review.complaint for review in support],
        "sentiment": [review.sentiment for review in support],
        "resolution": [review.resolution for review in support],
        "agent": [review.handling_agent for review in support],
        "happiness": [review.happiness for review in support],
    }
    for label, values in dimensions.items():
        if any(not value for value in values):
            errors.append(f"At least one support conversation has no {label} classification.")
        if sum(Counter(values).values()) != expected:
            errors.append(f"{label.title()} totals do not reconcile to support conversations.")
    active_ids = {row["id"] for row in active_inboxes}
    checked_ids = {
        row["id"]
        for row in active_inboxes
        if any(review.primary_inbox_id == row["id"] for review in all_reviews)
        or not any(review.primary_inbox_id == row["id"] for review in all_reviews)
    }
    if checked_ids != active_ids:
        errors.append("Not every active inbox was analysed.")
    if errors:
        raise ValueError("Validation failed:\n- " + "\n- ".join(errors))
    return {
        "inbound_reviewed": len(all_reviews),
        "support_reviewed": expected,
        "complaint_total": sum(Counter(dimensions["complaint"]).values()),
        "sentiment_total": sum(Counter(dimensions["sentiment"]).values()),
        "resolution_total": sum(Counter(dimensions["resolution"]).values()),
        "agent_total": sum(Counter(dimensions["agent"]).values()),
        "happiness_total": sum(Counter(dimensions["happiness"]).values()),
        "active_inboxes": len(active_inboxes),
    }


def _examples(items: list[SupportReview], *, evidence: str = "complaint", limit: int = 3) -> str:
    if not items:
        return "No examples"
    evidence_field = {
        "complaint": "complaint_evidence",
        "resolution": "resolution_evidence",
        "sentiment": "sentiment_evidence",
    }[evidence]
    return "; ".join(f"{item.id}: {getattr(item, evidence_field)}" for item in items[:limit])


def _agent_rows(support: list[SupportReview]) -> list[list[str]]:
    grouped: dict[str, list[SupportReview]] = defaultdict(list)
    for review in support:
        grouped[review.handling_agent].append(review)
    rows: list[list[str]] = []
    for agent, items in sorted(grouped.items()):
        responded = [item for item in items if item.human_outbound_after_inbound]
        resolved = [item for item in items if item.resolution == "Resolved"]
        positive = [item for item in items if item.sentiment == "Positive" or item.happiness == "Happy"]
        unresolved = [item for item in items if item.resolution == "Unresolved"]
        no_response = [item for item in items if not item.human_outbound_after_inbound]
        fast = [item for item in items if item.base.response_seconds is not None and item.base.response_seconds <= 3600]
        strengths = (
            f"Human response in {len(responded)}/{len(items)} conversation(s); {len(resolved)} resolved; "
            f"{len(fast)} initial response(s) within one hour."
        )
        weaknesses = (
            f"{len(unresolved)} unresolved and {len(no_response)} without an attributable human response."
            if unresolved or no_response
            else "No material handling weakness was evidenced in this period."
        )
        positive_text = _examples(
            positive or resolved,
            evidence="sentiment" if positive else "resolution",
            limit=2,
        )
        missed = (
            "; ".join(f"{item.id}: {item.resolution_evidence}" for item in (no_response + unresolved)[:3])
            or "None evidenced."
        )
        handling = f"Response coverage {shared.pct(len(responded), len(items))}; resolved {shared.pct(len(resolved), len(items))}."
        communication = (
            f"{len(positive)} positive/happy interaction(s); "
            f"{sum(item.sentiment == 'Negative' for item in items)} negative-sentiment conversation(s)."
        )
        coaching_ids = ", ".join(item.id for item in (no_response + unresolved)[:3])
        coaching = (
            f"Review {coaching_ids}; acknowledge impact, give a specific next action and time, and close with customer confirmation."
            if coaching_ids
            else "Reinforce explicit resolution confirmation and concise expectation-setting using resolved examples."
        )
        rows.append(
            [agent, str(len(items)), strengths, weaknesses, positive_text, missed, handling, communication, coaching]
        )
    return rows


def _warnings(
    all_reviews: list[shared.ConversationReview],
    support: list[SupportReview],
    data: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    unconfigured = [review for review in support if review.base.primary_inbox_id is None]
    inactive = [review for review in support if "Inactive/unconfigured" in review.inbox_name]
    if unconfigured:
        warnings.append(
            f"{len(unconfigured)} support conversation(s) used a channel with no configured inbox target; they were included under an unconfigured channel label."
        )
    if inactive:
        warnings.append(
            f"{len(inactive)} support conversation(s) referenced an inactive or unconfigured inbox target; they were still reviewed."
        )
    unassigned = sum(review.handling_agent == "Unassigned" for review in support)
    if unassigned:
        warnings.append(f"{unassigned} support conversation(s) could not be attributed under the required precedence.")
    zero_volume = [
        row["name"]
        for row in data["active_inboxes"]
        if not any(review.primary_inbox_id == row["id"] for review in all_reviews)
    ]
    if zero_volume:
        warnings.append(f"Active inboxes with zero inbound conversations in the period: {', '.join(zero_volume)}.")
    attachment_ids = {row["message_id"] for row in data["attachments"]}
    media_only = sum(
        1
        for review in support
        for message in review.base.window_inbound
        if message.id in attachment_ids
        and not re.sub(
            r"\[(?:image|audio|video|document) message\]|\(attachment\)", "", message.body, flags=re.I
        ).strip()
    )
    if media_only:
        warnings.append(
            f"{media_only} inbound support message(s) were attachment/media-only; metadata was included but media content was not transcribed."
        )
    return warnings


def build_markdown(
    all_reviews: list[shared.ConversationReview],
    support: list[SupportReview],
    active_inboxes: list[dict[str, Any]],
    start_local: datetime,
    end_local: datetime,
    warnings: list[str],
) -> str:
    period = f"{start_local:%d %B %Y} - {(end_local - timedelta(seconds=1)):%d %B %Y}"
    complaint_groups: dict[str, list[SupportReview]] = defaultdict(list)
    resolution_groups: dict[str, list[SupportReview]] = defaultdict(list)
    happiness_groups: dict[str, list[SupportReview]] = defaultdict(list)
    sentiment_groups: dict[str, list[SupportReview]] = defaultdict(list)
    pain_groups: dict[str, list[SupportReview]] = defaultdict(list)
    for review in support:
        complaint_groups[review.complaint].append(review)
        resolution_groups[review.resolution].append(review)
        happiness_groups[review.happiness].append(review)
        sentiment_groups[review.sentiment].append(review)
        for pain in review.pain_points:
            pain_groups[pain].append(review)
    inbound_inbox_counts = Counter(review.primary_inbox_name for review in all_reviews)
    support_inbox_counts = Counter(review.inbox_name for review in support)
    active_names = {row["name"] for row in active_inboxes}
    inbox_rows = [
        [row["name"], inbound_inbox_counts.get(row["name"], 0), support_inbox_counts.get(row["name"], 0)]
        for row in active_inboxes
    ]
    extra_names = sorted((set(inbound_inbox_counts) | set(support_inbox_counts)) - active_names)
    inbox_rows.extend([[name, inbound_inbox_counts[name], support_inbox_counts[name]] for name in extra_names])
    agent_counts = Counter(review.handling_agent for review in support)
    unresolved = len(resolution_groups.get("Unresolved", []))
    pending = len(resolution_groups.get("Pending Follow-up", []))
    negative = len(sentiment_groups.get("Negative", []))
    unhappy = len(happiness_groups.get("Unhappy", []))
    no_human = sum(not review.human_outbound_after_inbound for review in support)
    top_pain = sorted(pain_groups.items(), key=lambda item: (-len(item[1]), item[0]))

    recurring_rows: list[list[Any]] = []
    for group, complaints in RECURRING_GROUPS.items():
        items = [review for review in support if review.complaint in complaints]
        description = {
            "Technical issues": "Connectivity, equipment, performance, and security incidents.",
            "Operational issues": "Installation, relocation, technician, and field-visit execution.",
            "Billing issues": "Payments, invoices, renewals, refunds, and lost-service credits.",
            "Service quality issues": "Outages, instability, and performance below customer expectations.",
            "Communication issues": "Delayed responses, absent updates, and unclear restoration expectations.",
            "Process issues": "Portal access, service-state changes, ticket hand-offs, and field workflows.",
        }[group]
        recurring_rows.append([group, len(items), description, _examples(items)])

    lines = [
        f"# {REPORT_NAME.replace('_', ' ')}",
        "",
        f"**Reporting period:** {period}",
        "",
        "**Scope:** Support inbound experience only — previous complete week, Africa/Lagos timezone",
        "",
        "## Table of Contents",
        "",
        "1. Executive Summary",
        "2. Customer Complaints",
        "3. Customer Pain Points",
        "4. Resolution Status",
        "5. Customer Happiness",
        "6. Customer Sentiment",
        "7. Recurring Issues",
        "8. Agent Performance Review",
        "9. Customer Experience Analysis",
        "10. Action Plan",
        "",
        "## 1. Executive Summary",
        "",
        shared._table(
            ["Metric", "Result"],
            [
                ["Reporting period", period],
                ["Total inbound conversations reviewed", len(all_reviews)],
                ["Total support conversations", len(support)],
                ["Active inboxes reviewed", len(active_inboxes)],
            ],
        ),
        "",
        "### Conversations per inbox",
        "",
        shared._table(["Inbox", "All inbound reviewed", "Support conversations"], inbox_rows),
        "",
        "### Conversations per handling agent",
        "",
        shared._table(
            ["Handling agent", "Support conversations"],
            [[agent, count] for agent, count in sorted(agent_counts.items())],
        ),
        "",
        "## 2. Customer Complaints",
        "",
        shared._table(
            ["Complaint", "Occurrences", "Percentage", "Representative examples"],
            [
                [name, len(items), shared.pct(len(items), len(support)), _examples(items)]
                for name, items in sorted(complaint_groups.items(), key=lambda item: (-len(item[1]), item[0]))
            ],
        ),
        "",
        "## 3. Customer Pain Points",
        "",
        shared._table(
            ["Pain point", "Frequency", "Representative examples"],
            [[name, len(items), _examples(items)] for name, items in top_pain]
            or [["No recurring pain point", 0, "No recurring pain point was detected."]],
        ),
        "",
        "## 4. Resolution Status",
        "",
        shared._table(
            ["Resolution status", "Total", "Percentage", "Supporting evidence"],
            [
                [
                    status,
                    len(resolution_groups.get(status, [])),
                    shared.pct(len(resolution_groups.get(status, [])), len(support)),
                    _examples(resolution_groups.get(status, []), evidence="resolution"),
                ]
                for status in ("Resolved", "Partially Resolved", "Unresolved", "Pending Follow-up")
            ],
        ),
        "",
        "## 5. Customer Happiness",
        "",
        shared._table(
            ["Customer happiness", "Total", "Percentage", "Why"],
            [
                [
                    status,
                    len(happiness_groups.get(status, [])),
                    shared.pct(len(happiness_groups.get(status, [])), len(support)),
                    {
                        "Happy": "Customer appreciation coincided with evidence that the issue was resolved.",
                        "Neutral": "The interaction was factual or lacked enough evidence of happiness or unhappiness.",
                        "Unhappy": "Customer language showed repeated frustration, dissatisfaction, or an unresolved adverse experience.",
                    }[status],
                ]
                for status in ("Happy", "Neutral", "Unhappy")
            ],
        ),
        "",
        "## 6. Customer Sentiment",
        "",
        shared._table(
            ["Sentiment", "Total", "Percentage", "Supporting observations"],
            [
                [
                    status,
                    len(sentiment_groups.get(status, [])),
                    shared.pct(len(sentiment_groups.get(status, [])), len(support)),
                    _examples(sentiment_groups.get(status, []), evidence="sentiment"),
                ]
                for status in ("Positive", "Neutral", "Negative")
            ],
        ),
        "",
        "## 7. Recurring Issues",
        "",
        shared._table(["Issue type", "Occurrences", "What it includes", "Representative examples"], recurring_rows),
        "",
        "## 8. Agent Performance Review",
        "",
        shared._table(
            [
                "Handling agent",
                "Total conversations",
                "Strengths",
                "Weaknesses",
                "Positive customer interactions",
                "Missed opportunities",
                "Customer handling quality",
                "Communication quality",
                "Specific coaching recommendations",
            ],
            _agent_rows(support),
        ),
        "",
        "## 9. Customer Experience Analysis",
        "",
        shared._table(
            ["Experience dimension", "Observed evidence", "Where support could improve"],
            [
                [
                    "What customers appreciated",
                    f"{len(sentiment_groups.get('Positive', []))} conversation(s) contained appreciation or positive resolution language.",
                    "Preserve clear ownership, prompt replies, and explicit resolution confirmation in successful interactions.",
                ],
                [
                    "What customers disliked",
                    f"{negative} conversation(s) had negative sentiment and {unhappy} customers appeared unhappy.",
                    "Acknowledge impact early and replace generic reassurance with a concrete action, owner, and time expectation.",
                ],
                [
                    "Recurring frustrations",
                    "; ".join(f"{name} ({len(items)})" for name, items in top_pain[:5])
                    or "No recurring frustration detected.",
                    "Use the highest-frequency pain points as weekly service-recovery and root-cause priorities.",
                ],
                [
                    "Customer expectations",
                    "Customers repeatedly expected reliable service, prompt acknowledgement, a restoration timeline, regular updates, and credit for paid downtime.",
                    "Set and meet one explicit next-update time; confirm restoration and compensation disposition before closure.",
                ],
                [
                    "Support experience improvement",
                    f"{unresolved} unresolved, {pending} pending follow-up, and {no_human} without an attributable human response.",
                    "Prioritise unowned and unresolved cases, then require customer-confirmed closure or a dated follow-up state.",
                ],
            ],
        ),
        "",
        "## 10. Action Plan",
        "",
        "### HIGH PRIORITY",
        "",
        shared._table(
            ["Issue", "Evidence", "Recommended action", "Expected customer impact"],
            [
                [
                    "Unresolved support demand",
                    f"{unresolved} of {len(support)} support conversation(s) remained unresolved; {no_human} had no attributable human response.",
                    "Create an immediate recovery queue ordered by no-response, extended downtime, and repeat-fault evidence; assign one owner and next-update time.",
                    "Faster acknowledgement and restoration, with fewer customers left uncertain or repeatedly chasing support.",
                ],
                [
                    "Recurring connectivity failure",
                    f"{sum(review.complaint in RECURRING_GROUPS['Technical issues'] for review in support)} technical complaint(s) were identified.",
                    "Review the cited conversations by affected service area and failure pattern; route confirmed root causes to the authoritative technical owner.",
                    "Reduces repeat outages and improves service reliability for affected customers.",
                ],
            ],
        ),
        "",
        "### MEDIUM PRIORITY",
        "",
        shared._table(
            ["Issue", "Evidence", "Recommended action", "Expected customer impact"],
            [
                [
                    "Weak restoration communication",
                    f"{len(pain_groups.get('Delayed human response', []))} conversation(s) showed delayed-response pain and {len(pain_groups.get('Unclear restoration timeline', []))} showed timeline uncertainty.",
                    "Coach agents to state the action taken, responsible team, next-update time, and escalation trigger in every unresolved reply.",
                    "Customers receive clearer expectations and need fewer repeat contacts for status updates.",
                ],
                [
                    "Paid downtime and billing friction",
                    f"{sum(review.complaint in RECURRING_GROUPS['Billing issues'] for review in support)} billing/credit complaint(s) were identified.",
                    "Standardise evidence collection and communicate the decision owner, review time, and outcome for payments, invoices, and service credits.",
                    "Reduces financial uncertainty and increases trust that paid service time is protected.",
                ],
            ],
        ),
        "",
        "### LOW PRIORITY",
        "",
        shared._table(
            ["Issue", "Evidence", "Recommended action", "Expected customer impact"],
            [
                [
                    "Inbox attribution gaps",
                    f"{sum(review.base.primary_inbox_id is None for review in support)} support conversation(s) lacked a configured inbox target.",
                    "After this report is accepted, define a canonical inbox identity for unconfigured channels without modifying historical conversations.",
                    "Improves future channel accountability and trend analysis.",
                ],
                [
                    "Reporting controls",
                    "This is the first Support run of the reusable weekly read-only logic.",
                    "Review representative classifications before considering any scheduling or automation.",
                    "Improves confidence in weekly support metrics before operational rollout.",
                ],
            ],
        ),
        "",
        "## Validation and Warnings",
        "",
        "All required reconciliation checks passed: active inbox coverage, inbound support coverage, complaint totals, sentiment totals, resolution totals, happiness totals, and agent totals.",
        "",
        *(f"- {warning}" for warning in warnings),
        "",
        "*Customer evidence is deliberately paraphrased and identified only by conversation ID.*",
    ]
    return "\n".join(lines).strip() + "\n"


def generate(output_dir: Path, now: datetime | None = None) -> dict[str, Any]:
    as_of = now or datetime.now(UTC)
    start_local, end_local, start_utc, end_utc = shared.previous_complete_week(as_of)
    db = shared.SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        data = shared.collect_data(db, start_utc, end_utc)
        all_reviews = shared.build_reviews(data, start_utc, end_utc)
        support = classify_support(all_reviews, data, start_utc, end_utc)
        checks = validate(all_reviews, support, data["active_inboxes"])
        warnings = _warnings(all_reviews, support, data)
        markdown = build_markdown(
            all_reviews,
            support,
            data["active_inboxes"],
            start_local,
            end_local,
            warnings,
        )
    finally:
        db.rollback()
        db.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / f"{REPORT_NAME}.md"
    pdf_path = output_dir / f"{REPORT_NAME}.pdf"
    markdown_path.write_text(markdown, encoding="utf-8")
    period = f"{start_local:%d %B %Y} - {(end_local - timedelta(seconds=1)):%d %B %Y}"
    report_html = shared.markdown_to_html(markdown, period, scope_label="Support inbound experience only")
    from weasyprint import HTML

    document = HTML(string=report_html, base_url=str(output_dir)).render()
    document.write_pdf(str(pdf_path))
    return {
        "reporting_period": period,
        "total_support_conversations_reviewed": checks["support_reviewed"],
        "total_inbound_conversations_reviewed": checks["inbound_reviewed"],
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
    parser.add_argument("--as-of", help="Optional ISO-8601 instant for deterministic validation; defaults to now.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    now = shared._coerce_datetime(args.as_of) if args.as_of else None
    try:
        result = generate(args.output_dir, now=now)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=True))
        return 1
    print(json.dumps({"status": "ok", **result}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
