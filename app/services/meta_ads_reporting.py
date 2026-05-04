"""Meta ads outcome report payloads for PDF export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class MetaAdsMetric:
    label: str
    value: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class MetaAdsConversationExample:
    title: str
    outcome: str
    detail: str


def build_meta_ads_outcome_report() -> dict:
    """Return the curated Meta ads outcome report for the latest reviewed week.

    This report is intentionally fixed to the investigated campaign window so the
    generated PDF matches the transcript review already completed for management.
    """

    generated_at = datetime.now(UTC)
    return {
        "title": "Meta Ads Outcome Report",
        "subtitle": "Conversation, lead, and conversion review",
        "date_range_label": "April 28, 2026 to May 4, 2026",
        "generated_at": generated_at,
        "ad_ids": [
            "6963151237936",
            "6963095784536",
            "6963059771336",
        ],
        "headline_metrics": [
            MetaAdsMetric("Total Conversations Reviewed", "200", "Messenger and Instagram only"),
            MetaAdsMetric("Recorded Form Leads", "68", "Hard-attributed CRM leads"),
            MetaAdsMetric(
                "Sales Prospect Conversations", "73", "Complaints, jobs, and existing-customer chats excluded"
            ),
            MetaAdsMetric("Confirmed In-Area Prospects", "17", "Agent explicitly confirmed service availability"),
            MetaAdsMetric("Qualified Prospects", "9", "Covered locations that received concrete offers"),
            MetaAdsMetric("Warm Planning-To-Buy Prospects", "5", "Intent shown, but not converted yet"),
        ],
        "lead_metrics": [
            MetaAdsMetric("Total Recorded Leads", "68"),
            MetaAdsMetric("Qualified CRM Leads", "0"),
            MetaAdsMetric("Closed CRM Leads", "0"),
            MetaAdsMetric("Leads Still New", "68"),
            MetaAdsMetric("Unassigned Leads", "64"),
            MetaAdsMetric("Assigned Leads", "4"),
        ],
        "conversation_metrics": [
            MetaAdsMetric("Messenger Conversations", "180"),
            MetaAdsMetric("Instagram Conversations", "20"),
            MetaAdsMetric("Resolved Conversations", "193"),
            MetaAdsMetric("Open or Pending Conversations", "7"),
            MetaAdsMetric("Unique People Reached", "158"),
            MetaAdsMetric("Repeat Conversation Threads", "42"),
        ],
        "prospect_metrics": [
            MetaAdsMetric("True Prospect Conversations", "73"),
            MetaAdsMetric("Unique Prospect Accounts", "70"),
            MetaAdsMetric("Confirmed In Service Area", "17"),
            MetaAdsMetric("Confirmed Outside Service Area", "7"),
            MetaAdsMetric("Coverage Still Pending", "2"),
            MetaAdsMetric("Quoted In-Area Prospects", "9"),
            MetaAdsMetric("In-Area Price Objections", "2"),
            MetaAdsMetric("In-Area Warm / High Intent", "3"),
        ],
        "channel_mix": [
            MetaAdsMetric("Messenger Prospect Conversations", "66"),
            MetaAdsMetric("Instagram Prospect Conversations", "7"),
            MetaAdsMetric("Facebook Lead Forms", "53"),
            MetaAdsMetric("Instagram Lead Forms", "15"),
        ],
        "attribution_summary": [
            "CRM reliably stored 68 lead-form submissions for ad ID 6963151237936.",
            "CRM did not preserve ad IDs on most Messenger and Instagram chats, so social conversations were reviewed by campaign-week transcript analysis rather than strict ad-ID joins.",
            "Three Instagram DM threads retained direct ad attribution inside CRM; the broader social outcome was assessed by reading all sales-like Messenger and Instagram chats for the week.",
        ],
        "executive_summary": [
            "Meta ads produced strong top-of-funnel volume through 68 hard-recorded lead forms and 73 additional social prospect conversations in the same campaign week.",
            "The strongest outcome came from service-area discovery and pricing enquiries, especially around Lagos and Abuja clusters.",
            "Commercial conversion is underperforming after enquiry: none of the 68 CRM leads have been qualified or closed, and price resistance around the installation fee is the clearest blocker among in-area prospects.",
        ],
        "ad_breakdown": [
            {
                "ad_id": "6963151237936",
                "summary": "Primary converting ad",
                "highlights": [
                    "68 recorded lead-form submissions",
                    "53 Facebook lead forms and 15 Instagram lead forms",
                    "No CRM lead has progressed beyond new status",
                ],
            },
            {
                "ad_id": "6963095784536",
                "summary": "Low retained social attribution",
                "highlights": [
                    "1 directly attributable Instagram DM thread in CRM",
                    "No lead created from that thread",
                ],
            },
            {
                "ad_id": "6963059771336",
                "summary": "Low retained social attribution",
                "highlights": [
                    "2 directly attributable Instagram DM threads in CRM",
                    "One thread showed real buying intent but was not converted into a CRM lead",
                ],
            },
        ],
        "finding_sections": [
            {
                "title": "What Prospects Asked",
                "points": [
                    "Coverage and area availability dominated the conversations.",
                    "Pricing and installation cost questions came immediately after coverage confirmation.",
                    "Lagos and Abuja locations produced the clearest serviceable opportunities.",
                    "Many out-of-area enquiries came from expansion cities such as Uyo, Port Harcourt, Owerri, Onitsha, Warri, Benin, and Makurdi.",
                ],
            },
            {
                "title": "What Happened After Enquiry",
                "points": [
                    "17 prospects were explicitly confirmed to be within service area.",
                    "9 of those in-area prospects received concrete pricing or installation offers and are the closest thing to qualified sales opportunities.",
                    "5 prospects showed clear planning-to-buy signals, but the pipeline did not convert them into qualified CRM leads.",
                    "2 covered prospects pushed back specifically on the installation fee after offer presentation.",
                ],
            },
            {
                "title": "What Limited Conversion",
                "points": [
                    "Installation cost was the most consistent commercial objection.",
                    "Serious Messenger and Instagram sales chats were often answered, but not converted into structured CRM leads.",
                    "The 68 recorded form leads were not progressed through qualification, ownership, or close workflow.",
                ],
            },
        ],
        "example_threads": [
            MetaAdsConversationExample(
                "Karu, Abuja Instagram prospect",
                "Covered, quoted, then price-sensitive",
                "Prospect shared address and live location, received Air Fiber availability plus plan information, then challenged the installation fee.",
            ),
            MetaAdsConversationExample(
                "Ojo, Lagos Instagram prospect",
                "Strong residential buying intent",
                "Prospect said they were a new customer, wanted home internet for work, shared location, asked for subscription pricing, and received a concrete installation offer.",
            ),
            MetaAdsConversationExample(
                "Mpape/Bwari, Abuja Messenger prospect",
                "Commercial SME prospect",
                "Prospect described high-capacity need, shared location details, received a fiber offer and pricing, but reacted negatively to the total cost.",
            ),
            MetaAdsConversationExample(
                "Ikoyi / Dolphin Estate Messenger prospect",
                "Covered and open for follow-up",
                "Prospect shared address, coverage was confirmed, and installation pricing was given; the thread remains commercially live.",
            ),
            MetaAdsConversationExample(
                "Kurudu Instagram prospect",
                "Out of area",
                "Prospect shared live location, but the team replied that clear coverage was not currently available.",
            ),
        ],
        "recommendations": [
            "Tighten geo targeting around serviceable Lagos and Abuja clusters instead of broad national reach.",
            "Rewrite ad and sales messaging around installation cost so prospects understand why setup pricing is higher than monthly subscription.",
            "Force immediate CRM lead creation and assignment for every serious Messenger and Instagram sales conversation.",
            "Create a separate follow-up playbook for in-area price-objection prospects because these are not cold leads; they are stalled opportunities.",
        ],
        "footer_note": (
            "Prepared from CRM lead records plus manual transcript review of Messenger and Instagram conversations "
            "received during the campaign week."
        ),
    }
