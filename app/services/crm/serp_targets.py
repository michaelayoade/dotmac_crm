"""SERP API target discovery for CRM campaigns."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models.crm.campaign import Campaign, CampaignRecipient
from app.models.crm.enums import CampaignChannel, CampaignRecipientStatus, CampaignStatus
from app.models.crm.sales import Lead
from app.models.domain_settings import SettingDomain
from app.models.person import PartyStatus, Person
from app.models.subscriber import AccountType, Organization, Subscriber, SubscriberStatus
from app.services.common import coerce_uuid
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)

SERP_SOURCE_REPORT = "serp_google"
SERP_AUDIENCE_MODE = "manual_snapshot"
SERP_OUTREACH_KIND = "outreach"

_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")


@dataclass(frozen=True, slots=True)
class SerpTarget:
    title: str
    link: str
    snippet: str
    domain: str
    email: str | None
    phone: str | None
    position: int | None


@dataclass(frozen=True, slots=True)
class ExistingCustomerMatch:
    source: str
    field: str
    value: str


@dataclass(slots=True)
class CustomerExclusionIndex:
    phones: dict[str, ExistingCustomerMatch]
    emails: dict[str, ExistingCustomerMatch]
    domains: dict[str, ExistingCustomerMatch]
    names: dict[str, ExistingCustomerMatch]


def _campaign_metadata(campaign: Campaign) -> dict:
    metadata = getattr(campaign, "metadata_", None)
    return dict(metadata) if isinstance(metadata, dict) else {}


def _clean_domain(link: str) -> str:
    parsed = urlparse(link)
    host = (parsed.netloc or parsed.path).split("/")[0].strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _extract_first_email(*values: str) -> str | None:
    for value in values:
        match = _EMAIL_RE.search(value or "")
        if match:
            return match.group(0).lower()
    return None


def _extract_first_phone(*values: str) -> str | None:
    for value in values:
        match = _PHONE_RE.search(value or "")
        if match:
            digits = "".join(ch for ch in match.group(0) if ch.isdigit())
            if 8 <= len(digits) <= 15:
                return f"+{digits}" if match.group(0).strip().startswith("+") else digits
    return None


def _phone_match_keys(value: str | None) -> set[str]:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        return set()
    keys = {digits}
    if len(digits) >= 10:
        keys.add(digits[-10:])
    if digits.startswith("0") and len(digits) >= 10:
        keys.add(f"234{digits[1:]}")
    if digits.startswith("234") and len(digits) > 10:
        keys.add(f"0{digits[3:]}")
    return {key for key in keys if len(key) >= 8}


def _normalize_email(value: str | None) -> str | None:
    email = str(value or "").strip().lower()
    return email if _EMAIL_RE.fullmatch(email) else None


def _email_match_keys(value: str | None) -> set[str]:
    email = _normalize_email(value)
    return {email} if email else set()


def _domain_match_keys(value: str | None) -> set[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return set()
    domain = _clean_domain(raw)
    if not domain:
        return set()
    keys = {domain}
    if domain.startswith("www."):
        keys.add(domain[4:])
    return {key for key in keys if "." in key}


def _name_match_keys(value: str | None) -> set[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    if not normalized:
        return set()
    words = [word for word in normalized.split() if word]
    if not words:
        return set()
    keys = {" ".join(words)}
    legal_suffixes = {"limited", "ltd", "plc", "llc", "inc", "incorporated", "company", "co"}
    trimmed = list(words)
    while trimmed and trimmed[-1] in legal_suffixes:
        trimmed.pop()
    if trimmed:
        keys.add(" ".join(trimmed))
        if len(trimmed[-1]) > 4 and trimmed[-1].endswith("s"):
            singular = [*trimmed[:-1], trimmed[-1][:-1]]
            keys.add(" ".join(singular))
    return {key for key in keys if len(key) >= 3}


def _put_match(
    bucket: dict[str, ExistingCustomerMatch],
    keys: set[str],
    *,
    source: str,
    field: str,
    value: str | None,
) -> None:
    clean_value = str(value or "").strip()
    if not clean_value:
        return
    match = ExistingCustomerMatch(source=source, field=field, value=clean_value)
    for key in keys:
        bucket.setdefault(key, match)


def _add_person_to_exclusion_index(index: CustomerExclusionIndex, person: Person, *, source: str) -> None:
    _put_match(index.emails, _email_match_keys(person.email), source=source, field="email", value=person.email)
    _put_match(index.phones, _phone_match_keys(person.phone), source=source, field="phone", value=person.phone)
    for channel in person.channels or []:
        address = str(channel.address or "").strip()
        _put_match(index.emails, _email_match_keys(address), source=source, field="email", value=address)
        _put_match(index.phones, _phone_match_keys(address), source=source, field="phone", value=address)
    for name in (person.display_name, f"{person.first_name} {person.last_name}".strip()):
        _put_match(index.names, _name_match_keys(name), source=source, field="name", value=name)


def _add_organization_to_exclusion_index(
    index: CustomerExclusionIndex, organization: Organization, *, source: str
) -> None:
    for name in (organization.name, organization.legal_name):
        _put_match(index.names, _name_match_keys(name), source=source, field="name", value=name)
    _put_match(
        index.emails, _email_match_keys(organization.email), source=source, field="email", value=organization.email
    )
    _put_match(
        index.phones, _phone_match_keys(organization.phone), source=source, field="phone", value=organization.phone
    )
    for domain_value in (organization.domain, organization.website):
        _put_match(index.domains, _domain_match_keys(domain_value), source=source, field="domain", value=domain_value)


def _build_customer_exclusion_index(db: Session) -> CustomerExclusionIndex:
    index = CustomerExclusionIndex(phones={}, emails={}, domains={}, names={})

    customer_people = (
        db.query(Person)
        .options(joinedload(Person.channels), joinedload(Person.organization))
        .filter(Person.is_active.is_(True))
        .filter(Person.party_status.in_([PartyStatus.customer, PartyStatus.subscriber]))
        .all()
    )
    for person in customer_people:
        _add_person_to_exclusion_index(index, person, source="dotmac_person")
        if person.organization:
            _add_organization_to_exclusion_index(index, person.organization, source="dotmac_organization")

    customer_organizations = (
        db.query(Organization)
        .filter(Organization.is_active.is_(True))
        .filter(Organization.account_type == AccountType.customer)
        .all()
    )
    for organization in customer_organizations:
        _add_organization_to_exclusion_index(index, organization, source="dotmac_organization")

    active_subscribers = (
        db.query(Subscriber)
        .options(joinedload(Subscriber.person).joinedload(Person.channels), joinedload(Subscriber.organization))
        .filter(Subscriber.is_active.is_(True))
        .filter(Subscriber.status != SubscriberStatus.terminated)
        .all()
    )
    for subscriber in active_subscribers:
        source = "splynx_subscriber" if subscriber.external_system == "splynx" else "dotmac_subscriber"
        _put_match(
            index.names,
            _name_match_keys(subscriber.subscriber_number),
            source=source,
            field="name",
            value=subscriber.subscriber_number,
        )
        if subscriber.person:
            _add_person_to_exclusion_index(index, subscriber.person, source=source)
        if subscriber.organization:
            _add_organization_to_exclusion_index(index, subscriber.organization, source=source)

    return index


def _existing_customer_match(index: CustomerExclusionIndex, target: SerpTarget) -> ExistingCustomerMatch | None:
    for key in _phone_match_keys(target.phone):
        if match := index.phones.get(key):
            return match
    if target.email:
        email = _normalize_email(target.email)
        if email and (match := index.emails.get(email)):
            return match
    for key in _domain_match_keys(target.domain) | _domain_match_keys(target.link):
        if match := index.domains.get(key):
            return match
    for key in _name_match_keys(target.title):
        if match := index.names.get(key):
            return match
    return None


def _result_position(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _email_from_pattern(pattern: str, domain: str) -> str | None:
    clean_domain = domain.strip().lower()
    if not clean_domain or "." not in clean_domain:
        return None
    clean_pattern = (pattern or "").strip() or "info@{domain}"
    if "{domain}" in clean_pattern:
        email = clean_pattern.replace("{domain}", clean_domain)
    else:
        email = clean_pattern
    return email.lower() if _EMAIL_RE.fullmatch(email) else None


def _fallback_email_for_target(target: SerpTarget) -> str:
    local_part = re.sub(r"[^a-z0-9]+", "-", (target.domain or target.title).lower()).strip("-")
    return f"serp-{local_part or 'target'}@invalid.local"[:255]


def _resolve_serpapi_key(db: Session) -> str:
    configured = resolve_value(db, SettingDomain.integration, "serpapi_api_key")
    api_key = str(configured or os.getenv("SERPAPI_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="SERP API key is not configured.")
    return api_key


def _resolve_timeout(db: Session) -> float:
    configured = resolve_value(db, SettingDomain.integration, "serpapi_timeout_seconds")
    try:
        return float(configured) if isinstance(configured, int | str | float) else 20.0
    except (TypeError, ValueError):
        return 20.0


def _is_unsupported_location_error(detail: str) -> bool:
    normalized = detail.strip().lower()
    return "unsupported" in normalized and "location" in normalized


def _location_retry_candidates(location: str | None) -> list[str]:
    normalized = str(location or "").strip().lower()
    if not normalized:
        return []
    candidates: list[str] = []
    if "fct" in normalized or "federal capital" in normalized or "gwarimpa" in normalized or "gwarinpa" in normalized:
        candidates.append("Abuja, Federal Capital Territory, Nigeria")
    return candidates


def _google_maps_location(location: str | None, query: str) -> str | None:
    normalized = f"{location or ''} {query or ''}".strip().lower()
    if "gwarimpa" in normalized or "gwarinpa" in normalized:
        return "Gwarinpa, Abuja, Federal Capital Territory, Nigeria"
    if "fct" in normalized or "federal capital" in normalized:
        return "Abuja, Federal Capital Territory, Nigeria"
    return location.strip() if location and location.strip() else None


def _serpapi_payload(params: dict[str, str | int], *, timeout: float) -> dict:
    try:
        response = httpx.get("https://serpapi.com/search.json", params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        detail = "SERP API request failed."
        try:
            body = exc.response.json()
            detail = str(body.get("error") or detail)
        except ValueError:
            pass
        raise HTTPException(status_code=502, detail=detail) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="SERP API request failed.") from exc

    if not isinstance(payload, dict):
        return {}
    if payload.get("error"):
        raise HTTPException(status_code=502, detail=str(payload["error"]))
    return payload


def _targets_from_organic_results(
    organic_results: object,
    *,
    email_pattern: str,
    result_limit: int,
    seen_domains: set[str],
) -> list[SerpTarget]:
    if not isinstance(organic_results, list):
        return []
    targets: list[SerpTarget] = []
    for row in organic_results:
        if not isinstance(row, dict):
            continue
        link = str(row.get("link") or "").strip()
        domain = _clean_domain(link)
        if not link or not domain or domain in seen_domains:
            continue
        title = str(row.get("title") or domain).strip()
        snippet = str(row.get("snippet") or "").strip()
        email = _extract_first_email(title, snippet, link) or _email_from_pattern(email_pattern, domain)
        phone = _extract_first_phone(title, snippet)
        targets.append(
            SerpTarget(
                title=title[:200],
                link=link,
                snippet=snippet[:1000],
                domain=domain,
                email=email,
                phone=phone,
                position=_result_position(row.get("position")),
            )
        )
        seen_domains.add(domain)
        if len(targets) >= result_limit:
            break
    return targets


def _targets_from_local_results(
    local_results: object,
    *,
    email_pattern: str,
    result_limit: int,
    seen_domains: set[str],
) -> list[SerpTarget]:
    rows: list[object] = []
    if isinstance(local_results, list):
        rows = local_results
    elif isinstance(local_results, dict):
        places = local_results.get("places")
        if isinstance(places, list):
            rows = places
    targets: list[SerpTarget] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("name") or "").strip()
        link = str(row.get("link") or row.get("website") or row.get("place_id_search") or "").strip()
        domain = _clean_domain(link) if link else ""
        dedupe_key = domain or title.lower()
        if not title or not dedupe_key or dedupe_key in seen_domains:
            continue
        address = str(row.get("address") or "").strip()
        snippet = str(row.get("description") or row.get("type") or address).strip()
        phone = _extract_first_phone(str(row.get("phone") or ""), snippet)
        email = _extract_first_email(title, snippet, link) or (
            _email_from_pattern(email_pattern, domain) if domain else None
        )
        targets.append(
            SerpTarget(
                title=title[:200],
                link=link,
                snippet=(snippet or address)[:1000],
                domain=domain or dedupe_key,
                email=email,
                phone=phone,
                position=_result_position(row.get("position")),
            )
        )
        seen_domains.add(dedupe_key)
        if len(targets) >= result_limit:
            break
    return targets


def _targets_from_payload(payload: dict, *, email_pattern: str, result_limit: int) -> list[SerpTarget]:
    seen_domains: set[str] = set()
    targets = _targets_from_local_results(
        payload.get("local_results"),
        email_pattern=email_pattern,
        result_limit=result_limit,
        seen_domains=seen_domains,
    )
    if len(targets) < result_limit:
        targets.extend(
            _targets_from_organic_results(
                payload.get("organic_results"),
                email_pattern=email_pattern,
                result_limit=result_limit - len(targets),
                seen_domains=seen_domains,
            )
        )
    return targets


def search_google_targets(
    db: Session,
    *,
    query: str,
    location: str | None,
    max_results: int,
    email_pattern: str,
) -> list[SerpTarget]:
    clean_query = (query or "").strip()
    if len(clean_query) < 3:
        raise HTTPException(status_code=400, detail="Search query is required.")
    result_limit = max(1, min(int(max_results or 10), 50))
    params: dict[str, str | int] = {
        "engine": "google",
        "q": clean_query,
        "api_key": _resolve_serpapi_key(db),
        "num": result_limit,
    }
    if location and location.strip():
        params["location"] = location.strip()

    try:
        payload = _serpapi_payload(params, timeout=_resolve_timeout(db))
    except HTTPException as exc:
        if "location" not in params or not _is_unsupported_location_error(str(exc.detail)):
            raise
        unsupported_location = str(params.get("location") or "")
        retry_params = dict(params)
        for candidate in _location_retry_candidates(unsupported_location):
            retry_params["location"] = candidate
            try:
                payload = _serpapi_payload(retry_params, timeout=_resolve_timeout(db))
                break
            except HTTPException as retry_exc:
                if not _is_unsupported_location_error(str(retry_exc.detail)):
                    raise
        else:
            retry_params.pop("location", None)
            logger.info(
                "serpapi_location_unsupported_retry_without_location location=%s query=%s",
                unsupported_location,
                clean_query,
            )
            payload = _serpapi_payload(retry_params, timeout=_resolve_timeout(db))
    targets = _targets_from_payload(payload, email_pattern=email_pattern, result_limit=result_limit)
    return targets


def search_google_maps_targets(
    db: Session,
    *,
    query: str,
    location: str | None,
    max_results: int,
    email_pattern: str,
) -> list[SerpTarget]:
    clean_query = (query or "").strip()
    if len(clean_query) < 3:
        raise HTTPException(status_code=400, detail="Search query is required.")
    result_limit = max(1, min(int(max_results or 10), 50))
    params: dict[str, str | int] = {
        "engine": "google_maps",
        "type": "search",
        "q": clean_query,
        "api_key": _resolve_serpapi_key(db),
        "hl": "en",
        "gl": "ng",
    }
    maps_location = _google_maps_location(location, clean_query)
    if maps_location:
        params["location"] = maps_location
        params["z"] = 15

    try:
        payload = _serpapi_payload(params, timeout=_resolve_timeout(db))
    except HTTPException as exc:
        if "location" not in params or not _is_unsupported_location_error(str(exc.detail)):
            raise
        retry_params = dict(params)
        retry_params.pop("location", None)
        retry_params.pop("z", None)
        logger.info(
            "serpapi_maps_location_unsupported_retry_without_location location=%s query=%s",
            params.get("location"),
            clean_query,
        )
        payload = _serpapi_payload(retry_params, timeout=_resolve_timeout(db))

    return _targets_from_local_results(
        payload.get("local_results"),
        email_pattern=email_pattern,
        result_limit=result_limit,
        seen_domains=set(),
    )


def _person_name_from_target(target: SerpTarget) -> tuple[str, str, str]:
    display_name = (target.title or target.domain).strip()[:120]
    first_name = display_name[:80] or target.domain[:80] or "SERP Lead"
    return first_name, "", display_name


def _get_or_create_person(db: Session, target: SerpTarget) -> Person:
    person = None
    if target.email:
        person = db.query(Person).filter(Person.email == target.email).first()
    if not person and target.phone:
        person = db.query(Person).filter(Person.phone == target.phone).first()
    metadata = {
        "serp": {
            "source": SERP_SOURCE_REPORT,
            "domain": target.domain,
            "link": target.link,
            "snippet": target.snippet,
            "position": target.position,
            "discovered_at": datetime.now(UTC).isoformat(),
        }
    }
    if person:
        existing_metadata = dict(person.metadata_) if isinstance(person.metadata_, dict) else {}
        existing_metadata.update(metadata)
        person.metadata_ = existing_metadata
        if person.party_status == PartyStatus.contact:
            person.party_status = PartyStatus.lead
        return person

    first_name, last_name, display_name = _person_name_from_target(target)
    person = Person(
        first_name=first_name,
        last_name=last_name,
        display_name=display_name,
        email=target.email or _fallback_email_for_target(target),
        phone=target.phone,
        party_status=PartyStatus.lead,
        marketing_opt_in=False,
        notes=f"Discovered from Google SERP result: {target.link}",
        metadata_=metadata,
    )
    db.add(person)
    db.flush()
    return person


def _ensure_lead(db: Session, person: Person, target: SerpTarget) -> None:
    existing = db.query(Lead).filter(Lead.person_id == person.id, Lead.is_active.is_(True)).first()
    if existing:
        return
    db.add(
        Lead(
            person_id=person.id,
            title=f"SERP prospect: {target.domain}"[:200],
            lead_source="SERP",
            notes=f"{target.title}\n{target.link}\n\n{target.snippet}".strip(),
            metadata_={
                "serp": {
                    "source": SERP_SOURCE_REPORT,
                    "domain": target.domain,
                    "link": target.link,
                    "position": target.position,
                }
            },
        )
    )


def seed_campaign_from_serp(
    db: Session,
    *,
    campaign_id: str,
    query: str,
    location: str | None,
    max_results: int,
    email_pattern: str,
) -> dict[str, int]:
    campaign = db.get(Campaign, coerce_uuid(campaign_id))
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status != CampaignStatus.draft:
        raise HTTPException(status_code=400, detail="SERP targets can only be added to draft campaigns.")

    targets = search_google_maps_targets(
        db,
        query=query,
        location=location,
        max_results=max_results,
        email_pattern=email_pattern,
    )
    existing_person_ids = {
        pid
        for (pid,) in db.query(CampaignRecipient.person_id)
        .filter(CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.step_id.is_(None))
        .all()
    }
    selected = len(targets)
    seeded = 0
    skipped = 0
    skipped_existing_customers = 0
    existing_customer_matches: list[dict[str, str]] = []
    snapshot_rows: list[dict[str, str]] = []
    customer_exclusion_index = _build_customer_exclusion_index(db)

    for target in targets:
        if match := _existing_customer_match(customer_exclusion_index, target):
            skipped += 1
            skipped_existing_customers += 1
            existing_customer_matches.append(
                {
                    "name": target.title,
                    "domain": target.domain,
                    "source_url": target.link,
                    "matched_source": match.source,
                    "matched_field": match.field,
                    "matched_value": match.value,
                }
            )
            continue
        address = target.email if campaign.channel == CampaignChannel.email else target.phone
        if not address:
            skipped += 1
            continue
        person = _get_or_create_person(db, target)
        _ensure_lead(db, person, target)
        if person.id in existing_person_ids:
            skipped += 1
            continue
        db.add(
            CampaignRecipient(
                campaign_id=campaign.id,
                person_id=person.id,
                address=address,
                email=target.email if campaign.channel == CampaignChannel.email else None,
                status=CampaignRecipientStatus.pending,
            )
        )
        existing_person_ids.add(person.id)
        seeded += 1
        snapshot_rows.append(
            {
                "person_id": str(person.id),
                "name": person.display_name or person.first_name,
                "email": target.email or "",
                "phone": target.phone or "",
                "domain": target.domain,
                "source_url": target.link,
                "serp_position": str(target.position or ""),
            }
        )

    metadata = _campaign_metadata(campaign)
    metadata["kind"] = metadata.get("kind") or SERP_OUTREACH_KIND
    metadata["source_report"] = SERP_SOURCE_REPORT
    metadata["audience_mode"] = SERP_AUDIENCE_MODE
    metadata["serp_last_query"] = {
        "query": query.strip(),
        "location": (location or "").strip(),
        "engine": "google_maps",
        "search_type": "search",
        "result_kind": "business",
        "max_results": max_results,
        "email_pattern": email_pattern.strip() or "info@{domain}",
        "selected": selected,
        "seeded": seeded,
        "skipped": skipped,
        "skipped_existing_customers": skipped_existing_customers,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if existing_customer_matches:
        metadata["serp_existing_customer_skips"] = existing_customer_matches[-100:]
    existing_snapshot = metadata.get("audience_snapshot")
    existing_rows = existing_snapshot if isinstance(existing_snapshot, list) else []
    metadata["audience_snapshot"] = existing_rows + snapshot_rows
    metadata["audience_snapshot_count"] = len(metadata["audience_snapshot"])
    campaign.metadata_ = metadata
    db.flush()
    campaign.total_recipients = (
        db.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.step_id.is_(None))
        .count()
    )
    db.commit()
    return {"selected": selected, "seeded": seeded, "skipped": skipped}
