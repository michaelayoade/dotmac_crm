from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.projects import Project
from app.models.vendor import InstallationProject, ProjectQuote, QuoteLineItem, Vendor
from app.services.ai.redaction import redact_text
from app.services.common import coerce_uuid


def gather_vendor_context(db: Session, params: dict[str, Any]) -> str:
    """
    Context builder for vendor quote analysis.

    Required params:
      - quote_id: UUID
    """
    quote_id = params.get("quote_id")
    if not quote_id:
        raise ValueError("quote_id is required")

    quote = db.get(ProjectQuote, coerce_uuid(quote_id))
    if not quote:
        raise ValueError("Quote not found")

    max_line_items = min(int(params.get("max_line_items", 25)), 80)
    max_chars = int(params.get("max_chars", 700))

    vendor = db.get(Vendor, quote.vendor_id) if quote.vendor_id else None
    inst_project = db.get(InstallationProject, quote.project_id) if quote.project_id else None
    base_project = db.get(Project, inst_project.project_id) if inst_project and inst_project.project_id else None

    lines: list[str] = [
        f"Quote ID: {str(quote.id)[:8]}",
        f"Status: {quote.status.value if hasattr(quote.status, 'value') else str(quote.status)}",
        f"Currency: {redact_text(quote.currency or '', max_chars=10)}",
        f"Subtotal: {quote.subtotal!s}",
        f"Tax total: {quote.tax_total!s}",
        f"Total: {quote.total!s}",
        f"Valid from: {quote.valid_from.isoformat() if quote.valid_from else 'unknown'}",
        f"Valid until: {quote.valid_until.isoformat() if quote.valid_until else 'unknown'}",
        f"Submitted at: {quote.submitted_at.isoformat() if quote.submitted_at else 'not submitted'}",
        f"Reviewed at: {quote.reviewed_at.isoformat() if quote.reviewed_at else 'not reviewed'}",
        f"Review notes: {redact_text(quote.review_notes or '', max_chars=600)}",
    ]

    if vendor:
        lines.extend(
            [
                f"Vendor: {redact_text(vendor.name or '', max_chars=160)}",
                f"Vendor code: {redact_text(vendor.code or '', max_chars=60)}",
                f"Vendor service area: {redact_text(vendor.service_area or '', max_chars=240)}",
            ]
        )

    if inst_project:
        lines.extend(
            [
                f"Installation project ID: {str(inst_project.id)[:8]}",
                f"Installation project status: {inst_project.status.value if hasattr(inst_project.status, 'value') else str(inst_project.status)}",
                f"Assignment type: {inst_project.assignment_type.value if inst_project.assignment_type else 'unknown'}",
                f"Project notes: {redact_text(inst_project.notes or '', max_chars=400)}",
            ]
        )

    if base_project:
        lines.extend(
            [
                f"Project name: {redact_text(base_project.name or '', max_chars=180)}",
                f"Project status: {base_project.status.value if hasattr(base_project.status, 'value') else str(base_project.status)}",
                f"Project priority: {base_project.priority.value if hasattr(base_project.priority, 'value') else str(base_project.priority)}",
                f"Project description: {redact_text(base_project.description or '', max_chars=max_chars)}",
            ]
        )

    items = (
        db.query(QuoteLineItem)
        .filter(QuoteLineItem.quote_id == quote.id)
        .filter(QuoteLineItem.is_active.is_(True))
        .order_by(QuoteLineItem.created_at.asc())
        .limit(max(0, max_line_items))
        .all()
    )
    if items:
        lines.append("Line items:")
        for it in items:
            desc = redact_text(it.description or "", max_chars=260)
            qty = str(it.quantity)
            unit = str(it.unit_price)
            amt = str(it.amount)
            cable = redact_text(it.cable_type or "", max_chars=60)
            notes = redact_text(it.notes or "", max_chars=220)
            meta = ", ".join(
                [p for p in [f"type={it.item_type}" if it.item_type else "", f"cable={cable}" if cable else ""] if p]
            )
            lines.append(f"  - {meta} qty={qty} unit={unit} amount={amt} desc={desc} notes={notes}")

    # Keep a short, redacted summary of what we want.
    if params.get("goal"):
        lines.append("Goal:")
        lines.append(redact_text(str(params["goal"]), max_chars=260))

    return "\n".join([line for line in lines if line.strip()])
