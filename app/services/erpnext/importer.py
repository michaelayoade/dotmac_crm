"""ERPNext data importer.

Imports data from ERPNext into DotMac with idempotent upserts
using ExternalReference tracking.

Usage:
    from app.services.erpnext import ERPNextImporter

    importer = ERPNextImporter(
        base_url="https://erp.example.com",
        api_key="key",
        api_secret="secret",
        connector_config_id=uuid,
    )

    stats = importer.import_all(db)
    print(f"Created: {stats['created']}, Updated: {stats['updated']}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING
from uuid import UUID

from app.logging import get_logger
from app.models.external import ExternalEntityType
from app.services.erpnext.client import ERPNextClient, ERPNextError
from app.services.erpnext.mappers import (
    map_hd_ticket,
    map_project,
    map_task,
    map_contact,
    map_customer,
    map_lead,
    map_quotation,
    HD_TICKET_FIELDS,
    PROJECT_FIELDS,
    TASK_FIELDS,
    CONTACT_FIELDS,
    CUSTOMER_FIELDS,
    LEAD_FIELDS,
    QUOTATION_FIELDS,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = get_logger(__name__)


@dataclass
class ImportStats:
    """Statistics from import operation."""
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    error_messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "errors": self.errors,
            "error_messages": self.error_messages[:10],  # Limit error messages
        }


@dataclass
class ImportResult:
    """Result from full import operation."""
    success: bool
    contacts: ImportStats = field(default_factory=ImportStats)
    customers: ImportStats = field(default_factory=ImportStats)
    tickets: ImportStats = field(default_factory=ImportStats)
    projects: ImportStats = field(default_factory=ImportStats)
    tasks: ImportStats = field(default_factory=ImportStats)
    leads: ImportStats = field(default_factory=ImportStats)
    quotes: ImportStats = field(default_factory=ImportStats)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "contacts": self.contacts.to_dict(),
            "customers": self.customers.to_dict(),
            "tickets": self.tickets.to_dict(),
            "projects": self.projects.to_dict(),
            "tasks": self.tasks.to_dict(),
            "leads": self.leads.to_dict(),
            "quotes": self.quotes.to_dict(),
        }


class ERPNextImporter:
    """Imports data from ERPNext into DotMac.

    Uses ExternalReference to track imported records and prevent duplicates.
    Supports incremental imports via external ID tracking.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        connector_config_id: UUID | str | None = None,
    ):
        from app.services.common import coerce_uuid

        self.client = ERPNextClient(base_url, api_key, api_secret)
        self.connector_config_id = coerce_uuid(connector_config_id) if connector_config_id else None

        # Cache for lookups during import
        self._person_cache: dict[str, UUID] = {}  # erpnext_name -> person_id
        self._org_cache: dict[str, UUID] = {}     # erpnext_name -> org_id
        self._project_cache: dict[str, UUID] = {} # erpnext_name -> project_id

    def test_connection(self) -> bool:
        """Test ERPNext API connection."""
        return self.client.test_connection()

    def import_all(self, db: "Session") -> ImportResult:
        """Import all doctypes from ERPNext.

        Order matters for foreign key relationships:
        1. Contacts (creates Persons)
        2. Customers (creates Organizations, references Contacts)
        3. Projects (references Customers)
        4. Tasks (references Projects)
        5. Tickets (references Customers, Contacts)
        6. Leads (creates Persons)
        7. Quotations (references Leads/Customers)
        """
        result = ImportResult(success=True)

        try:
            # 1. Import Contacts first (creates Person records)
            logger.info("erpnext_import_starting doctype=Contact")
            result.contacts = self._import_contacts(db)

            # 2. Import Customers (creates Organization + Subscriber records)
            logger.info("erpnext_import_starting doctype=Customer")
            result.customers = self._import_customers(db)

            # 3. Import Projects
            logger.info("erpnext_import_starting doctype=Project")
            result.projects = self._import_projects(db)

            # 4. Import Tasks (after Projects)
            logger.info("erpnext_import_starting doctype=Task")
            result.tasks = self._import_tasks(db)

            # 5. Import HD Tickets
            logger.info("erpnext_import_starting doctype=HD Ticket")
            result.tickets = self._import_tickets(db)

            # 6. Import Leads
            logger.info("erpnext_import_starting doctype=Lead")
            result.leads = self._import_leads(db)

            # 7. Import Quotations
            logger.info("erpnext_import_starting doctype=Quotation")
            result.quotes = self._import_quotations(db)

            logger.info("erpnext_import_completed result=%s", result.to_dict())

        except ERPNextError as e:
            logger.error("erpnext_import_failed error=%s", e)
            result.success = False

        return result

    def _get_or_create_external_ref(
        self,
        db: "Session",
        entity_type: ExternalEntityType,
        external_id: str,
        entity_id: UUID | None = None,
    ) -> tuple[Any, bool]:
        """Get or create external reference for idempotent imports.

        Returns (external_ref, is_new) tuple.
        """
        from app.models.external import ExternalReference

        # Look up by external ID
        ref = (
            db.query(ExternalReference)
            .filter(ExternalReference.connector_config_id == self.connector_config_id)
            .filter(ExternalReference.entity_type == entity_type)
            .filter(ExternalReference.external_id == external_id)
            .first()
        )

        if ref:
            return ref, False

        # Create new reference
        ref = ExternalReference(
            connector_config_id=self.connector_config_id,
            entity_type=entity_type,
            external_id=external_id,
            entity_id=entity_id,
        )
        return ref, True

    def _import_contacts(self, db: "Session") -> ImportStats:
        """Import ERPNext Contacts as Person records."""
        from app.models.person import Person
        from app.models.external import ExternalEntityType

        stats = ImportStats()

        for doc in self.client.get_all("Contact", fields=CONTACT_FIELDS):
            try:
                external_id = doc.get("name")
                if not external_id:
                    continue

                # Check if already imported
                ref, is_new = self._get_or_create_external_ref(
                    db, ExternalEntityType.person, external_id
                )

                # Map ERPNext doc to Person data
                data = map_contact(doc)
                email = data.get("email")

                if is_new:
                    # Check if person with this email already exists
                    existing = db.query(Person).filter(Person.email == email).first()
                    if existing:
                        # Check if there's already an ExternalReference for this Person
                        from app.models.external import ExternalReference
                        existing_ref = (
                            db.query(ExternalReference)
                            .filter(ExternalReference.connector_config_id == self.connector_config_id)
                            .filter(ExternalReference.entity_type == ExternalEntityType.person)
                            .filter(ExternalReference.entity_id == existing.id)
                            .first()
                        )
                        if existing_ref:
                            # Person already linked, just cache and skip
                            self._person_cache[external_id] = existing.id
                            stats.skipped += 1
                            continue

                        # Link this Contact to existing person
                        ref.entity_id = existing.id
                        db.add(ref)
                        self._person_cache[external_id] = existing.id
                        stats.skipped += 1
                        continue

                    # Create new person
                    person = Person(
                        first_name=data["first_name"],
                        last_name=data["last_name"],
                        email=email,
                        phone=data.get("phone"),
                        gender=data.get("gender"),
                        is_active=data.get("is_active", True),
                    )
                    db.add(person)
                    db.flush()

                    ref.entity_id = person.id
                    ref.metadata_ = {
                        "erpnext_company": data.get("_erpnext_company"),
                    }
                    db.add(ref)

                    self._person_cache[external_id] = person.id
                    stats.created += 1
                else:
                    # Update existing person
                    person = db.get(Person, ref.entity_id)
                    if person:
                        person.first_name = data["first_name"]
                        person.last_name = data["last_name"]
                        person.phone = data.get("phone") or person.phone
                        self._person_cache[external_id] = person.id
                        stats.updated += 1

                db.commit()

            except Exception as e:
                db.rollback()
                stats.errors += 1
                stats.error_messages.append(f"Contact {doc.get('name')}: {e}")
                logger.warning("erpnext_import_contact_error doc=%s error=%s", doc.get("name"), e)

        return stats

    def _import_customers(self, db: "Session") -> ImportStats:
        """Import ERPNext Customers as Organization + Subscriber records."""
        from app.models.subscriber import Organization, Subscriber
        from app.models.external import ExternalEntityType

        stats = ImportStats()

        for doc in self.client.get_all("Customer", fields=CUSTOMER_FIELDS):
            try:
                external_id = doc.get("name")
                if not external_id:
                    continue

                # Check if already imported
                ref, is_new = self._get_or_create_external_ref(
                    db, ExternalEntityType.subscriber, external_id
                )

                data = map_customer(doc)

                if is_new:
                    # Create Organization
                    org = Organization(
                        name=data["name"],
                        legal_name=data.get("legal_name"),
                        tax_id=data.get("tax_id"),
                        website=data.get("website"),
                        notes=data.get("notes"),
                    )
                    db.add(org)
                    db.flush()

                    self._org_cache[external_id] = org.id

                    # Generate unique subscriber_number (max 60 chars)
                    # If name > 50 chars, truncate and add hash suffix for uniqueness
                    if external_id and len(external_id) > 50:
                        import hashlib
                        hash_suffix = hashlib.md5(external_id.encode()).hexdigest()[:8]
                        subscriber_number = f"{external_id[:50]}-{hash_suffix}"
                    else:
                        subscriber_number = external_id[:60] if external_id else None

                    # Create Subscriber linked to Organization
                    subscriber = Subscriber(
                        organization_id=org.id,
                        external_id=external_id[:200] if external_id else None,
                        external_system="erpnext",
                        subscriber_number=subscriber_number,
                        is_active=True,
                    )
                    db.add(subscriber)
                    db.flush()

                    ref.entity_id = subscriber.id
                    ref.metadata_ = {
                        "organization_id": str(org.id),
                        "erpnext_customer_type": data.get("_erpnext_customer_type"),
                        "erpnext_customer_group": data.get("_erpnext_customer_group"),
                    }
                    db.add(ref)

                    stats.created += 1
                else:
                    # Update existing
                    subscriber = db.get(Subscriber, ref.entity_id)
                    if subscriber and subscriber.organization:
                        org = subscriber.organization
                        org.name = data["name"]
                        org.legal_name = data.get("legal_name")
                        org.tax_id = data.get("tax_id") or org.tax_id
                        self._org_cache[external_id] = org.id
                        stats.updated += 1

                db.commit()

            except Exception as e:
                db.rollback()
                stats.errors += 1
                stats.error_messages.append(f"Customer {doc.get('name')}: {e}")
                logger.warning("erpnext_import_customer_error doc=%s error=%s", doc.get("name"), e)

        return stats

    def _import_projects(self, db: "Session") -> ImportStats:
        """Import ERPNext Projects."""
        from app.models.projects import Project
        from app.models.external import ExternalEntityType

        stats = ImportStats()

        for doc in self.client.get_all("Project", fields=PROJECT_FIELDS):
            try:
                external_id = doc.get("name")
                if not external_id:
                    continue

                ref, is_new = self._get_or_create_external_ref(
                    db, ExternalEntityType.project, external_id
                )

                data = map_project(doc)

                # Resolve customer to subscriber
                subscriber_id = None
                erpnext_customer = data.get("_erpnext_customer")
                if erpnext_customer:
                    # Look up subscriber by external ID
                    from app.models.external import ExternalReference
                    cust_ref = (
                        db.query(ExternalReference)
                        .filter(ExternalReference.connector_config_id == self.connector_config_id)
                        .filter(ExternalReference.entity_type == ExternalEntityType.subscriber)
                        .filter(ExternalReference.external_id == erpnext_customer)
                        .first()
                    )
                    if cust_ref:
                        subscriber_id = cust_ref.entity_id

                if is_new:
                    project = Project(
                        name=data["name"],
                        description=data.get("description"),
                        status=data.get("status"),
                        priority=data.get("priority"),
                        start_at=data.get("start_at"),
                        due_at=data.get("due_at"),
                        subscriber_id=subscriber_id,
                        is_active=data.get("is_active", True),
                    )
                    db.add(project)
                    db.flush()

                    ref.entity_id = project.id
                    db.add(ref)

                    self._project_cache[external_id] = project.id
                    stats.created += 1
                else:
                    project = db.get(Project, ref.entity_id)
                    if project:
                        project.name = data["name"]
                        project.description = data.get("description") or project.description
                        project.status = data.get("status")
                        project.priority = data.get("priority")
                        self._project_cache[external_id] = project.id
                        stats.updated += 1

                db.commit()

            except Exception as e:
                db.rollback()
                stats.errors += 1
                stats.error_messages.append(f"Project {doc.get('name')}: {e}")
                logger.warning("erpnext_import_project_error doc=%s error=%s", doc.get("name"), e)

        return stats

    def _import_tasks(self, db: "Session") -> ImportStats:
        """Import ERPNext Tasks as ProjectTasks."""
        from app.models.projects import ProjectTask
        from app.models.external import ExternalEntityType

        stats = ImportStats()

        for doc in self.client.get_all("Task", fields=TASK_FIELDS):
            try:
                external_id = doc.get("name")
                if not external_id:
                    continue

                # Tasks require a project
                erpnext_project = doc.get("project")
                if not erpnext_project:
                    stats.skipped += 1
                    continue

                # Look up project
                project_id = self._project_cache.get(erpnext_project)
                if not project_id:
                    from app.models.external import ExternalReference
                    proj_ref = (
                        db.query(ExternalReference)
                        .filter(ExternalReference.connector_config_id == self.connector_config_id)
                        .filter(ExternalReference.entity_type == ExternalEntityType.project)
                        .filter(ExternalReference.external_id == erpnext_project)
                        .first()
                    )
                    if proj_ref:
                        project_id = proj_ref.entity_id
                        self._project_cache[erpnext_project] = project_id

                if not project_id:
                    stats.skipped += 1
                    continue

                ref, is_new = self._get_or_create_external_ref(
                    db, ExternalEntityType.project_task, external_id
                )

                data = map_task(doc)

                if is_new:
                    task = ProjectTask(
                        project_id=project_id,
                        title=data["name"],  # ERPNext 'name' maps to 'title'
                        description=data.get("description"),
                        status=data.get("status"),
                        priority=data.get("priority"),
                        due_at=data.get("due_date"),  # ERPNext 'due_date' maps to 'due_at'
                        is_active=data.get("is_active", True),
                    )
                    db.add(task)
                    db.flush()

                    ref.entity_id = task.id
                    db.add(ref)

                    stats.created += 1
                else:
                    task = db.get(ProjectTask, ref.entity_id)
                    if task:
                        task.title = data["name"]
                        task.description = data.get("description") or task.description
                        task.status = data.get("status")
                        stats.updated += 1

                db.commit()

            except Exception as e:
                db.rollback()
                stats.errors += 1
                stats.error_messages.append(f"Task {doc.get('name')}: {e}")
                logger.warning("erpnext_import_task_error doc=%s error=%s", doc.get("name"), e)

        return stats

    def _import_tickets(self, db: "Session") -> ImportStats:
        """Import ERPNext HD Tickets."""
        from app.models.tickets import Ticket
        from app.models.external import ExternalEntityType

        stats = ImportStats()

        for doc in self.client.get_all("HD Ticket", fields=HD_TICKET_FIELDS):
            try:
                external_id = doc.get("name")
                if not external_id:
                    continue
                # ERPNext HD Ticket IDs are integers, convert to string
                external_id = str(external_id)

                ref, is_new = self._get_or_create_external_ref(
                    db, ExternalEntityType.ticket, external_id
                )

                data = map_hd_ticket(doc)

                # Resolve customer to subscriber
                subscriber_id = None
                erpnext_customer = data.get("_erpnext_customer")
                if erpnext_customer:
                    from app.models.external import ExternalReference
                    cust_ref = (
                        db.query(ExternalReference)
                        .filter(ExternalReference.connector_config_id == self.connector_config_id)
                        .filter(ExternalReference.entity_type == ExternalEntityType.subscriber)
                        .filter(ExternalReference.external_id == erpnext_customer)
                        .first()
                    )
                    if cust_ref:
                        subscriber_id = cust_ref.entity_id

                if is_new:
                    ticket = Ticket(
                        title=data["title"],
                        description=data.get("description"),
                        status=data.get("status"),
                        priority=data.get("priority"),
                        channel=data.get("channel"),
                        tags=data.get("tags"),
                        subscriber_id=subscriber_id,
                        is_active=data.get("is_active", True),
                    )
                    db.add(ticket)
                    db.flush()

                    ref.entity_id = ticket.id
                    ref.metadata_ = {
                        "erpnext_raised_by": data.get("_erpnext_raised_by"),
                    }
                    db.add(ref)

                    stats.created += 1
                else:
                    ticket = db.get(Ticket, ref.entity_id)
                    if ticket:
                        ticket.title = data["title"]
                        ticket.description = data.get("description") or ticket.description
                        ticket.status = data.get("status")
                        ticket.priority = data.get("priority")
                        stats.updated += 1

                db.commit()

            except Exception as e:
                db.rollback()
                stats.errors += 1
                stats.error_messages.append(f"HD Ticket {doc.get('name')}: {e}")
                logger.warning("erpnext_import_ticket_error doc=%s error=%s", doc.get("name"), e)

        return stats

    def _import_leads(self, db: "Session") -> ImportStats:
        """Import ERPNext Leads as CRM Leads."""
        from app.models.crm.sales import Lead
        from app.models.person import Person
        from app.models.external import ExternalEntityType

        stats = ImportStats()

        for doc in self.client.get_all("Lead", fields=LEAD_FIELDS):
            try:
                external_id = doc.get("name")
                if not external_id:
                    continue

                ref, is_new = self._get_or_create_external_ref(
                    db, ExternalEntityType.lead, external_id
                )

                data = map_lead(doc)

                # Create or find person for lead contact
                person_id = None
                contact_email = data.get("_contact_email")

                # Skip leads without email - can't create required Person
                if not contact_email:
                    stats.skipped += 1
                    continue

                # Strip whitespace from email for consistent lookup
                contact_email = contact_email.strip()

                if contact_email:
                    person = db.query(Person).filter(Person.email == contact_email).first()
                    if not person:
                        # Parse name and truncate to 80 chars (VARCHAR limit)
                        contact_name = data.get("_contact_name", "")
                        name_parts = contact_name.split(" ", 1) if contact_name else ["Unknown", ""]
                        first_name = (name_parts[0] or "Unknown")[:80]
                        last_name = (name_parts[1] if len(name_parts) > 1 else "")[:80]

                        person = Person(
                            first_name=first_name,
                            last_name=last_name,
                            email=contact_email,  # Already stripped above
                            phone=data.get("_contact_phone"),
                            is_active=True,
                        )
                        db.add(person)
                        db.flush()
                    person_id = person.id

                if is_new:
                    lead = Lead(
                        title=data["title"],
                        status=data.get("status"),
                        notes=data.get("notes"),
                        person_id=person_id,
                    )
                    db.add(lead)
                    db.flush()

                    ref.entity_id = lead.id
                    ref.metadata_ = {
                        "erpnext_source": data.get("source") or data.get("_erpnext_source"),
                        "erpnext_territory": data.get("_erpnext_territory"),
                    }
                    db.add(ref)

                    stats.created += 1
                else:
                    lead = db.get(Lead, ref.entity_id)
                    if lead:
                        lead.title = data["title"]
                        lead.status = data.get("status")
                        lead.notes = data.get("notes") or lead.notes
                        stats.updated += 1

                db.commit()

            except Exception as e:
                db.rollback()
                stats.errors += 1
                stats.error_messages.append(f"Lead {doc.get('name')}: {e}")
                logger.warning("erpnext_import_lead_error doc=%s error=%s", doc.get("name"), e)

        return stats

    def _import_quotations(self, db: "Session") -> ImportStats:
        """Import ERPNext Quotations as CRM Quotes."""
        from app.models.crm.sales import Quote, CrmQuoteLineItem
        from app.models.crm.enums import QuoteStatus
        from app.models.external import ExternalEntityType, ExternalReference
        from app.models.subscriber import Subscriber
        from decimal import Decimal

        stats = ImportStats()

        for doc in self.client.get_all("Quotation", fields=QUOTATION_FIELDS):
            try:
                external_id = doc.get("name")
                if not external_id:
                    continue

                ref, is_new = self._get_or_create_external_ref(
                    db, ExternalEntityType.quote, external_id
                )

                # Fetch full doc with items
                full_doc = self.client.get_doc("Quotation", external_id)
                data = map_quotation(full_doc)

                # Resolve party to person_id
                person_id = None
                party_name = data.get("_erpnext_party_name")
                quotation_to = data.get("_erpnext_quotation_to")

                if party_name:
                    if quotation_to == "Customer":
                        # Look up customer -> subscriber -> organization -> person
                        cust_ref = (
                            db.query(ExternalReference)
                            .filter(ExternalReference.connector_config_id == self.connector_config_id)
                            .filter(ExternalReference.entity_type == ExternalEntityType.subscriber)
                            .filter(ExternalReference.external_id == party_name)
                            .first()
                        )
                        if cust_ref:
                            subscriber = db.get(Subscriber, cust_ref.entity_id)
                            if subscriber and subscriber.person_id:
                                person_id = subscriber.person_id
                    elif quotation_to == "Lead":
                        # Look up lead -> person
                        lead_ref = (
                            db.query(ExternalReference)
                            .filter(ExternalReference.connector_config_id == self.connector_config_id)
                            .filter(ExternalReference.entity_type == ExternalEntityType.lead)
                            .filter(ExternalReference.external_id == party_name)
                            .first()
                        )
                        if lead_ref:
                            from app.models.crm.sales import Lead
                            lead = db.get(Lead, lead_ref.entity_id)
                            if lead:
                                person_id = lead.person_id

                # Skip quotes without a person_id (required field)
                if not person_id:
                    stats.skipped += 1
                    continue

                # Map status string to enum
                status_str = data.get("status", "draft")
                try:
                    status = QuoteStatus(status_str)
                except ValueError:
                    status = QuoteStatus.draft

                if is_new:
                    quote = Quote(
                        person_id=person_id,
                        status=status,
                        expires_at=data.get("valid_until"),
                        subtotal=data.get("subtotal", Decimal("0.00")),
                        total=data.get("total", Decimal("0.00")),
                        currency=data.get("currency", "NGN"),
                        notes=data.get("terms"),
                        is_active=data.get("is_active", True),
                    )
                    db.add(quote)
                    db.flush()

                    # Add line items
                    for item_data in data.get("_items", []):
                        line_item = CrmQuoteLineItem(
                            quote_id=quote.id,
                            description=item_data.get("description") or "Item",
                            quantity=Decimal(str(item_data.get("quantity", 1))),
                            unit_price=item_data.get("unit_price", Decimal("0.00")),
                            amount=item_data.get("amount", Decimal("0.00")),
                        )
                        db.add(line_item)

                    ref.entity_id = quote.id
                    ref.metadata_ = {
                        "erpnext_quote_number": data.get("quote_number"),
                        "erpnext_party_name": party_name,
                        "erpnext_quotation_to": quotation_to,
                    }
                    db.add(ref)

                    stats.created += 1
                else:
                    quote = db.get(Quote, ref.entity_id)
                    if quote:
                        quote.status = status
                        quote.total = data.get("total", quote.total)
                        stats.updated += 1

                db.commit()

            except Exception as e:
                db.rollback()
                stats.errors += 1
                stats.error_messages.append(f"Quotation {doc.get('name')}: {e}")
                logger.warning("erpnext_import_quotation_error doc=%s error=%s", doc.get("name"), e)

        return stats
