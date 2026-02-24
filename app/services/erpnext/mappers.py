"""ERPNext to DotMac data mappers.

Maps ERPNext doctypes to DotMac model schemas:
- HD Ticket → Ticket
- Project → Project
- Task → ProjectTask
- Contact → Person
- Customer → Organization + Subscriber
- Lead → CRM Lead
- Quotation → CRM Quote
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, TypeVar

from app.logging import get_logger
from app.models.crm.sales import LeadStatus
from app.models.person import Gender
from app.models.projects import ProjectPriority, ProjectStatus, TaskPriority, TaskStatus
from app.models.tickets import TicketChannel, TicketPriority, TicketStatus

logger = get_logger(__name__)


def _parse_date(value: str | None) -> date | None:
    """Parse ERPNext date string to date object."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse ERPNext datetime string to datetime object."""
    if not value:
        return None
    try:
        # ERPNext uses various datetime formats
        for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None
    except (ValueError, TypeError):
        return None


def _clean_html(value: str | None) -> str | None:
    """Strip HTML tags from ERPNext rich text fields."""
    if not value:
        return None
    # Simple HTML stripping - for production use a proper HTML parser
    import re

    clean = re.sub(r"<[^>]+>", "", value)
    return clean.strip() or None


T = TypeVar("T")


def _map_lookup(mapping: dict[str, T], value: Any, default: T) -> T:
    if isinstance(value, str):
        return mapping.get(value, default)
    return default


# -----------------------------------------------------------------------------
# Status Mappings
# -----------------------------------------------------------------------------

HD_TICKET_STATUS_MAP = {
    "Open": TicketStatus.open,
    "Replied": TicketStatus.pending,
    "Resolved": TicketStatus.resolved,
    "Closed": TicketStatus.closed,
    "On Hold": TicketStatus.on_hold,
}

HD_TICKET_PRIORITY_MAP = {
    "Low": TicketPriority.low,
    "Medium": TicketPriority.normal,
    "High": TicketPriority.high,
    "Urgent": TicketPriority.urgent,
}

PROJECT_STATUS_MAP = {
    "Open": ProjectStatus.open,
    "Completed": ProjectStatus.completed,
    "Cancelled": ProjectStatus.canceled,
    "Overdue": ProjectStatus.active,  # Map to active with flag
}

PROJECT_PRIORITY_MAP = {
    "Low": ProjectPriority.low,
    "Medium": ProjectPriority.normal,
    "High": ProjectPriority.high,
}

TASK_STATUS_MAP = {
    "Open": TaskStatus.todo,
    "Working": TaskStatus.in_progress,
    "Pending Review": TaskStatus.in_progress,
    "Overdue": TaskStatus.in_progress,
    "Completed": TaskStatus.done,
    "Cancelled": TaskStatus.canceled,
}

TASK_PRIORITY_MAP = {
    "Low": TaskPriority.low,
    "Medium": TaskPriority.normal,
    "High": TaskPriority.high,
    "Urgent": TaskPriority.urgent,
}

LEAD_STATUS_MAP = {
    "Lead": LeadStatus.new,
    "Open": LeadStatus.contacted,
    "Replied": LeadStatus.contacted,
    "Opportunity": LeadStatus.qualified,
    "Quotation": LeadStatus.proposal,
    "Lost Quotation": LeadStatus.lost,
    "Interested": LeadStatus.qualified,
    "Converted": LeadStatus.won,
    "Do Not Contact": LeadStatus.lost,
}

GENDER_MAP = {
    "Male": Gender.male,
    "Female": Gender.female,
    "Other": Gender.other,
    "Prefer not to say": Gender.unknown,
}


# -----------------------------------------------------------------------------
# Doctype Mappers
# -----------------------------------------------------------------------------


def map_hd_ticket(doc: dict[str, Any]) -> dict[str, Any]:
    """Map ERPNext HD Ticket to DotMac Ticket schema.

    ERPNext HD Ticket fields:
    - name: Ticket ID (e.g., "HD-TICKET-00001")
    - subject: Ticket subject/title
    - description: Detailed description
    - status: Open, Replied, Resolved, Closed
    - priority: Low, Medium, High, Urgent
    - raised_by: Email of person who raised ticket
    - customer: Link to Customer doctype
    - contact: Link to Contact doctype
    - resolution_details: Resolution notes
    - opening_date: When ticket was created
    - resolution_date: When resolved
    """
    return {
        "title": doc.get("subject") or doc.get("name"),
        "description": _clean_html(doc.get("description")),
        "status": _map_lookup(HD_TICKET_STATUS_MAP, doc.get("status"), TicketStatus.new),
        "priority": _map_lookup(HD_TICKET_PRIORITY_MAP, doc.get("priority"), TicketPriority.normal),
        "channel": TicketChannel.email if doc.get("raised_by") else TicketChannel.web,
        "tags": [doc.get("ticket_type")] if doc.get("ticket_type") else None,
        "resolution_notes": _clean_html(doc.get("resolution_details")),
        "is_active": doc.get("status") not in ["Closed", "Cancelled"],
        # Metadata for reference
        "_erpnext_name": doc.get("name"),
        "_erpnext_customer": doc.get("customer"),
        "_erpnext_contact": doc.get("contact"),
        "_erpnext_raised_by": doc.get("raised_by"),
        "_erpnext_creation": doc.get("creation"),
        "_erpnext_modified": doc.get("modified"),
    }


