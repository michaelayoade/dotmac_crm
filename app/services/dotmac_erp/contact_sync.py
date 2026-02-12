"""Pull customers and contacts from DotMac ERP into Organization and Person models."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.person import ChannelType, PartyStatus, Person, PersonChannel
from app.models.subscriber import Organization
from app.services import settings_spec
from app.services.dotmac_erp.client import DotMacERPClient

logger = logging.getLogger(__name__)

# Map ERP party_status to local PartyStatus
_PARTY_STATUS_MAP: dict[str, PartyStatus] = {
    "lead": PartyStatus.lead,
    "contact": PartyStatus.contact,
    "customer": PartyStatus.customer,
    "subscriber": PartyStatus.subscriber,
}

# Map ERP channel types to local ChannelType
_CHANNEL_TYPE_MAP: dict[str, ChannelType] = {
    "email": ChannelType.email,
    "phone": ChannelType.phone,
    "sms": ChannelType.sms,
    "whatsapp": ChannelType.whatsapp,
    "facebook_messenger": ChannelType.facebook_messenger,
    "instagram_dm": ChannelType.instagram_dm,
}


@dataclass
class ContactSyncResult:
    """Result of a contact/customer sync operation."""

    orgs_created: int = 0
    orgs_updated: int = 0
    contacts_created: int = 0
    contacts_updated: int = 0
    contacts_linked: int = 0
    channels_upserted: int = 0
    errors: list[dict] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def total_synced(self) -> int:
        return self.orgs_created + self.orgs_updated + self.contacts_created + self.contacts_updated

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


class DotMacERPContactSync:
    """Pull customers (Organizations) and contacts (Persons) from DotMac ERP."""

    def __init__(self, db: Session):
        self.db = db
        self._client: DotMacERPClient | None = None
        self._org_cache: dict[str, Organization] = {}
        self._person_cache: dict[str, Person] = {}

    def _get_client(self) -> DotMacERPClient | None:
        """Get configured ERP client, or None if not configured."""
        if self._client is not None:
            return self._client

        enabled = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_contact_sync_enabled")
        if not enabled:
            return None

        base_url_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_base_url")
        token_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_token")

        base_url = str(base_url_value) if base_url_value else None
        token = str(token_value) if token_value else None

        if not base_url or not token:
            logger.warning("DotMac ERP contact sync enabled but not configured (missing URL or token)")
            return None

        timeout_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_timeout_seconds")
        timeout = int(timeout_value) if isinstance(timeout_value, int | str) else 30

        self._client = DotMacERPClient(base_url=base_url, token=token, timeout=timeout)
        return self._client

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    def _get_org_by_erp_id(self, erp_id: str) -> Organization | None:
        """Get Organization by erp_id with caching."""
        if erp_id in self._org_cache:
            return self._org_cache[erp_id]
        org = self.db.query(Organization).filter(Organization.erp_id == erp_id).first()
        if org:
            self._org_cache[erp_id] = org
        return org

    def _get_person_by_erp_customer_id(self, erp_customer_id: str) -> Person | None:
        """Get Person by erp_customer_id with caching."""
        if erp_customer_id in self._person_cache:
            return self._person_cache[erp_customer_id]
        person = self.db.query(Person).filter(Person.erp_customer_id == erp_customer_id).first()
        if person:
            self._person_cache[erp_customer_id] = person
        return person

    def _get_person_by_email(self, email: str) -> Person | None:
        """Fallback: get Person by email."""
        cache_key = f"email:{email.lower()}"
        if cache_key in self._person_cache:
            return self._person_cache[cache_key]
        person = self.db.query(Person).filter(Person.email == email.lower()).first()
        if person:
            self._person_cache[cache_key] = person
        return person

    def _upsert_organization(self, company: dict) -> str:
        """Upsert an Organization from ERP company data. Returns 'created', 'updated', or 'skipped'."""
        erp_id = company.get("customer_id")
        if not erp_id:
            return "skipped"

        org = self._get_org_by_erp_id(erp_id)
        address = company.get("address") or {}

        if org:
            org.name = company.get("customer_name") or org.name
            if company.get("legal_name"):
                org.legal_name = company["legal_name"]
            if company.get("tax_id"):
                org.tax_id = company["tax_id"]
            if company.get("domain"):
                org.domain = company["domain"]
            if company.get("website"):
                org.website = company["website"]
            if company.get("phone"):
                org.phone = self._normalize_phone(company["phone"])
            if company.get("email"):
                org.email = company["email"]
            if company.get("industry"):
                org.industry = company["industry"]
            if address:
                org.address_line1 = address.get("line1") or org.address_line1
                org.city = address.get("city") or org.city
                org.region = address.get("region") or org.region
                org.postal_code = address.get("postal_code") or org.postal_code
                org.country_code = address.get("country_code") or org.country_code
            org.is_active = company.get("is_active", True)
            return "updated"
        else:
            org = Organization(
                name=company.get("customer_name", "Unknown"),
                legal_name=company.get("legal_name"),
                tax_id=company.get("tax_id"),
                domain=company.get("domain"),
                website=company.get("website"),
                phone=self._normalize_phone(company.get("phone")),
                email=company.get("email"),
                industry=company.get("industry"),
                erp_id=erp_id,
                address_line1=address.get("line1"),
                address_line2=address.get("line2"),
                city=address.get("city"),
                region=address.get("region"),
                postal_code=address.get("postal_code"),
                country_code=address.get("country_code"),
                is_active=company.get("is_active", True),
            )
            self.db.add(org)
            self.db.flush()
            self._org_cache[erp_id] = org
            return "created"

    def _resolve_person(self, contact: dict) -> tuple[Person | None, str]:
        """Resolve ERP contact to local Person. Returns (person, action)."""
        contact_id = contact.get("contact_id")
        email = (contact.get("email") or "").lower()

        # Try by erp_customer_id first
        if contact_id:
            person = self._get_person_by_erp_customer_id(contact_id)
            if person:
                return person, "found_by_erp_id"

        # Fallback to email
        if email:
            person = self._get_person_by_email(email)
            if person:
                if contact_id and not person.erp_customer_id:
                    person.erp_customer_id = contact_id
                    self.db.flush()
                return person, "found_by_email"

        return None, "not_found"

    @staticmethod
    def _normalize_phone(raw: str | None) -> str | None:
        """Take first phone from comma-separated list, cap at 40 chars."""
        if not raw:
            return None
        first = raw.split(",")[0].strip()
        return first[:40] if first else None

    def _upsert_contact(self, contact: dict, result: ContactSyncResult) -> None:
        """Upsert a Person from ERP contact data."""
        contact_id = contact.get("contact_id")
        email = (contact.get("email") or "").lower()
        if not email:
            return

        person, match_type = self._resolve_person(contact)

        address = contact.get("address") or {}
        party_status = _PARTY_STATUS_MAP.get(contact.get("party_status", ""), PartyStatus.contact)

        if person:
            # Update existing
            if contact.get("first_name"):
                person.first_name = contact["first_name"]
            if contact.get("last_name"):
                person.last_name = contact["last_name"]
            if contact.get("phone"):
                person.phone = self._normalize_phone(contact["phone"])
            if contact.get("job_title"):
                person.job_title = contact["job_title"]
            person.party_status = party_status
            person.is_active = contact.get("is_active", True)
            if address:
                person.address_line1 = address.get("line1") or person.address_line1
                person.city = address.get("city") or person.city
                person.region = address.get("region") or person.region
                person.postal_code = address.get("postal_code") or person.postal_code
                person.country_code = address.get("country_code") or person.country_code

            if match_type == "found_by_email":
                result.contacts_linked += 1
            result.contacts_updated += 1
        else:
            # Create new Person
            person = Person(
                first_name=contact.get("first_name", ""),
                last_name=contact.get("last_name", ""),
                email=email,
                phone=self._normalize_phone(contact.get("phone")),
                job_title=contact.get("job_title"),
                party_status=party_status,
                erp_customer_id=contact_id,
                address_line1=address.get("line1"),
                city=address.get("city"),
                region=address.get("region"),
                postal_code=address.get("postal_code"),
                country_code=address.get("country_code"),
                is_active=contact.get("is_active", True),
            )
            self.db.add(person)
            self.db.flush()
            result.contacts_created += 1

        # Link to Organization if company_id provided
        company_id = contact.get("company_id")
        if company_id:
            org = self._get_org_by_erp_id(company_id)
            if org:
                person.organization_id = org.id

        # Upsert channels
        channels = contact.get("channels") or []
        for ch in channels:
            ch_type_str = ch.get("type", "")
            ch_type = _CHANNEL_TYPE_MAP.get(ch_type_str)
            ch_address = ch.get("address", "").strip()
            if not ch_type or not ch_address:
                continue

            existing = (
                self.db.query(PersonChannel)
                .filter(
                    PersonChannel.person_id == person.id,
                    PersonChannel.channel_type == ch_type,
                    PersonChannel.address == ch_address,
                )
                .first()
            )
            if existing:
                existing.is_primary = ch.get("is_primary", False)
            else:
                self.db.add(
                    PersonChannel(
                        person_id=person.id,
                        channel_type=ch_type,
                        address=ch_address,
                        is_primary=ch.get("is_primary", False),
                    )
                )
            result.channels_upserted += 1

        if contact_id:
            self._person_cache[contact_id] = person

    def sync_organizations(self) -> ContactSyncResult:
        """Pull companies from ERP and sync to Organization model."""
        start_time = datetime.now(UTC)
        result = ContactSyncResult()

        client = self._get_client()
        if not client:
            result.errors.append({"type": "config", "error": "ERP contact sync not configured or disabled"})
            return result

        try:
            offset = 0
            limit = 500
            while True:
                companies = client.get_companies(limit=limit, offset=offset)
                if not companies:
                    break

                logger.info("Fetched %d companies from ERP (offset=%d)", len(companies), offset)

                for company in companies:
                    try:
                        savepoint = self.db.begin_nested()
                        action = self._upsert_organization(company)
                        savepoint.commit()
                        if action == "created":
                            result.orgs_created += 1
                        elif action == "updated":
                            result.orgs_updated += 1
                    except Exception as e:
                        savepoint.rollback()
                        erp_id = company.get("customer_id", "?")
                        logger.error("Failed to sync company %s: %s", erp_id, e)
                        result.errors.append({"type": "company", "erp_id": erp_id, "error": str(e)})

                if len(companies) < limit:
                    break
                offset += limit

            self.db.commit()

        except Exception as e:
            logger.error("Organization sync failed: %s", e)
            result.errors.append({"type": "api", "error": str(e)})
            self.db.rollback()

        result.duration_seconds = (datetime.now(UTC) - start_time).total_seconds()
        return result

    def sync_contacts(self) -> ContactSyncResult:
        """Pull contacts from ERP and sync to Person model."""
        start_time = datetime.now(UTC)
        result = ContactSyncResult()

        client = self._get_client()
        if not client:
            result.errors.append({"type": "config", "error": "ERP contact sync not configured or disabled"})
            return result

        try:
            offset = 0
            limit = 500
            while True:
                contacts = client.get_contacts(limit=limit, offset=offset)
                if not contacts:
                    break

                logger.info("Fetched %d contacts from ERP (offset=%d)", len(contacts), offset)

                for contact in contacts:
                    try:
                        savepoint = self.db.begin_nested()
                        self._upsert_contact(contact, result)
                        savepoint.commit()
                    except Exception as e:
                        savepoint.rollback()
                        contact_id = contact.get("contact_id", "?")
                        logger.error("Failed to sync contact %s: %s", contact_id, e)
                        result.errors.append({"type": "contact", "erp_id": contact_id, "error": str(e)})

                if len(contacts) < limit:
                    break
                offset += limit

            self.db.commit()

        except Exception as e:
            logger.error("Contact sync failed: %s", e)
            result.errors.append({"type": "api", "error": str(e)})
            self.db.rollback()

        result.duration_seconds = (datetime.now(UTC) - start_time).total_seconds()
        return result

    def sync_all(self) -> ContactSyncResult:
        """Sync both organizations and contacts from ERP."""
        start_time = datetime.now(UTC)
        combined = ContactSyncResult()

        # Sync orgs first so contacts can link to them
        org_result = self.sync_organizations()
        combined.orgs_created = org_result.orgs_created
        combined.orgs_updated = org_result.orgs_updated
        combined.errors.extend(org_result.errors)

        # Then sync contacts
        contact_result = self.sync_contacts()
        combined.contacts_created = contact_result.contacts_created
        combined.contacts_updated = contact_result.contacts_updated
        combined.contacts_linked = contact_result.contacts_linked
        combined.channels_upserted = contact_result.channels_upserted
        combined.errors.extend(contact_result.errors)

        combined.duration_seconds = (datetime.now(UTC) - start_time).total_seconds()

        logger.info(
            "Contact sync complete: %d orgs created, %d updated, %d contacts created, %d updated, %d linked",
            combined.orgs_created,
            combined.orgs_updated,
            combined.contacts_created,
            combined.contacts_updated,
            combined.contacts_linked,
        )

        return combined


def dotmac_erp_contact_sync(db: Session) -> DotMacERPContactSync:
    """Create a DotMac ERP contact sync service instance."""
    return DotMacERPContactSync(db)
