"""Dedicated billing risk report service functions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from threading import Lock
from time import monotonic
from typing import Any

from sqlalchemy import Date, cast, false, func, or_, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.person import ChannelType as PersonChannelType
from app.models.person import Person, PersonChannel
from app.models.sales_order import SalesOrder, SalesOrderPaymentStatus
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.tickets import Ticket, TicketStatus
from app.services import subscriber_reports as subscriber_reports_service

_clean_report_name = subscriber_reports_service._clean_report_name
_coerce_datetime_utc = subscriber_reports_service._coerce_datetime_utc
_dedupe_churn_rows = subscriber_reports_service._dedupe_churn_rows
_days_since_expr = subscriber_reports_service._days_since_expr
_looks_like_noise_name = subscriber_reports_service._looks_like_noise_name
_metadata_text = subscriber_reports_service._metadata_text
_parse_balance_amount = subscriber_reports_service._parse_balance_amount
_parse_iso_date_text = subscriber_reports_service._parse_iso_date_text

_SPYLNX_LIVE_CACHE_TTLS = {
    "fetch_customers": 60.0,
    "fetch_customer_billing": 300.0,
    "fetch_customer_internet_services": 300.0,
}
_SPYLNX_LIVE_CACHE: dict[tuple[str, tuple[object, ...]], tuple[float, Any]] = {}
_SPYLNX_LIVE_CACHE_LOCK = Lock()
_BILLING_RISK_SEGMENT_ORDER = ["Due Soon", "Suspended", "Churned", "Pending"]
_OPEN_TICKET_STATUSES = (
    TicketStatus.new,
    TicketStatus.open,
    TicketStatus.pending,
    TicketStatus.waiting_on_customer,
    TicketStatus.lastmile_rerun,
    TicketStatus.site_under_construction,
    TicketStatus.on_hold,
)
_FINAL_TICKET_STATUSES = (TicketStatus.closed, TicketStatus.canceled, TicketStatus.merged)


def clear_live_splynx_cache() -> None:
    with _SPYLNX_LIVE_CACHE_LOCK:
        _SPYLNX_LIVE_CACHE.clear()


def _coerce_nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _cached_live_splynx_read(cache_name: str, loader, *args, cache_scope: object | None = None):
    ttl_seconds = _SPYLNX_LIVE_CACHE_TTLS.get(cache_name, 0.0)
    if ttl_seconds <= 0:
        return loader()

    cache_key = (cache_name, ((cache_scope or ""), *(str(arg) for arg in args)))
    now = monotonic()
    with _SPYLNX_LIVE_CACHE_LOCK:
        cached_entry = _SPYLNX_LIVE_CACHE.get(cache_key)
        if cached_entry is not None:
            expires_at, cached_value = cached_entry
            if expires_at > now:
                return deepcopy(cached_value)
            _SPYLNX_LIVE_CACHE.pop(cache_key, None)

    loaded_value = loader()
    with _SPYLNX_LIVE_CACHE_LOCK:
        _SPYLNX_LIVE_CACHE[cache_key] = (now + ttl_seconds, deepcopy(loaded_value))
    return deepcopy(loaded_value)


def _format_phone_display(raw_value: object) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""

    def _normalize_phone_part(part: str) -> str:
        candidate = part.strip()
        if not candidate:
            return ""
        digits = re.sub(r"\D+", "", candidate)
        if not digits:
            return candidate
        if digits.startswith("234") and len(digits) == 13:
            return f"+{digits}"
        if digits.startswith("0") and len(digits) == 11:
            return f"+234{digits[1:]}"
        if len(digits) == 10:
            return f"+234{digits}"
        return candidate if candidate.startswith("+") else digits

    normalized = re.sub(r"[\n;/|]+", ",", text)
    parts = [part.strip() for part in normalized.split(",") if part.strip()]
    if len(parts) <= 1:
        return _normalize_phone_part(parts[0]) if parts else text
    deduped_parts: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized_part = _normalize_phone_part(part)
        if not normalized_part or normalized_part in seen:
            continue
        seen.add(normalized_part)
        deduped_parts.append(normalized_part)
    return ", ".join(deduped_parts)


def get_billing_risk_table(
    db: Session,
    *,
    due_soon_days: int = 7,
    high_balance_only: bool = False,
    segment: str | None = None,
    segments: list[str] | None = None,
    days_past_due: str | None = None,
    limit: int = 500,
    page: int = 1,
    page_size: int | None = None,
    search: str | None = None,
    overdue_bucket: str | None = None,
    enrich_visible_rows: bool = True,
) -> list[dict]:
    """Billing risk rows sourced from live Splynx data."""
    from app.services.splynx import (
        _select_primary_service,
        fetch_customer_billing,
        fetch_customer_internet_services,
        fetch_customers,
        map_customer_to_subscriber_data,
    )

    today = datetime.now(UTC).date()

    def _normalize_segment(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        normalized_segment = value.strip().lower()
        if normalized_segment in {"due_soon", "due soon", "overdue"}:
            return "Due Soon"
        if normalized_segment == "suspended":
            return "Suspended"
        if normalized_segment == "churned":
            return "Churned"
        if normalized_segment == "pending":
            return "Pending"
        return None

    selected_segments: set[str] = set()
    normalized_single = _normalize_segment(segment)
    if normalized_single is not None:
        selected_segments.add(normalized_single)
    for raw_segment in segments or []:
        for candidate in str(raw_segment).split(","):
            normalized = _normalize_segment(candidate)
            if normalized is not None:
                selected_segments.add(normalized)

    normalized_days_past_due = (
        (days_past_due if isinstance(days_past_due, str) else "").strip().lower().replace("_", "-")
    )
    if normalized_days_past_due in {"current", "0"}:
        selected_days_past_due_category = "0"
    elif normalized_days_past_due in {"1-7", "1-to-7", "1 to 7", "within-7", "within7"}:
        selected_days_past_due_category = "1-7"
    elif normalized_days_past_due in {"8-30", "8-to-30", "8 to 30"}:
        selected_days_past_due_category = "8-30"
    elif normalized_days_past_due in {"31+", "31-plus", "31-and-above", "over30", "over-30", "31"}:
        selected_days_past_due_category = "31+"
    else:
        selected_days_past_due_category = None

    def _days_past_due_bucket(value: int | None) -> str | None:
        if value is None:
            return None
        if value <= 0:
            return "0"
        if value <= 7:
            return "1-7"
        if value <= 30:
            return "8-30"
        return "31+"

    def _matches_days_past_due_bucket(value: int | None) -> bool:
        if selected_days_past_due_category is None:
            return True
        return _days_past_due_bucket(value) == selected_days_past_due_category

    normalized_search = (search if isinstance(search, str) else "").strip().lower()
    normalized_overdue_bucket = (overdue_bucket if isinstance(overdue_bucket, str) else "").strip().lower()

    def _blocked_days_from_text(value: object) -> int | None:
        parsed = _parse_iso_date_text(str(value or ""))
        if parsed is None:
            return None
        return max(0, (today - parsed).days)

    normalized_page = max(1, int(page))
    normalized_page_size = max(1, int(page_size)) if page_size is not None else None

    def _matches_overdue_bucket(value: int | None) -> bool:
        if not normalized_overdue_bucket or normalized_overdue_bucket == "all":
            return True
        if value is None:
            return False
        if normalized_overdue_bucket == "0-7":
            return 0 <= value <= 7
        if normalized_overdue_bucket == "8-30":
            return 8 <= value <= 30
        if normalized_overdue_bucket == "31-60":
            return 31 <= value <= 60
        if normalized_overdue_bucket == "61+":
            return value >= 61
        return True

    def _matches_search(row: Mapping[str, Any]) -> bool:
        if not normalized_search:
            return True
        haystack = " ".join(
            [
                str(row.get("name") or ""),
                str(row.get("subscriber_id") or ""),
                str(row.get("_external_id") or ""),
                str(row.get("_subscriber_number") or ""),
                str(row.get("phone") or ""),
                str(row.get("city") or ""),
                str(row.get("street") or ""),
                str(row.get("area") or ""),
                str(row.get("plan") or ""),
            ]
        ).lower()
        return normalized_search in haystack

    def _slice_page(rows: list[dict]) -> list[dict]:
        if normalized_page_size is None:
            return rows[: max(1, int(limit))]
        start = (normalized_page - 1) * normalized_page_size
        end = start + normalized_page_size
        return rows[start:end]

    def _call_splynx(cache_name: str, read_fn, *args):
        def _loader():
            splynx_db = SessionLocal()
            try:
                return read_fn(splynx_db, *args)
            finally:
                splynx_db.close()

        return _cached_live_splynx_read(cache_name, _loader, *args, cache_scope=read_fn)

    def _call_splynx_with_session(cache_name: str, read_fn, splynx_db, *args):
        return _cached_live_splynx_read(
            cache_name,
            lambda: read_fn(splynx_db, *args),
            *args,
            cache_scope=read_fn,
        )

    customers = _call_splynx("fetch_customers", fetch_customers)
    live_results: list[dict] = []
    customer_emails = {
        str(customer.get("email") or "").strip().lower()
        for customer in customers
        if isinstance(customer, Mapping) and str(customer.get("email") or "").strip()
    }
    customer_external_ids = {
        str(customer.get("id") or "").strip()
        for customer in customers
        if isinstance(customer, Mapping) and str(customer.get("id") or "").strip()
    }
    customer_logins = {
        str(customer.get("login") or "").strip()
        for customer in customers
        if isinstance(customer, Mapping) and str(customer.get("login") or "").strip()
    }
    people_by_email: dict[str, tuple[str, str]] = {}
    channels_by_person: dict[str, list[PersonChannel]] = {}
    subscriber_sync_by_id: dict[str, dict[str, Any]] = {}
    subscriber_sync_by_external_id: dict[str, dict[str, Any]] = {}
    subscriber_sync_by_email: dict[str, dict[str, Any]] = {}
    subscriber_sync_by_login: dict[str, dict[str, Any]] = {}
    if customer_emails:
        matched_people = db.execute(
            select(Person.id, Person.email, Person.phone).where(func.lower(Person.email).in_(customer_emails))
        ).all()
        people_by_email = {
            str(email).strip().lower(): (str(person_id), str(phone or "").strip())
            for person_id, email, phone in matched_people
            if email
        }
        person_ids = [person_id for person_id, _email, _phone in matched_people]
        if person_ids:
            channel_rows = db.execute(
                select(PersonChannel).where(
                    PersonChannel.person_id.in_(person_ids),
                    PersonChannel.channel_type.in_(
                        [PersonChannelType.phone, PersonChannelType.whatsapp, PersonChannelType.sms]
                    ),
                )
            ).scalars()
            for channel in channel_rows:
                key = str(channel.person_id)
                channels_by_person.setdefault(key, []).append(channel)

    if customer_external_ids or customer_emails or customer_logins:
        subscriber_rows = db.execute(
            select(
                Subscriber.id,
                Subscriber.person_id,
                Subscriber.external_id,
                Subscriber.subscriber_number,
                Subscriber.sync_metadata,
                Subscriber.suspended_at,
                Subscriber.next_bill_date,
                Subscriber.balance,
                Subscriber.billing_cycle,
                Subscriber.service_plan,
                Subscriber.service_city,
                Subscriber.service_region,
                Subscriber.service_address_line1,
                Subscriber.activated_at,
                Subscriber.updated_at,
                Subscriber.created_at,
                Person.email,
            )
            .select_from(Subscriber)
            .outerjoin(Person, Person.id == Subscriber.person_id)
            .where(
                or_(
                    Subscriber.external_id.in_(customer_external_ids) if customer_external_ids else false(),
                    Subscriber.subscriber_number.in_(customer_logins) if customer_logins else false(),
                    func.lower(Person.email).in_(customer_emails) if customer_emails else false(),
                )
            )
        ).all()
        for (
            subscriber_id,
            person_id,
            external_id,
            subscriber_number,
            sync_metadata,
            suspended_at,
            next_bill_date,
            balance,
            billing_cycle,
            service_plan,
            service_city,
            service_region,
            service_address_line1,
            activated_at,
            updated_at,
            created_at,
            person_email,
        ) in subscriber_rows:
            cached_row = {
                "id": str(subscriber_id) if subscriber_id else "",
                "person_id": str(person_id) if person_id else "",
                "sync_metadata": sync_metadata if isinstance(sync_metadata, Mapping) else {},
                "suspended_at": _coerce_datetime_utc(suspended_at),
                "next_bill_date": _coerce_datetime_utc(next_bill_date),
                "balance": _parse_balance_amount(balance),
                "billing_cycle": str(billing_cycle or "").strip(),
                "service_plan": str(service_plan or "").strip(),
                "service_city": str(service_city or "").strip(),
                "service_region": str(service_region or "").strip(),
                "service_address_line1": str(service_address_line1 or "").strip(),
                "activated_at": _coerce_datetime_utc(activated_at),
                "updated_at": _coerce_datetime_utc(updated_at),
                "created_at": _coerce_datetime_utc(created_at),
            }
            subscriber_key = str(subscriber_id or "").strip()
            if subscriber_key:
                subscriber_sync_by_id[subscriber_key] = cached_row
            external_key = str(external_id or "").strip()
            if external_key:
                subscriber_sync_by_external_id[external_key] = cached_row
            login_key = str(subscriber_number or "").strip()
            if login_key:
                subscriber_sync_by_login[login_key] = cached_row
            email_key = str(person_email or "").strip().lower()
            if email_key:
                subscriber_sync_by_email[email_key] = cached_row

    def _cached_subscriber_row(customer_payload: Mapping[str, Any]) -> dict[str, Any]:
        external_key = str(customer_payload.get("id") or "").strip()
        if external_key and external_key in subscriber_sync_by_external_id:
            return subscriber_sync_by_external_id[external_key]

        login_key = str(customer_payload.get("login") or "").strip()
        if login_key and login_key in subscriber_sync_by_login:
            return subscriber_sync_by_login[login_key]

        email_key = str(customer_payload.get("email") or "").strip().lower()
        if email_key and email_key in subscriber_sync_by_email:
            return subscriber_sync_by_email[email_key]
        return {}

    def _ticket_counts_for_subscribers(
        subscriber_rows_by_id: Mapping[str, Mapping[str, Any]],
        fallback_person_ids: set[str] | None = None,
    ) -> tuple[dict[str, dict[str, int | str]], dict[str, dict[str, int | str]]]:
        subscriber_ids = [subscriber_id for subscriber_id in subscriber_rows_by_id if subscriber_id]
        person_ids = sorted(
            {
                str(row.get("person_id") or "").strip()
                for row in subscriber_rows_by_id.values()
                if str(row.get("person_id") or "").strip()
            }
            | {person_id for person_id in (fallback_person_ids or set()) if person_id}
        )
        if not subscriber_ids and not person_ids:
            return {}, {}

        counts_by_subscriber: dict[str, dict[str, int]] = {}
        rows = []
        if subscriber_ids:
            rows = db.execute(
                select(
                    Ticket.subscriber_id,
                    func.count(Ticket.id).label("total_count"),
                    func.count(Ticket.id).filter(Ticket.status.in_(_OPEN_TICKET_STATUSES)).label("open_count"),
                    func.count(Ticket.id).filter(Ticket.status == TicketStatus.closed).label("closed_count"),
                    func.count(Ticket.id).filter(Ticket.status.in_(_FINAL_TICKET_STATUSES)).label("final_count"),
                )
                .where(
                    Ticket.is_active.is_(True),
                    Ticket.subscriber_id.in_(subscriber_ids),
                )
                .group_by(Ticket.subscriber_id)
            ).all()
        for subscriber_id, total_count, open_count, closed_count, final_count in rows:
            subscriber_key = str(subscriber_id or "").strip()
            if not subscriber_key:
                continue
            bucket = counts_by_subscriber.setdefault(
                subscriber_key,
                {
                    "open_tickets": 0,
                    "closed_tickets": 0,
                    "final_tickets": 0,
                    "total_tickets": 0,
                },
            )
            bucket["open_tickets"] += int(open_count or 0)
            bucket["closed_tickets"] += int(closed_count or 0)
            bucket["final_tickets"] += int(final_count or 0)
            bucket["total_tickets"] += int(total_count or 0)

        counts_by_person: dict[str, dict[str, int]] = {}
        person_rows = []
        if person_ids:
            person_rows = db.execute(
                select(
                    Ticket.customer_person_id,
                    func.count(Ticket.id).label("total_count"),
                    func.count(Ticket.id).filter(Ticket.status.in_(_OPEN_TICKET_STATUSES)).label("open_count"),
                    func.count(Ticket.id).filter(Ticket.status == TicketStatus.closed).label("closed_count"),
                    func.count(Ticket.id).filter(Ticket.status.in_(_FINAL_TICKET_STATUSES)).label("final_count"),
                )
                .where(
                    Ticket.is_active.is_(True),
                    Ticket.customer_person_id.in_(person_ids),
                )
                .group_by(Ticket.customer_person_id)
            ).all()
        for person_id, total_count, open_count, closed_count, final_count in person_rows:
            person_key = str(person_id or "").strip()
            if not person_key:
                continue
            counts_by_person[person_key] = {
                "open_tickets": int(open_count or 0),
                "closed_tickets": int(closed_count or 0),
                "final_tickets": int(final_count or 0),
                "total_tickets": int(total_count or 0),
            }

        latest_status_by_subscriber: dict[str, str] = {}
        if subscriber_ids:
            latest_subscriber_status_rows = db.execute(
                select(Ticket.subscriber_id, Ticket.status)
                .where(
                    Ticket.is_active.is_(True),
                    Ticket.subscriber_id.in_(subscriber_ids),
                )
                .order_by(Ticket.subscriber_id.asc(), Ticket.created_at.desc(), Ticket.id.desc())
            ).all()
            for subscriber_id, status in latest_subscriber_status_rows:
                subscriber_key = str(subscriber_id or "").strip()
                if not subscriber_key or subscriber_key in latest_status_by_subscriber:
                    continue
                latest_status_by_subscriber[subscriber_key] = str(
                    status.value if isinstance(status, TicketStatus) else status or ""
                )

        latest_status_by_person: dict[str, str] = {}
        if person_ids:
            latest_person_status_rows = db.execute(
                select(Ticket.customer_person_id, Ticket.status)
                .where(
                    Ticket.is_active.is_(True),
                    Ticket.customer_person_id.in_(person_ids),
                )
                .order_by(Ticket.customer_person_id.asc(), Ticket.created_at.desc(), Ticket.id.desc())
            ).all()
            for person_id, status in latest_person_status_rows:
                person_key = str(person_id or "").strip()
                if not person_key or person_key in latest_status_by_person:
                    continue
                latest_status_by_person[person_key] = str(
                    status.value if isinstance(status, TicketStatus) else status or ""
                )

        latest_ticket_id_by_subscriber: dict[str, str] = {}
        latest_ticket_ref_by_subscriber: dict[str, str] = {}
        if subscriber_ids:
            latest_subscriber_ticket_rows = db.execute(
                select(Ticket.subscriber_id, Ticket.id, Ticket.number)
                .where(
                    Ticket.is_active.is_(True),
                    Ticket.subscriber_id.in_(subscriber_ids),
                )
                .order_by(Ticket.subscriber_id.asc(), Ticket.created_at.desc(), Ticket.id.desc())
            ).all()
            for subscriber_id, ticket_id, ticket_number in latest_subscriber_ticket_rows:
                subscriber_key = str(subscriber_id or "").strip()
                if not subscriber_key or subscriber_key in latest_ticket_id_by_subscriber:
                    continue
                ticket_id_text = str(ticket_id or "").strip()
                ticket_number_text = str(ticket_number or "").strip()
                latest_ticket_id_by_subscriber[subscriber_key] = ticket_id_text
                latest_ticket_ref_by_subscriber[subscriber_key] = ticket_number_text or ticket_id_text

        latest_ticket_id_by_person: dict[str, str] = {}
        latest_ticket_ref_by_person: dict[str, str] = {}
        if person_ids:
            latest_person_ticket_rows = db.execute(
                select(Ticket.customer_person_id, Ticket.id, Ticket.number)
                .where(
                    Ticket.is_active.is_(True),
                    Ticket.customer_person_id.in_(person_ids),
                )
                .order_by(Ticket.customer_person_id.asc(), Ticket.created_at.desc(), Ticket.id.desc())
            ).all()
            for person_id, ticket_id, ticket_number in latest_person_ticket_rows:
                person_key = str(person_id or "").strip()
                if not person_key or person_key in latest_ticket_id_by_person:
                    continue
                ticket_id_text = str(ticket_id or "").strip()
                ticket_number_text = str(ticket_number or "").strip()
                latest_ticket_id_by_person[person_key] = ticket_id_text
                latest_ticket_ref_by_person[person_key] = ticket_number_text or ticket_id_text

        ticket_context_by_person: dict[str, dict[str, int | str]] = {}
        for person_key in person_ids:
            context = counts_by_person.get(person_key) or {
                "open_tickets": 0,
                "closed_tickets": 0,
                "final_tickets": 0,
                "total_tickets": 0,
            }
            latest_status = latest_status_by_person.get(person_key) or ""
            context_with_status = dict(context)
            context_with_status["latest_ticket_status"] = (
                latest_status.replace("_", " ").title() if latest_status else ""
            )
            context_with_status["latest_ticket_id"] = latest_ticket_id_by_person.get(person_key, "")
            context_with_status["latest_ticket_ref"] = latest_ticket_ref_by_person.get(person_key, "")
            ticket_context_by_person[person_key] = context_with_status

        ticket_context_by_subscriber: dict[str, dict[str, int | str]] = {}
        for subscriber_key, row in subscriber_rows_by_id.items():
            if not subscriber_key:
                continue
            person_key = str(row.get("person_id") or "").strip()
            context = (
                counts_by_subscriber.get(subscriber_key)
                or ticket_context_by_person.get(person_key)
                or {"open_tickets": 0, "closed_tickets": 0, "final_tickets": 0, "total_tickets": 0}
            )
            latest_status = latest_status_by_subscriber.get(subscriber_key) or str(
                context.get("latest_ticket_status") or ""
            )
            context_with_status = dict(context)
            context_with_status["latest_ticket_status"] = (
                latest_status.replace("_", " ").title() if latest_status else ""
            )
            context_with_status["latest_ticket_id"] = latest_ticket_id_by_subscriber.get(subscriber_key) or str(
                context.get("latest_ticket_id") or ""
            )
            context_with_status["latest_ticket_ref"] = latest_ticket_ref_by_subscriber.get(subscriber_key) or str(
                context.get("latest_ticket_ref") or ""
            )
            ticket_context_by_subscriber[subscriber_key] = context_with_status
        return ticket_context_by_subscriber, ticket_context_by_person

    fallback_person_ids = {person_id for person_id, _phone in people_by_email.values() if person_id}
    ticket_counts_by_subscriber, ticket_counts_by_person = _ticket_counts_for_subscribers(
        subscriber_sync_by_id,
        fallback_person_ids=fallback_person_ids,
    )

    def _contact_phone(email_value: str, default_phone: str) -> str:
        formatted_default = _format_phone_display(default_phone)
        email_key = email_value.strip().lower()
        if not email_key:
            return formatted_default
        person_match = people_by_email.get(email_key)
        if not person_match:
            return formatted_default
        person_id, person_phone = person_match
        channels = channels_by_person.get(person_id, [])
        for preferred_type in [PersonChannelType.phone, PersonChannelType.whatsapp, PersonChannelType.sms]:
            primary = next(
                (
                    channel.address.strip()
                    for channel in channels
                    if channel.channel_type == preferred_type and channel.is_primary and channel.address
                ),
                "",
            )
            if primary:
                return _format_phone_display(primary)
        for preferred_type in [PersonChannelType.phone, PersonChannelType.whatsapp, PersonChannelType.sms]:
            any_channel = next(
                (
                    channel.address.strip()
                    for channel in channels
                    if channel.channel_type == preferred_type and channel.address
                ),
                "",
            )
            if any_channel:
                return _format_phone_display(any_channel)
        return _format_phone_display(person_phone or formatted_default)

    def _live_billing_start_date(
        customer_payload: Mapping[str, Any],
        mapped_payload: Mapping[str, Any],
        billing_payload: Mapping[str, Any] | None = None,
    ) -> str:
        if isinstance(billing_payload, Mapping):
            billing_start = _live_billing_text(
                billing_payload,
                "billing_start_date",
                "billing_start",
                "start_date",
                "date_from",
                "from_date",
                "period_from",
            )
            if billing_start:
                return billing_start

        mapped_start = _coerce_datetime_utc(mapped_payload.get("activated_at"))
        if mapped_start is not None:
            return mapped_start.strftime("%Y-%m-%d")

        for candidate in (
            customer_payload.get("start_date"),
            customer_payload.get("date_add"),
            customer_payload.get("conversion_date"),
            customer_payload.get("created_at"),
            customer_payload.get("created"),
            customer_payload.get("registration_date"),
        ):
            parsed_date = _parse_iso_date_text(str(candidate or ""))
            if parsed_date is None:
                continue
            parsed_dt = _coerce_datetime_utc(parsed_date)
            if parsed_dt is not None:
                return parsed_dt.strftime("%Y-%m-%d")
        return ""

    def _live_area_from_customer(customer_payload: Mapping[str, Any]) -> str:
        def _normalize_area(raw_value: object) -> str:
            text = str(raw_value or "").strip()
            if not text:
                return ""
            area_value = re.sub(r"\s*\([^)]*\)", "", text).strip()
            area_value = re.sub(r"\s+access\b", "", area_value, flags=re.IGNORECASE).strip()
            area_value = re.sub(r"\s+", " ", area_value).strip(" -")
            return area_value

        def _extract_area(payload: Mapping[str, Any] | None) -> str:
            if not isinstance(payload, Mapping):
                return ""
            for key in ("nas_name", "nas", "router_name", "router", "access_router", "access_name"):
                area_value = _normalize_area(payload.get(key))
                if area_value:
                    return area_value
            for key, value in payload.items():
                key_text = str(key or "").strip().lower()
                value_text = str(value or "").strip()
                if not value_text:
                    continue
                if any(token in key_text for token in ("nas", "router", "station", "access", "pop", "olt")):
                    area_value = _normalize_area(value)
                    if area_value:
                        return area_value
                if re.search(r"\baccess\b", value_text, flags=re.IGNORECASE):
                    area_value = _normalize_area(value)
                    if area_value:
                        return area_value
            return ""

        direct_area = _extract_area(customer_payload)
        if direct_area:
            return direct_area
        additional_attributes = customer_payload.get("additional_attributes")
        if isinstance(additional_attributes, Mapping):
            return _extract_area(additional_attributes)
        if isinstance(additional_attributes, list):
            for item in additional_attributes:
                if isinstance(item, Mapping):
                    direct_area = _extract_area(item)
                    if direct_area:
                        return direct_area
        return ""

    def _infer_city(*values: object) -> str:
        haystack = " ".join(str(value or "") for value in values).strip()
        if not haystack:
            return ""
        city_markers = (
            (
                "Abuja",
                (
                    "abuja",
                    "fct",
                    "wuse",
                    "wuse-1",
                    "gwarimpa",
                    "gwarinpa",
                    "maitama",
                    "mabushi",
                    "kubwa",
                    "jabi",
                    "asokoro",
                    "cbd",
                    "central business district",
                    "kaura",
                    "apo",
                    "gaduwa",
                    "gudu",
                ),
            ),
            ("Lagos", ("lagos", "ikeja", "lekki", "victoria island", "vi ", "ikoyi")),
            ("Port Harcourt", ("port harcourt", "phc")),
            ("Nasarawa", ("nasarawa", "mararaba")),
            ("Abeokuta", ("abeokuta", "oke mosan")),
            ("Maiduguri", ("maiduguri",)),
            ("Yola", ("yola",)),
            ("Awka", ("awka",)),
            ("Kaduna", ("kaduna",)),
            ("Kano", ("kano",)),
        )
        normalized = f" {haystack.lower()} "
        for label, markers in city_markers:
            if any(marker in normalized for marker in markers):
                return label
        return ""

    def _live_street_address(customer_payload: Mapping[str, Any], cached_subscriber: Mapping[str, Any]) -> str:
        ignored_values = {"", "-", "n/a", "na", "none", "null", "unknown"}
        street_parts: list[str] = []
        seen_parts: set[str] = set()
        for candidate in (
            customer_payload.get("street_1"),
            customer_payload.get("street_2"),
            cached_subscriber.get("service_address_line1"),
        ):
            part = " ".join(str(candidate or "").replace("\r", " ").replace("\n", " ").split())
            if part.casefold() in ignored_values:
                continue
            dedupe_key = part.casefold().strip(" ,")
            if not dedupe_key or dedupe_key in seen_parts:
                continue
            seen_parts.add(dedupe_key)
            street_parts.append(part.strip(" ,"))
        street = ", ".join(street_parts)
        if street:
            return street
        return ""

    def _live_billing_text(payload: Mapping[str, Any] | None, *keys: str) -> str:
        if not isinstance(payload, Mapping):
            return ""
        for key in keys:
            candidate = payload.get(key)
            candidate_text = str(candidate or "").strip()
            if not candidate_text or candidate_text == "0000-00-00":
                continue
            parsed_date = _parse_iso_date_text(candidate_text)
            if parsed_date is not None:
                return parsed_date.strftime("%Y-%m-%d")
            if candidate_text:
                return candidate_text
        return ""

    def _live_invoiced_until_date(billing_payload: Mapping[str, Any] | None) -> str:
        return _live_billing_text(
            billing_payload,
            "invoiced_until",
            "invoiced_to",
            "paid_until",
            "invoice_until",
            "paid_to",
        )

    def _live_blocked_date_from_billing(billing_payload: Mapping[str, Any] | None) -> str:
        blocking_date = _live_billing_text(
            billing_payload,
            "blocking_date",
            "request_auto_next",
            "blocked_date",
        )
        return blocking_date or _live_invoiced_until_date(billing_payload)

    def _live_service_plan(services_payload: object) -> str:
        if not isinstance(services_payload, list):
            return ""
        services = [service for service in services_payload if isinstance(service, dict)]
        primary_service = _select_primary_service(services)
        if not isinstance(primary_service, Mapping):
            return ""
        for candidate in (
            primary_service.get("description"),
            primary_service.get("tariff_name"),
            primary_service.get("plan_name"),
            primary_service.get("package"),
            primary_service.get("name"),
        ):
            text = str(candidate or "").strip()
            if text:
                return text
        return ""

    for customer in customers:
        if not isinstance(customer, Mapping):
            continue
        mapped = map_customer_to_subscriber_data(db, dict(customer), include_remote_details=False)
        cached_subscriber = _cached_subscriber_row(customer)
        cached_sync_metadata = (
            cached_subscriber.get("sync_metadata")
            if isinstance(cached_subscriber.get("sync_metadata"), Mapping)
            else {}
        )
        cached_suspended_at = _coerce_datetime_utc(cached_subscriber.get("suspended_at"))
        status_raw = str(mapped.get("status") or "unknown").strip().lower()
        status_value = status_raw.removeprefix("subscriberstatus.")
        plan_value = str(mapped.get("service_plan") or "").strip() or str(cached_subscriber.get("service_plan") or "")
        embedded_billing = customer.get("billing") if isinstance(customer.get("billing"), Mapping) else None
        billing_start_date = _live_billing_start_date(customer, mapped, embedded_billing)
        if not billing_start_date:
            cached_activated_at = _coerce_datetime_utc(cached_subscriber.get("activated_at"))
            if cached_activated_at is not None:
                billing_start_date = cached_activated_at.strftime("%Y-%m-%d")
        area_value = _live_area_from_customer(customer)
        next_bill_raw = _coerce_datetime_utc(mapped.get("next_bill_date")) or _coerce_datetime_utc(
            cached_subscriber.get("next_bill_date")
        )
        due_days = (next_bill_raw.date() - today).days if next_bill_raw is not None else None
        balance_amount = _parse_balance_amount(mapped.get("balance") or customer.get("balance"))
        if balance_amount == 0.0 and cached_subscriber.get("balance") is not None:
            balance_amount = float(cached_subscriber.get("balance") or 0.0)
        sync_metadata = mapped.get("sync_metadata") if isinstance(mapped.get("sync_metadata"), Mapping) else {}
        if not sync_metadata and cached_sync_metadata:
            sync_metadata = cached_sync_metadata
        invoiced_until_text = _metadata_text(sync_metadata, "invoiced_until")
        invoiced_until_date = _parse_iso_date_text(invoiced_until_text)
        days_since_last_payment = max(0, (today - invoiced_until_date).days) if invoiced_until_date else None
        row_days_past_due = days_since_last_payment
        customer_last_update = _live_billing_text(customer, "last_update")
        customer_last_online = _live_billing_text(customer, "last_online")
        embedded_blocking_date = _live_blocked_date_from_billing(embedded_billing)
        blocking_period_days = (
            _coerce_nonnegative_int(embedded_billing.get("blocking_period"))
            if isinstance(embedded_billing, Mapping)
            else None
        )
        blocked_date_text = embedded_blocking_date or invoiced_until_text
        blocked_for_days = _blocked_days_from_text(blocked_date_text)
        live_segment_value: str | None = None
        if status_value == SubscriberStatus.terminated.value:
            live_segment_value = "Churned"
        elif status_value == SubscriberStatus.suspended.value:
            live_segment_value = "Suspended"
        elif status_value == SubscriberStatus.pending.value:
            live_segment_value = "Pending"
        elif status_value == SubscriberStatus.active.value and due_days is not None and due_days <= due_soon_days:
            live_segment_value = "Due Soon"
        if live_segment_value is None:
            continue
        if status_value == SubscriberStatus.active.value and live_segment_value == "Due Soon":
            blocked_date_text = ""
            blocked_for_days = None
        elif status_value == SubscriberStatus.suspended.value and not blocked_date_text:
            if blocking_period_days is not None and row_days_past_due is not None:
                inferred_blocked_days = max(0, int(row_days_past_due) - blocking_period_days)
                blocked_for_days = inferred_blocked_days
                if inferred_blocked_days > 0:
                    blocked_date_text = (today - timedelta(days=inferred_blocked_days)).strftime("%Y-%m-%d")
            else:
                # Splynx often leaves billing.blocking_date unset (0000-00-00).
                # Use customer last_update as best available blocked timestamp.
                parsed_last_update = _parse_iso_date_text(customer_last_update) or _parse_iso_date_text(
                    customer_last_online
                )
                if parsed_last_update is not None:
                    blocked_date_text = parsed_last_update.strftime("%Y-%m-%d")
                    blocked_for_days = max(0, (today - parsed_last_update).days)
        if selected_segments and live_segment_value not in selected_segments:
            continue
        if not _matches_days_past_due_bucket(row_days_past_due):
            continue

        display_name = str(customer.get("name") or "").strip() or str(mapped.get("subscriber_number") or "").strip()
        email_value = str(customer.get("email") or "").strip()
        phone_value = str(customer.get("phone") or "").strip()
        city_value = (
            str(customer.get("city") or "").strip()
            or str(cached_subscriber.get("service_city") or "").strip()
            or _infer_city(
                display_name,
                cached_subscriber.get("service_region"),
                cached_subscriber.get("service_address_line1"),
                customer.get("street_1"),
                customer.get("street_2"),
            )
        )
        street_value = _live_street_address(customer, cached_subscriber)
        subscriber_id_key = str(cached_subscriber.get("id") or "").strip()
        person_id_key = str(cached_subscriber.get("person_id") or "").strip()
        if not person_id_key and email_value:
            person_id_key = str(people_by_email.get(email_value.lower(), ("", ""))[0] or "").strip()
        ticket_counts = (
            ticket_counts_by_subscriber.get(subscriber_id_key) or ticket_counts_by_person.get(person_id_key) or {}
        )
        mrr_total_value = _parse_balance_amount(customer.get("mrr_total"))
        live_results.append(
            {
                "subscriber_id": str(customer.get("id") or ""),
                "name": _clean_report_name(display_name or "Unknown"),
                "email": email_value,
                "phone": _contact_phone(email_value, phone_value),
                "city": city_value,
                "street": street_value,
                "mrr_total": mrr_total_value,
                "subscriber_status": status_value.replace("_", " ").title(),
                "area": area_value,
                "plan": plan_value,
                "billing_start_date": billing_start_date,
                "billing_end_date": next_bill_raw.strftime("%Y-%m-%d") if next_bill_raw else "",
                "next_bill_date": next_bill_raw.strftime("%Y-%m-%d") if next_bill_raw else "",
                "balance": balance_amount,
                "billing_cycle": str(mapped.get("billing_cycle") or cached_subscriber.get("billing_cycle") or ""),
                "blocked_date": blocked_date_text,
                "blocked_for_days": blocked_for_days,
                "blocking_period": blocking_period_days,
                "suspended_at": cached_suspended_at.strftime("%Y-%m-%d") if cached_suspended_at is not None else "",
                "last_transaction_date": _metadata_text(sync_metadata, "last_transaction_date"),
                "expires_in": _metadata_text(sync_metadata, "expires_in"),
                "invoiced_until": invoiced_until_text,
                "days_since_last_payment": days_since_last_payment,
                "days_past_due": row_days_past_due,
                "total_paid": _parse_balance_amount(_metadata_text(sync_metadata, "total_paid")),
                "days_to_due": due_days,
                "risk_segment": live_segment_value,
                "open_tickets": int(ticket_counts.get("open_tickets") or 0),
                "closed_tickets": int(ticket_counts.get("closed_tickets") or 0),
                "final_tickets": int(ticket_counts.get("final_tickets") or 0),
                "total_tickets": int(ticket_counts.get("total_tickets") or 0),
                "latest_ticket_status": str(ticket_counts.get("latest_ticket_status") or ""),
                "latest_ticket_id": str(ticket_counts.get("latest_ticket_id") or ""),
                "latest_ticket_ref": str(ticket_counts.get("latest_ticket_ref") or ""),
                "_person_id": person_id_key,
                "ticket_subscriber_id": subscriber_id_key,
                "_subscriber_uuid": subscriber_id_key,
                "_external_id": str(customer.get("id") or ""),
                "_subscriber_number": str(mapped.get("subscriber_number") or ""),
                "_last_synced_at": "",
                "_customer_last_update": customer_last_update,
                "_customer_last_online": customer_last_online,
            }
        )

    live_results = _dedupe_churn_rows(live_results)
    avg_balance = round(sum(row["balance"] for row in live_results) / len(live_results), 2) if live_results else 0.0
    for entry in live_results:
        entry["is_high_balance_risk"] = entry["balance"] > avg_balance and entry["risk_segment"] in {
            "Due Soon",
            "Suspended",
            "Churned",
        }
    if high_balance_only:
        live_results = [row for row in live_results if row["is_high_balance_risk"]]
    live_results = [
        row for row in live_results if _matches_search(row) and _matches_overdue_bucket(row.get("blocked_for_days"))
    ]
    live_results.sort(
        key=lambda row: (
            -int(bool(row["is_high_balance_risk"])),
            -float(row["balance"]),
            row["days_to_due"] if isinstance(row["days_to_due"], int) else 10**9,
            row["name"],
        )
    )
    visible_results = _slice_page(live_results)

    def _enrich_live_entry(entry: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        external_id = str(entry.get("_external_id") or "").strip()
        if not external_id:
            return updates
        is_active_overdue = (
            str(entry.get("subscriber_status") or "").strip().lower() == "active"
            and str(entry.get("risk_segment") or "").strip() == "Due Soon"
        )
        splynx_db = SessionLocal()
        try:
            if not str(entry.get("plan") or "").strip() and str(entry.get("risk_segment") or "") == "Suspended":
                try:
                    services_payload = _call_splynx_with_session(
                        "fetch_customer_internet_services",
                        fetch_customer_internet_services,
                        splynx_db,
                        external_id,
                    )
                except Exception:
                    services_payload = []
                live_plan = _live_service_plan(services_payload)
                if live_plan:
                    updates["plan"] = live_plan
            try:
                billing_payload = _call_splynx_with_session(
                    "fetch_customer_billing",
                    fetch_customer_billing,
                    splynx_db,
                    external_id,
                )
            except Exception:
                billing_payload = {}
        finally:
            splynx_db.close()
        if is_active_overdue:
            updates["blocked_date"] = ""
            updates["blocked_for_days"] = None
            return updates
        if not isinstance(billing_payload, Mapping):
            return updates
        live_last_transaction_date = _live_billing_text(billing_payload, "last_transaction_date")
        if live_last_transaction_date:
            updates["last_transaction_date"] = live_last_transaction_date
        live_start_date = _live_billing_start_date({}, {}, billing_payload)
        if live_start_date:
            updates["billing_start_date"] = live_start_date
        live_invoiced_until = _live_invoiced_until_date(billing_payload)
        if live_invoiced_until:
            updates["invoiced_until"] = live_invoiced_until
        live_blocked_date = _live_blocked_date_from_billing(billing_payload)
        live_blocking_period = _coerce_nonnegative_int(billing_payload.get("blocking_period"))
        if live_blocking_period is not None:
            updates["blocking_period"] = live_blocking_period
        if live_blocked_date:
            updates["blocked_date"] = live_blocked_date
            updates["blocked_for_days"] = _blocked_days_from_text(live_blocked_date)
        elif str(entry.get("subscriber_status") or "").strip().lower() in {"suspended", "blocked"}:
            days_past_due = _coerce_nonnegative_int(entry.get("days_past_due"))
            if live_blocking_period is not None and days_past_due is not None:
                inferred_blocked_days = max(0, days_past_due - live_blocking_period)
                updates["blocked_for_days"] = inferred_blocked_days
                if inferred_blocked_days > 0:
                    updates["blocked_date"] = (
                        datetime.now(UTC).date() - timedelta(days=inferred_blocked_days)
                    ).strftime("%Y-%m-%d")
            else:
                parsed_last_update = _parse_iso_date_text(
                    str(entry.get("_customer_last_update") or "")
                ) or _parse_iso_date_text(str(entry.get("_customer_last_online") or ""))
                if parsed_last_update is not None:
                    updates["blocked_date"] = parsed_last_update.strftime("%Y-%m-%d")
                    updates["blocked_for_days"] = max(0, (datetime.now(UTC).date() - parsed_last_update).days)
        return updates

    if enrich_visible_rows and visible_results:
        max_workers = min(8, len(visible_results))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_enrich_live_entry, entry) for entry in visible_results]
            for entry, future in zip(visible_results, futures, strict=False):
                entry.update(future.result())
    visible_results.sort(
        key=lambda row: (
            _parse_iso_date_text(str(row.get("blocked_date") or "")) or date.max,
            row.get("name") or "",
        )
    )
    return visible_results


def enrich_billing_risk_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Refresh visible-row billing details without rebuilding the full table."""
    from app.services.splynx import _select_primary_service, fetch_customer_billing, fetch_customer_internet_services

    today = datetime.now(UTC).date()

    def _blocked_days_from_text(value: object) -> int | None:
        parsed = _parse_iso_date_text(str(value or ""))
        if parsed is None:
            return None
        return max(0, (today - parsed).days)

    def _live_billing_text(payload: Mapping[str, Any] | None, *keys: str) -> str:
        if not isinstance(payload, Mapping):
            return ""
        for key in keys:
            candidate = payload.get(key)
            candidate_text = str(candidate or "").strip()
            if not candidate_text or candidate_text == "0000-00-00":
                continue
            parsed_date = _parse_iso_date_text(candidate_text)
            if parsed_date is not None:
                return parsed_date.strftime("%Y-%m-%d")
            if candidate_text:
                return candidate_text
        return ""

    def _live_service_plan(services_payload: object) -> str:
        if not isinstance(services_payload, list):
            return ""
        services = [service for service in services_payload if isinstance(service, dict)]
        primary_service = _select_primary_service(services)
        if not isinstance(primary_service, Mapping):
            return ""
        for candidate in (
            primary_service.get("description"),
            primary_service.get("tariff_name"),
            primary_service.get("plan_name"),
            primary_service.get("package"),
            primary_service.get("name"),
        ):
            text = str(candidate or "").strip()
            if text:
                return text
        return ""

    def _amount(value: object) -> float:
        return _parse_balance_amount(value)

    def _service_amount(services_payload: object) -> float:
        if not isinstance(services_payload, list):
            return 0.0
        services = [service for service in services_payload if isinstance(service, dict)]
        primary_service = _select_primary_service(services)
        if not isinstance(primary_service, Mapping):
            return 0.0
        for candidate in (
            primary_service.get("unit_price"),
            primary_service.get("price"),
            primary_service.get("monthly_price"),
            primary_service.get("amount"),
        ):
            amount = _amount(candidate)
            if amount > 0:
                return amount
        return 0.0

    def _service_start_date(services_payload: object) -> str:
        if not isinstance(services_payload, list):
            return ""
        parsed_dates = [
            parsed
            for service in services_payload
            if isinstance(service, Mapping)
            for parsed in [_parse_iso_date_text(str(service.get("start_date") or ""))]
            if parsed is not None
        ]
        if not parsed_dates:
            return ""
        return min(parsed_dates).strftime("%Y-%m-%d")

    def _billing_start_date(billing_payload: Mapping[str, Any] | None) -> str:
        return _live_billing_text(
            billing_payload,
            "billing_start_date",
            "billing_start",
            "start_date",
            "date_from",
            "from_date",
            "period_from",
        )

    def _invoiced_until_date(billing_payload: Mapping[str, Any] | None) -> str:
        return _live_billing_text(
            billing_payload,
            "invoiced_until",
            "invoiced_to",
            "paid_until",
            "invoice_until",
            "paid_to",
        )

    def _blocked_date_from_billing(billing_payload: Mapping[str, Any] | None) -> str:
        blocking_date = _live_billing_text(
            billing_payload,
            "blocking_date",
            "blocked_date",
        )
        return blocking_date or _invoiced_until_date(billing_payload)

    def _enrich_live_entry(entry: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        external_id = str(entry.get("_external_id") or "").strip()
        if not external_id:
            return updates
        is_active_overdue = (
            str(entry.get("subscriber_status") or "").strip().lower() == "active"
            and str(entry.get("risk_segment") or "").strip() == "Due Soon"
        )
        needs_services = (
            not str(entry.get("plan") or "").strip()
            or not str(entry.get("billing_start_date") or "").strip()
            or _amount(entry.get("mrr_total")) <= 0
            or _amount(entry.get("balance")) <= 0
        )
        needs_billing = not is_active_overdue and (
            not str(entry.get("billing_start_date") or "").strip()
            or not str(entry.get("blocked_date") or "").strip()
            or _amount(entry.get("balance")) <= 0
        )
        splynx_db = SessionLocal()
        try:
            if needs_services:
                try:
                    services_payload = _cached_live_splynx_read(
                        "fetch_customer_internet_services",
                        lambda: fetch_customer_internet_services(splynx_db, external_id),
                        external_id,
                        cache_scope=fetch_customer_internet_services,
                    )
                except Exception:
                    services_payload = []
                live_plan = _live_service_plan(services_payload)
                if live_plan:
                    updates["plan"] = live_plan
                live_start_date = _service_start_date(services_payload)
                if live_start_date and not str(entry.get("billing_start_date") or "").strip():
                    updates["billing_start_date"] = live_start_date
                live_service_amount = _service_amount(services_payload)
                if live_service_amount > 0:
                    if _amount(entry.get("mrr_total")) <= 0:
                        updates["mrr_total"] = live_service_amount
                    if _amount(entry.get("balance")) <= 0:
                        updates["balance"] = live_service_amount
            if needs_billing:
                try:
                    billing_payload = _cached_live_splynx_read(
                        "fetch_customer_billing",
                        lambda: fetch_customer_billing(splynx_db, external_id),
                        external_id,
                        cache_scope=fetch_customer_billing,
                    )
                except Exception:
                    billing_payload = {}
            else:
                billing_payload = {}
        finally:
            splynx_db.close()
        if not isinstance(billing_payload, Mapping):
            return updates
        live_month_price = _amount(billing_payload.get("month_price"))
        if live_month_price > 0 and _amount(entry.get("mrr_total")) <= 0:
            updates["mrr_total"] = live_month_price
        live_balance = _amount(billing_payload.get("deposit"))
        if live_balance > 0 and _amount(entry.get("balance")) <= 0:
            updates["balance"] = live_balance
        if is_active_overdue:
            updates["blocked_date"] = ""
            updates["blocked_for_days"] = None
            return updates
        live_last_transaction_date = _live_billing_text(billing_payload, "last_transaction_date")
        if live_last_transaction_date:
            updates["last_transaction_date"] = live_last_transaction_date
        live_start_date = _billing_start_date(billing_payload)
        if live_start_date:
            updates["billing_start_date"] = live_start_date
        live_invoiced_until = _invoiced_until_date(billing_payload)
        if live_invoiced_until:
            updates["invoiced_until"] = live_invoiced_until
        live_blocked_date = _blocked_date_from_billing(billing_payload)
        live_blocking_period = _coerce_nonnegative_int(billing_payload.get("blocking_period"))
        if live_blocking_period is not None:
            updates["blocking_period"] = live_blocking_period
        if live_blocked_date:
            updates["blocked_date"] = live_blocked_date
            updates["blocked_for_days"] = _blocked_days_from_text(live_blocked_date)
        elif str(entry.get("subscriber_status") or "").strip().lower() in {"suspended", "blocked"}:
            days_past_due = _coerce_nonnegative_int(entry.get("days_past_due"))
            if live_blocking_period is not None and days_past_due is not None:
                inferred_blocked_days = max(0, days_past_due - live_blocking_period)
                updates["blocked_for_days"] = inferred_blocked_days
                if inferred_blocked_days > 0:
                    updates["blocked_date"] = (
                        datetime.now(UTC).date() - timedelta(days=inferred_blocked_days)
                    ).strftime("%Y-%m-%d")
        return updates

    if rows:
        max_workers = min(8, len(rows))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_enrich_live_entry, entry) for entry in rows]
            for entry, future in zip(rows, futures, strict=False):
                entry.update(future.result())
        rows.sort(
            key=lambda row: (
                _parse_iso_date_text(str(row.get("blocked_date") or "")) or date.max,
                row.get("name") or "",
            )
        )
    return rows