def map_project(doc: dict[str, Any]) -> dict[str, Any]:
    """Map ERPNext Project to DotMac Project schema.

    ERPNext Project fields:
    - name: Project ID
    - project_name: Display name
    - status: Open, Completed, Cancelled
    - priority: Low, Medium, High
    - expected_start_date, expected_end_date
    - actual_start_date, actual_end_date
    - percent_complete
    - notes: Project notes/description
    - customer: Link to Customer
    """
    return {
        "name": doc.get("project_name") or doc.get("name"),
        "description": _clean_html(doc.get("notes")),
        "status": _map_lookup(PROJECT_STATUS_MAP, doc.get("status"), ProjectStatus.active),
        "priority": _map_lookup(PROJECT_PRIORITY_MAP, doc.get("priority"), ProjectPriority.normal),
        "start_at": _parse_datetime(doc.get("actual_start_date") or doc.get("expected_start_date")),
        "due_at": _parse_datetime(doc.get("actual_end_date") or doc.get("expected_end_date")),
        "progress_percent": doc.get("percent_complete"),
        "is_active": doc.get("status") not in ["Cancelled"],
        # Metadata
        "_erpnext_name": doc.get("name"),
        "_erpnext_customer": doc.get("customer"),
        "_erpnext_creation": doc.get("creation"),
        "_erpnext_modified": doc.get("modified"),
    }


def map_task(doc: dict[str, Any]) -> dict[str, Any]:
    """Map ERPNext Task to DotMac ProjectTask schema.

    ERPNext Task fields:
    - name: Task ID
    - subject: Task title
    - status: Open, Working, Pending Review, Completed, Cancelled
    - priority: Low, Medium, High, Urgent
    - project: Link to Project
    - exp_start_date, exp_end_date
    - description
    - progress: 0-100
    - parent_task: For subtasks
    """
    return {
        "name": doc.get("subject") or doc.get("name"),
        "description": _clean_html(doc.get("description")),
        "status": _map_lookup(TASK_STATUS_MAP, doc.get("status"), TaskStatus.todo),
        "priority": _map_lookup(TASK_PRIORITY_MAP, doc.get("priority"), TaskPriority.normal),
        "start_at": _parse_datetime(doc.get("exp_start_date")),
        "due_date": _parse_date(doc.get("exp_end_date")),
        "progress_percent": doc.get("progress"),
        "is_active": doc.get("status") not in ["Cancelled"],
        # Metadata
        "_erpnext_name": doc.get("name"),
        "_erpnext_project": doc.get("project"),
        "_erpnext_parent_task": doc.get("parent_task"),
        "_erpnext_creation": doc.get("creation"),
        "_erpnext_modified": doc.get("modified"),
    }


def map_contact(doc: dict[str, Any]) -> dict[str, Any]:
    """Map ERPNext Contact to DotMac Person schema.

    ERPNext Contact fields:
    - name: Contact ID
    - first_name, middle_name, last_name
    - email_id: Primary email
    - phone, mobile_no
    - gender
    - salutation
    - company_name
    - address: Link to Address
    - email_ids: Child table of emails
    - phone_nos: Child table of phone numbers
    """
    # Get primary email from email_ids child table or email_id field
    email = doc.get("email_id")
    if not email and doc.get("email_ids"):
        for e in doc["email_ids"]:
            if e.get("is_primary"):
                email = e.get("email_id")
                break
        if not email and doc["email_ids"]:
            email = doc["email_ids"][0].get("email_id")

    # Get primary phone
    phone = doc.get("mobile_no") or doc.get("phone")
    if not phone and doc.get("phone_nos"):
        for p in doc["phone_nos"]:
            if p.get("is_primary_mobile_no") or p.get("is_primary_phone"):
                phone = p.get("phone")
                break
        if not phone and doc["phone_nos"]:
            phone = doc["phone_nos"][0].get("phone")

    first_name = doc.get("first_name") or ""
    last_name = doc.get("last_name") or ""

    # If no last name, try to split full name
    if not last_name and first_name and " " in first_name:
        parts = first_name.rsplit(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

    # Truncate to fit database column constraints (VARCHAR(80))
    first_name = (first_name or "Unknown")[:80]
    last_name = (last_name or "")[:80]

    return {
        "first_name": first_name,
        "last_name": last_name,
        "email": email or f"{doc.get('name', 'unknown')}@placeholder.local",
        "phone": phone,
        "gender": _map_lookup(GENDER_MAP, doc.get("gender"), Gender.unknown),
        "is_active": True,
        # Metadata
        "_erpnext_name": doc.get("name"),
        "_erpnext_company": doc.get("company_name"),
        "_erpnext_creation": doc.get("creation"),
        "_erpnext_modified": doc.get("modified"),
    }


def map_customer(doc: dict[str, Any]) -> dict[str, Any]:
    """Map ERPNext Customer to DotMac Organization schema.

    ERPNext Customer fields:
    - name: Customer ID
    - customer_name: Display name
    - customer_type: Company or Individual
    - customer_group
    - territory
    - tax_id
    - website
    - primary_address, customer_primary_address
    - primary_contact, customer_primary_contact
    """
    # Extract Splynx ID from custom fields (Frappe custom fields are prefixed custom_*)
    splynx_id = doc.get("custom_splynx_id") or doc.get("custom_splynx_customer_id")
    if not splynx_id:
        logger.debug("map_customer no splynx_id found for customer=%s", doc.get("name"))

    return {
        "name": doc.get("customer_name") or doc.get("name"),
        "legal_name": doc.get("customer_name"),
        "tax_id": doc.get("tax_id"),
        "website": doc.get("website"),
        "notes": f"Customer Group: {doc.get('customer_group', 'N/A')}\nTerritory: {doc.get('territory', 'N/A')}",
        "_erpnext_splynx_id": splynx_id,
        # Metadata
        "_erpnext_name": doc.get("name"),
        "_erpnext_customer_type": doc.get("customer_type"),
        "_erpnext_customer_group": doc.get("customer_group"),
        "_erpnext_territory": doc.get("territory"),
        "_erpnext_primary_contact": doc.get("customer_primary_contact"),
        "_erpnext_creation": doc.get("creation"),
        "_erpnext_modified": doc.get("modified"),
    }


def map_lead(doc: dict[str, Any]) -> dict[str, Any]:
    """Map ERPNext Lead to DotMac CRM Lead schema.

    ERPNext Lead fields:
    - name: Lead ID
    - lead_name: Full name
    - company_name
    - email_id
    - mobile_no, phone
    - status: Lead, Open, Replied, Opportunity, etc.
    - source: Lead source
    - territory
    - notes
    """
    return {
        "title": doc.get("company_name") or doc.get("lead_name") or doc.get("name"),
        "status": _map_lookup(LEAD_STATUS_MAP, doc.get("status"), LeadStatus.new),
        "source": doc.get("source"),
        "notes": _clean_html(doc.get("notes")),
        "is_active": doc.get("status") not in ["Do Not Contact", "Converted"],
        # Contact info for person creation
        "_contact_name": doc.get("lead_name"),
        "_contact_email": doc.get("email_id"),
        "_contact_phone": doc.get("mobile_no") or doc.get("phone"),
        "_contact_company": doc.get("company_name"),
        # Metadata
        "_erpnext_name": doc.get("name"),
        "_erpnext_source": doc.get("source"),
        "_erpnext_territory": doc.get("territory"),
        "_erpnext_creation": doc.get("creation"),
        "_erpnext_modified": doc.get("modified"),
    }


def map_quotation(doc: dict[str, Any]) -> dict[str, Any]:
    """Map ERPNext Quotation to DotMac CRM Quote schema.

    ERPNext Quotation fields:
    - name: Quotation ID (e.g., "QTN-00001")
    - party_name: Customer or Lead name
    - quotation_to: Customer or Lead
    - status: Draft, Open, Replied, Ordered, Lost, Cancelled
    - transaction_date
    - valid_till
    - grand_total, net_total
    - currency
    - items: Child table of line items
    - terms: Terms and conditions
    """
    from decimal import Decimal

    # Map status
    status_map = {
        "Draft": "draft",
        "Open": "sent",
        "Replied": "sent",
        "Ordered": "accepted",
        "Lost": "rejected",
        "Cancelled": "rejected",
        "Expired": "expired",
    }

    # Extract line items
    items = []
    for item in doc.get("items", []):
        items.append(
            {
                "description": item.get("item_name") or item.get("description"),
                "quantity": item.get("qty", 1),
                "unit_price": Decimal(str(item.get("rate", 0))),
                "amount": Decimal(str(item.get("amount", 0))),
                "_erpnext_item_code": item.get("item_code"),
            }
        )

    return {
        "quote_number": doc.get("name"),
        "status": _map_lookup(status_map, doc.get("status"), "draft"),
        "valid_until": _parse_date(doc.get("valid_till")),
        "subtotal": Decimal(str(doc.get("net_total", 0))),
        "total": Decimal(str(doc.get("grand_total", 0))),
        "currency": doc.get("currency", "USD"),
        "terms": _clean_html(doc.get("terms")),
        "is_active": doc.get("status") not in ["Cancelled"],
        "_items": items,
        # Metadata
        "_erpnext_name": doc.get("name"),
        "_erpnext_party_name": doc.get("party_name"),
        "_erpnext_quotation_to": doc.get("quotation_to"),
        "_erpnext_creation": doc.get("creation"),
        "_erpnext_modified": doc.get("modified"),
    }


# -----------------------------------------------------------------------------
# Child-Table / Communication Mappers
# -----------------------------------------------------------------------------


def map_hd_ticket_comment(comment_doc: dict[str, Any]) -> dict[str, Any]:
    """Map an ERPNext HD Ticket comments child-table row to DotMac TicketComment.

    ERPNext HD Ticket child table fields:
    - name: Child row ID
    - comment: Comment body (HTML)
    - comment_by: Email of commenter
    - comment_type: "Comment", "Info", etc.
    - creation: Timestamp
    """
    return {
        "body": _clean_html(comment_doc.get("comment") or comment_doc.get("content")) or "",
        "is_internal": comment_doc.get("comment_type", "Comment") != "Comment",
        "_erpnext_name": comment_doc.get("name"),
        "_erpnext_comment_by": comment_doc.get("comment_by"),
        "_erpnext_creation": comment_doc.get("creation"),
    }


def map_communication(comm_doc: dict[str, Any]) -> dict[str, Any]:
    """Map a Frappe Communication document to DotMac TicketComment.

    Communication doctype (email threads linked to HD Tickets):
    - name: Communication ID
    - subject: Email subject
    - content: Email body (HTML)
    - sender: Sender email
    - sent_or_received: "Sent" or "Received"
    - creation: Timestamp
    """
    subject = comm_doc.get("subject") or ""
    content = _clean_html(comm_doc.get("content")) or ""
    body = f"**{subject}**\n\n{content}" if subject else content

    return {
        "body": body,
        "is_internal": comm_doc.get("sent_or_received") == "Sent",
        "_erpnext_name": comm_doc.get("name"),
        "_erpnext_sender": comm_doc.get("sender"),
        "_erpnext_creation": comm_doc.get("creation"),
    }


def map_project_comment(comment_doc: dict[str, Any]) -> dict[str, Any]:
    """Map an ERPNext Project/Task comments child-table row to DotMac ProjectComment.

    Same structure as ticket comment mapper — used for both Project and Task comments.
    """
    return {
        "body": _clean_html(comment_doc.get("comment") or comment_doc.get("content")) or "",
        "is_internal": comment_doc.get("comment_type", "Comment") != "Comment",
        "_erpnext_name": comment_doc.get("name"),
        "_erpnext_comment_by": comment_doc.get("comment_by"),
        "_erpnext_creation": comment_doc.get("creation"),
    }


# -----------------------------------------------------------------------------
# Field Lists for API Queries
# -----------------------------------------------------------------------------

HD_TICKET_FIELDS = [
    "name",
    "subject",
    "description",
    "status",
    "priority",
    "raised_by",
    "customer",
    "contact",
    "ticket_type",
    "resolution_details",
    "opening_date",
    "resolution_date",
    "creation",
    "modified",
]

PROJECT_FIELDS = [
    "name",
    "project_name",
    "status",
    "priority",
    "notes",
    "expected_start_date",
    "expected_end_date",
    "actual_start_date",
    "actual_end_date",
    "percent_complete",
    "customer",
    "creation",
    "modified",
]

TASK_FIELDS = [
    "name",
    "subject",
    "status",
    "priority",
    "description",
    "project",
    "exp_start_date",
    "exp_end_date",
    "progress",
    "parent_task",
    "creation",
    "modified",
]

CONTACT_FIELDS = [
    "name",
    "first_name",
    "middle_name",
    "last_name",
    "email_id",
    "phone",
    "mobile_no",
    "gender",
    "salutation",
    "company_name",
    "creation",
    "modified",
]

CUSTOMER_FIELDS = [
    "name",
    "customer_name",
    "customer_type",
    "customer_group",
    "territory",
    "tax_id",
    "website",
    "customer_primary_contact",
    "customer_primary_address",
    "creation",
    "modified",
]

LEAD_FIELDS = [
    "name",
    "lead_name",
    "company_name",
    "email_id",
    "mobile_no",
    "phone",
    "status",
    "source",
    "territory",
    "notes",
    "creation",
    "modified",
]

QUOTATION_FIELDS = [
    "name",
    "party_name",
    "quotation_to",
    "status",
    "transaction_date",
    "valid_till",
    "net_total",
    "grand_total",
    "currency",
    "terms",
    "creation",
    "modified",
]