def get_live_blocked_dates(
    external_ids: list[str],
    *,
    force_live: bool = False,
    blocking_only_external_ids: list[str] | set[str] | None = None,
) -> dict[str, str]:
    """Fetch live blocked dates for the currently visible rows."""
    from app.services.splynx import (
        _select_primary_service,
        fetch_customer,
        fetch_customer_billing,
        fetch_customer_internet_services,
        fetch_customers,
    )

    blocked_dates: dict[str, str] = {}
    seen_ids: set[str] = set()
    blocking_only_set = {str(external_id).strip() for external_id in (blocking_only_external_ids or [])}

    preloaded_customers: dict[str, Mapping[str, Any]] = {}

    def _customers_loader():
        splynx_db = SessionLocal()
        try:
            return fetch_customers(splynx_db)
        finally:
            splynx_db.close()

    try:
        customers_payload = (
            _customers_loader()
            if force_live
            else _cached_live_splynx_read(
                "fetch_customers",
                _customers_loader,
                cache_scope=fetch_customers,
            )
        )
    except Exception:
        customers_payload = []
    if isinstance(customers_payload, list):
        for item in customers_payload:
            if not isinstance(item, Mapping):
                continue
            customer_id = str(item.get("id") or "").strip()
            if customer_id:
                preloaded_customers[customer_id] = item

    for raw_external_id in external_ids:
        external_id = str(raw_external_id or "").strip()
        if not external_id or external_id in seen_ids:
            continue
        seen_ids.add(external_id)

        def _billing_loader(bound_external_id: str = external_id):
            splynx_db = SessionLocal()
            try:
                return fetch_customer_billing(splynx_db, bound_external_id)
            finally:
                splynx_db.close()

        billing_payload = (
            _billing_loader()
            if force_live
            else _cached_live_splynx_read(
                "fetch_customer_billing",
                _billing_loader,
                external_id,
                cache_scope=fetch_customer_billing,
            )
        )

        if isinstance(billing_payload, Mapping):
            billing_text = str(
                billing_payload.get("blocking_date")
                or billing_payload.get("request_auto_next")
                or billing_payload.get("blocked_date")
                or ""
            )
            if external_id not in blocking_only_set and not billing_text.strip():
                billing_text = str(
                    billing_payload.get("invoiced_until")
                    or billing_payload.get("invoiced_to")
                    or billing_payload.get("paid_until")
                    or billing_payload.get("invoice_until")
                    or billing_payload.get("paid_to")
                    or ""
                )
        else:
            billing_text = ""

        blocked_date = _parse_iso_date_text(billing_text)
        if blocked_date is None:

            def _services_loader(bound_external_id: str = external_id):
                splynx_db = SessionLocal()
                try:
                    return fetch_customer_internet_services(splynx_db, bound_external_id)
                finally:
                    splynx_db.close()

            services_payload = (
                _services_loader()
                if force_live
                else _cached_live_splynx_read(
                    "fetch_customer_internet_services",
                    _services_loader,
                    external_id,
                    cache_scope=fetch_customer_internet_services,
                )
            )
            services = [service for service in (services_payload or []) if isinstance(service, Mapping)]
            primary_service = _select_primary_service(services)
            service_blocking_text = (
                str(primary_service.get("blocking_date") or "") if isinstance(primary_service, Mapping) else ""
            )
            if not service_blocking_text:
                for service in services:
                    candidate = str(service.get("blocking_date") or "")
                    if candidate:
                        service_blocking_text = candidate
                        break
            blocked_date = _parse_iso_date_text(service_blocking_text)

        if blocked_date is None:
            customer_payload = preloaded_customers.get(external_id)
            if customer_payload is None:

                def _customer_loader(bound_external_id: str = external_id):
                    splynx_db = SessionLocal()
                    try:
                        return fetch_customer(splynx_db, bound_external_id)
                    finally:
                        splynx_db.close()

                customer_payload = (
                    _customer_loader()
                    if force_live
                    else _cached_live_splynx_read(
                        "fetch_customer",
                        _customer_loader,
                        external_id,
                        cache_scope=fetch_customer,
                    )
                )
            if isinstance(customer_payload, Mapping):
                customer_status = str(customer_payload.get("status") or "").strip().lower()
                customer_text = str(
                    customer_payload.get("blocking_date")
                    or customer_payload.get("blocked_date")
                    or customer_payload.get("suspended_at")
                    or customer_payload.get("last_update")
                    or (
                        customer_payload.get("last_online")
                        if customer_status in {"blocked", "suspended", SubscriberStatus.suspended.value}
                        else ""
                    )
                    or ""
                )
                blocked_date = _parse_iso_date_text(customer_text)

        if blocked_date is not None:
            blocked_dates[external_id] = blocked_date.strftime("%Y-%m-%d")
    return blocked_dates


def get_overdue_invoices_table(
    db: Session,
    *,
    min_days_past_due: int = 30,
    limit: int = 500,
) -> list[dict]:
    """Overdue receivables by customer, used only for billing-risk KPI rollups."""
    dialect_name = db.get_bind().dialect.name if db.get_bind() is not None else ""
    due_dt = func.coalesce(SalesOrder.payment_due_date, SalesOrder.created_at)
    days_past_due = _days_since_expr(db, due_dt).label("days_past_due")
    oldest_due_day_expr = func.date(due_dt) if dialect_name == "sqlite" else cast(due_dt, Date)

    rows = db.execute(
        select(
            SalesOrder.person_id,
            func.count(SalesOrder.id).label("overdue_invoices"),
            func.sum(SalesOrder.balance_due).label("total_balance_due"),
            func.max(days_past_due).label("max_days_past_due"),
            func.min(oldest_due_day_expr).label("oldest_due_day"),
            Person.display_name,
            Person.first_name,
            Person.last_name,
            Person.email,
        )
        .select_from(SalesOrder)
        .join(Person, Person.id == SalesOrder.person_id, isouter=True)
        .where(
            SalesOrder.is_active.is_(True),
            func.coalesce(SalesOrder.balance_due, 0) > 0,
            SalesOrder.payment_status != SalesOrderPaymentStatus.paid,
            days_past_due >= min_days_past_due,
        )
        .group_by(
            SalesOrder.person_id,
            Person.display_name,
            Person.first_name,
            Person.last_name,
            Person.email,
        )
        .order_by(func.max(days_past_due).desc(), func.sum(SalesOrder.balance_due).desc())
        .limit(limit)
    ).all()

    results: list[dict] = []
    for row in rows:
        raw_name = (
            row.display_name
            or f"{row.first_name or ''} {row.last_name or ''}".strip()
            or (row.email.split(",")[0].strip() if row.email else "")
            or "Unknown"
        )
        oldest_due_day = ""
        if row.oldest_due_day:
            if isinstance(row.oldest_due_day, str):
                oldest_due_day = row.oldest_due_day[:10]
            else:
                oldest_due_day = row.oldest_due_day.strftime("%Y-%m-%d")
        results.append(
            {
                "person_id": str(row.person_id) if row.person_id else "",
                "name": _clean_report_name(raw_name),
                "email": row.email or "",
                "overdue_invoices": int(row.overdue_invoices or 0),
                "total_balance_due": float(row.total_balance_due or 0),
                "max_days_past_due": int(row.max_days_past_due or 0),
                "oldest_due_day": oldest_due_day,
            }
        )
    return results


def get_billing_risk_summary(
    churn_rows: list[dict],
    overdue_invoices: list[dict],
    recent_churn_kpis: dict[str, Any] | None = None,
) -> dict[str, float | int]:
    recent_churn_kpis = recent_churn_kpis or {}
    total_at_risk = len(churn_rows)
    total_balance_exposure = round(sum(float(row.get("balance") or 0) for row in churn_rows), 2)
    high_balance_risk_count = sum(1 for row in churn_rows if bool(row.get("is_high_balance_risk")))
    overdue_count = sum(1 for row in churn_rows if row.get("risk_segment") == "Due Soon")
    overdue_balance_exposure = round(
        sum(float(row.get("balance") or 0) for row in churn_rows if row.get("risk_segment") == "Due Soon"),
        2,
    )
    overdue_invoice_balance = round(
        sum(float(row.get("total_balance_due") or 0) for row in overdue_invoices),
        2,
    )
    return {
        "total_at_risk": total_at_risk,
        "total_balance_exposure": total_balance_exposure,
        "high_balance_risk_count": high_balance_risk_count,
        "high_balance_risk_pct": round((high_balance_risk_count / total_at_risk) * 100, 1) if total_at_risk else 0,
        "overdue_count": overdue_count,
        "overdue_balance_exposure": overdue_balance_exposure,
        "overdue_invoice_balance": overdue_invoice_balance,
        "recent_churned_count": int(recent_churn_kpis.get("churned_count") or 0),
        "recent_churn_rate": float(recent_churn_kpis.get("churn_rate") or 0),
        "recent_revenue_lost": float(recent_churn_kpis.get("revenue_lost_to_churn") or 0),
    }


def get_billing_risk_segment_breakdown(churn_rows: list[dict]) -> list[dict[str, float | int | str]]:
    segment_map: dict[str, dict[str, float | int | str]] = {}
    segment_billing_cycles: dict[str, dict[str, int]] = {}
    segment_payment_days: dict[str, list[int]] = {}
    total_count = len(churn_rows)

    for segment in _BILLING_RISK_SEGMENT_ORDER:
        segment_map[segment] = {
            "segment": segment,
            "count": 0,
            "balance": 0.0,
            "high_balance_count": 0,
            "avg_balance": 0.0,
            "share_pct": 0.0,
            "billing_mix": "",
        }
        segment_billing_cycles[segment] = {}
        segment_payment_days[segment] = []

    for row in churn_rows:
        segment = str(row.get("risk_segment") or "Unknown")
        if segment not in segment_map:
            segment_map[segment] = {
                "segment": segment,
                "count": 0,
                "balance": 0.0,
                "high_balance_count": 0,
                "avg_balance": 0.0,
                "share_pct": 0.0,
                "billing_mix": "",
            }
            segment_billing_cycles[segment] = {}
            segment_payment_days[segment] = []
        segment_map[segment]["count"] = int(segment_map[segment]["count"]) + 1
        segment_map[segment]["balance"] = round(
            float(segment_map[segment]["balance"]) + float(row.get("balance") or 0),
            2,
        )
        if row.get("is_high_balance_risk"):
            segment_map[segment]["high_balance_count"] = int(segment_map[segment]["high_balance_count"]) + 1
        billing_cycle = str(row.get("billing_cycle") or "").strip().lower()
        if billing_cycle:
            cycles = segment_billing_cycles[segment]
            cycles[billing_cycle] = cycles.get(billing_cycle, 0) + 1
        days_since_last_payment = row.get("days_since_last_payment")
        if isinstance(days_since_last_payment, int):
            segment_payment_days[segment].append(days_since_last_payment)
        elif isinstance(days_since_last_payment, str) and days_since_last_payment.strip().isdigit():
            segment_payment_days[segment].append(int(days_since_last_payment.strip()))
        else:
            invoiced_until_date = _parse_iso_date_text(str(row.get("invoiced_until") or ""))
            if invoiced_until_date is not None:
                segment_payment_days[segment].append(max(0, (datetime.now(UTC).date() - invoiced_until_date).days))

    results = list(segment_map.values())
    for row in results:
        count = int(row["count"])
        balance = float(row["balance"])
        row["avg_balance"] = round(balance / count, 2) if count else 0.0
        row["share_pct"] = round((count / total_count) * 100, 1) if total_count else 0.0
        cycle_counts = segment_billing_cycles.get(str(row["segment"]), {})
        payment_days = segment_payment_days.get(str(row["segment"]), [])
        payment_recency_text = ""
        if payment_days:
            avg_days = round(sum(payment_days) / len(payment_days))
            payment_recency_text = f"Avg {avg_days}d since payment ({len(payment_days)} accounts)"
        if cycle_counts and payment_recency_text:
            sorted_cycles = sorted(cycle_counts.items(), key=lambda item: (-item[1], item[0]))
            cycle_mix_text = ", ".join(
                f"{cycle_name.replace('_', ' ').title()} ({cycle_count})"
                for cycle_name, cycle_count in sorted_cycles[:3]
            )
            row["billing_mix"] = f"{payment_recency_text} | {cycle_mix_text}"
        elif cycle_counts:
            sorted_cycles = sorted(cycle_counts.items(), key=lambda item: (-item[1], item[0]))
            row["billing_mix"] = ", ".join(
                f"{cycle_name.replace('_', ' ').title()} ({cycle_count})"
                for cycle_name, cycle_count in sorted_cycles[:3]
            )
        elif payment_recency_text:
            row["billing_mix"] = payment_recency_text
        else:
            row["billing_mix"] = "No billing cycle data"

    results.sort(
        key=lambda row: (
            _BILLING_RISK_SEGMENT_ORDER.index(str(row["segment"]))
            if str(row["segment"]) in _BILLING_RISK_SEGMENT_ORDER
            else len(_BILLING_RISK_SEGMENT_ORDER),
            -int(row["count"]),
        )
    )
    return [row for row in results if int(row["count"]) > 0]


def get_billing_risk_aging_buckets(churn_rows: list[dict]) -> list[dict[str, int | str]]:
    buckets = {
        "Blocked 0-7 Days": 0,
        "Blocked 8-30 Days": 0,
        "Blocked 31-60 Days": 0,
        "Blocked 61+ Days": 0,
        "No Blocked Date": 0,
    }
    for row in churn_rows:
        blocked_date = _parse_iso_date_text(str(row.get("blocked_date") or ""))
        if blocked_date is None:
            buckets["No Blocked Date"] += 1
            continue
        blocked_days = max(0, (datetime.now(UTC).date() - blocked_date).days)
        if blocked_days <= 7:
            buckets["Blocked 0-7 Days"] += 1
        elif blocked_days <= 30:
            buckets["Blocked 8-30 Days"] += 1
        elif blocked_days <= 60:
            buckets["Blocked 31-60 Days"] += 1
        else:
            buckets["Blocked 61+ Days"] += 1
    return [{"label": label, "count": count} for label, count in buckets.items()]
