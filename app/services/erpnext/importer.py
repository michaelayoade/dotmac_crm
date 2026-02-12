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

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from app.logging import get_logger
from app.models.external import ExternalEntityType
from app.services.erpnext.client import ERPNextClient, ERPNextError
from app.services.erpnext.mappers import (
    CONTACT_FIELDS,
    LEAD_FIELDS,
    QUOTATION_FIELDS,
    map_communication,
    map_contact,
    map_customer,
    map_hd_ticket,
    map_hd_ticket_comment,
    map_lead,
    map_project,
    map_project_comment,
    map_quotation,
    map_task,
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
    ticket_comments: ImportStats = field(default_factory=ImportStats)
    project_comments: ImportStats = field(default_factory=ImportStats)
    task_comments: ImportStats = field(default_factory=ImportStats)

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
            "ticket_comments": self.ticket_comments.to_dict(),
            "project_comments": self.project_comments.to_dict(),
            "task_comments": self.task_comments.to_dict(),
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
        self._org_cache: dict[str, UUID] = {}  # erpnext_name -> org_id
        self._project_cache: dict[str, UUID] = {}  # erpnext_name -> project_id

    def test_connection(self) -> bool:
        """Test ERPNext API connection."""
        return self.client.test_connection()

    def import_all(self, db: Session) -> ImportResult:
        """Import all doctypes from ERPNext.

        Order matters for foreign key relationships:
        1. Contacts (creates Persons)
        2. Customers (creates Organizations, references Contacts)
        3. Projects (references Customers) + project comments
        4. Tasks (references Projects) + task comments
        5. Tickets (references Customers, Contacts) + ticket comments/comms
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

            # 3. Import Projects (with comments from child tables)
            logger.info("erpnext_import_starting doctype=Project")
            result.projects, result.project_comments = self._import_projects(db)

            # 4. Import Tasks (with comments from child tables)
            logger.info("erpnext_import_starting doctype=Task")
            result.tasks, result.task_comments = self._import_tasks(db)

            # 5. Import HD Tickets (with comments and communications)
            logger.info("erpnext_import_starting doctype=HD Ticket")
            result.tickets, result.ticket_comments = self._import_tickets(db)

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
        db: Session,
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

    def _resolve_subscriber_id(self, db: Session, erpnext_customer: str) -> UUID | None:
        """Resolve an ERPNext customer name to a Subscriber ID.

        Tries ExternalReference first (connector-scoped), then falls back to
        Subscriber.external_id or Organization.erpnext_id for cross-connector dedup.
        """
        from app.models.external import ExternalReference
        from app.models.subscriber import Organization, Subscriber

        # 1. Try ExternalReference (current connector scope)
        cust_ref = (
            db.query(ExternalReference)
            .filter(ExternalReference.connector_config_id == self.connector_config_id)
            .filter(ExternalReference.entity_type == ExternalEntityType.subscriber)
            .filter(ExternalReference.external_id == erpnext_customer)
            .first()
        )
        if cust_ref:
            return cust_ref.entity_id

        # 2. Try any ExternalReference (cross-connector fallback)
        any_ref = (
            db.query(ExternalReference)
            .filter(ExternalReference.entity_type == ExternalEntityType.subscriber)
            .filter(ExternalReference.external_id == erpnext_customer)
            .first()
        )
        if any_ref:
            return any_ref.entity_id

        # 3. Try Subscriber.external_id
        sub = db.query(Subscriber).filter(Subscriber.external_id == erpnext_customer).first()
        if sub:
            return sub.id

        # 4. Try Organization.erpnext_id -> first subscriber
        org = db.query(Organization).filter(Organization.erpnext_id == erpnext_customer).first()
        if org:
            sub = db.query(Subscriber).filter(Subscriber.organization_id == org.id).first()
            if sub:
                return sub.id

        return None

    # ─────────────────────────────────────────────────────────────────
    # Contacts
    # ─────────────────────────────────────────────────────────────────

    def _import_contacts(self, db: Session) -> ImportStats:
        """Import ERPNext Contacts as Person records."""
        from app.models.external import ExternalEntityType
        from app.models.person import Person

        stats = ImportStats()

        for doc in self.client.get_all("Contact", fields=CONTACT_FIELDS):
            try:
                external_id = doc.get("name")
                if not external_id:
                    continue

                # Check if already imported
                ref, is_new = self._get_or_create_external_ref(db, ExternalEntityType.person, external_id)

                # Map ERPNext doc to Person data
                data = map_contact(doc)
                email = data.get("email")
                person: Person | None = None
                if is_new:
                    # Dedup by erpnext_id first, then email fallback
                    existing = db.query(Person).filter(Person.erpnext_id == external_id).first()
                    if not existing:
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

                        # Link this Contact to existing person and set erpnext_id
                        if not existing.erpnext_id:
                            existing.erpnext_id = external_id
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
                        erpnext_id=external_id,
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
                        if not person.erpnext_id:
                            person.erpnext_id = external_id
                        self._person_cache[external_id] = person.id
                        stats.updated += 1

                db.commit()

            except Exception as e:
                db.rollback()
                stats.errors += 1
                stats.error_messages.append(f"Contact {doc.get('name')}: {e}")
                logger.warning("erpnext_import_contact_error doc=%s error=%s", doc.get("name"), e)

        return stats

    # ─────────────────────────────────────────────────────────────────
    # Customers
    # ─────────────────────────────────────────────────────────────────

    def _import_customers(self, db: Session) -> ImportStats:
        """Import ERPNext Customers as Organization + Subscriber records.

        Uses get_doc per customer to access custom fields (e.g. Splynx ID).
        """
        from app.models.external import ExternalEntityType
        from app.models.subscriber import Organization, Subscriber

        stats = ImportStats()

        # Phase 1: get lightweight list of names
        for stub in self.client.get_all("Customer", fields=["name"]):
            try:
                external_id = stub.get("name")
                if not external_id:
                    continue

                # Phase 2: get full doc with custom fields
                doc = self.client.get_doc("Customer", external_id)

                # Check if already imported
                ref, is_new = self._get_or_create_external_ref(db, ExternalEntityType.subscriber, external_id)

                data = map_customer(doc)
                subscriber: Subscriber | None = None

                if is_new:
                    # Dedup: check if Organization already exists by erpnext_id or name
                    existing_org = db.query(Organization).filter(Organization.erpnext_id == external_id).first()
                    if not existing_org:
                        existing_org = db.query(Organization).filter(Organization.name == data["name"]).first()

                    if existing_org:
                        # Found existing org — link it and update
                        if not existing_org.erpnext_id:
                            existing_org.erpnext_id = external_id
                        existing_org.legal_name = data.get("legal_name") or existing_org.legal_name
                        existing_org.tax_id = data.get("tax_id") or existing_org.tax_id
                        self._org_cache[external_id] = existing_org.id

                        # Find existing subscriber for this org
                        existing_sub = (
                            db.query(Subscriber).filter(Subscriber.organization_id == existing_org.id).first()
                        )
                        if existing_sub:
                            # Update Splynx ID if available
                            splynx_id = data.get("_erpnext_splynx_id")
                            if splynx_id and not (existing_sub.sync_metadata or {}).get("erpnext_splynx_id"):
                                if not existing_sub.sync_metadata:
                                    existing_sub.sync_metadata = {}
                                existing_sub.sync_metadata["erpnext_splynx_id"] = str(splynx_id)

                            ref.entity_id = existing_sub.id
                            db.add(ref)
                            stats.updated += 1
                            db.commit()
                            continue

                        # Org exists but no subscriber — fall through to create subscriber below
                        org = existing_org
                    else:
                        # Create new Organization with erpnext_id
                        org = Organization(
                            name=data["name"],
                            legal_name=data.get("legal_name"),
                            tax_id=data.get("tax_id"),
                            website=data.get("website"),
                            notes=data.get("notes"),
                            erpnext_id=external_id,
                        )
                        db.add(org)
                        db.flush()

                    self._org_cache[external_id] = org.id

                    # Generate unique subscriber_number (max 60 chars)
                    # If name > 50 chars, truncate and add hash suffix for uniqueness
                    subscriber_number: str | None
                    if external_id and len(external_id) > 50:
                        import hashlib

                        hash_suffix = hashlib.md5(
                            external_id.encode(),
                            usedforsecurity=False,
                        ).hexdigest()[:8]
                        subscriber_number = f"{external_id[:50]}-{hash_suffix}"
                    else:
                        subscriber_number = external_id[:60] if external_id else None

                    # Check if subscriber already exists with this number
                    existing_sub = (
                        db.query(Subscriber).filter(Subscriber.subscriber_number == subscriber_number).first()
                    )
                    if existing_sub:
                        # Link to existing subscriber
                        splynx_id = data.get("_erpnext_splynx_id")
                        if splynx_id and not (existing_sub.sync_metadata or {}).get("erpnext_splynx_id"):
                            if not existing_sub.sync_metadata:
                                existing_sub.sync_metadata = {}
                            existing_sub.sync_metadata["erpnext_splynx_id"] = str(splynx_id)

                        ref.entity_id = existing_sub.id
                        db.add(ref)
                        stats.updated += 1
                        db.commit()
                        continue

                    # Create Subscriber linked to Organization
                    subscriber = Subscriber(
                        organization_id=org.id,
                        external_id=external_id[:200] if external_id else None,
                        external_system="erpnext",
                        subscriber_number=subscriber_number,
                        is_active=True,
                    )

                    # Capture Splynx ID from ERPNext custom field
                    splynx_id = data.get("_erpnext_splynx_id")
                    if splynx_id:
                        subscriber.sync_metadata = {"erpnext_splynx_id": str(splynx_id)}
                        logger.info(
                            "erpnext_customer_splynx_link customer=%s splynx_id=%s",
                            external_id,
                            splynx_id,
                        )

                    db.add(subscriber)
                    db.flush()

                    ref.entity_id = subscriber.id
                    ref.metadata_ = {
                        "organization_id": str(org.id),
                        "erpnext_customer_type": data.get("_erpnext_customer_type"),
                        "erpnext_customer_group": data.get("_erpnext_customer_group"),
                        "erpnext_splynx_id": str(splynx_id) if splynx_id else None,
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
                        if not org.erpnext_id:
                            org.erpnext_id = external_id
                        self._org_cache[external_id] = org.id

                        # Update Splynx ID if newly available
                        splynx_id = data.get("_erpnext_splynx_id")
                        if splynx_id and not (subscriber.sync_metadata or {}).get("erpnext_splynx_id"):
                            if not subscriber.sync_metadata:
                                subscriber.sync_metadata = {}
                            subscriber.sync_metadata["erpnext_splynx_id"] = str(splynx_id)

                        stats.updated += 1

                db.commit()

            except Exception as e:
                db.rollback()
                stats.errors += 1
                stats.error_messages.append(f"Customer {stub.get('name')}: {e}")
                logger.warning("erpnext_import_customer_error doc=%s error=%s", stub.get("name"), e)

        return stats

    # ─────────────────────────────────────────────────────────────────
    # Projects (with comments)
    # ─────────────────────────────────────────────────────────────────

    def _import_projects(self, db: Session) -> tuple[ImportStats, ImportStats]:
        """Import ERPNext Projects. Returns (project_stats, comment_stats)."""
        from sqlalchemy import or_

        from app.models.domain_settings import SettingDomain
        from app.models.external import ExternalEntityType
        from app.models.projects import Project, ProjectPriority, ProjectStatus
        from app.services.numbering import generate_number

        stats = ImportStats()
        comment_stats = ImportStats()

        # Phase 1: lightweight name list
        for stub in self.client.get_all("Project", fields=["name"]):
            try:
                external_id = stub.get("name")
                if not external_id:
                    continue

                # Phase 2: full doc with child tables (comments)
                doc = self.client.get_doc("Project", external_id)

                ref, is_new = self._get_or_create_external_ref(db, ExternalEntityType.project, external_id)

                data = map_project(doc)
                project_status = cast(ProjectStatus, data.get("status"))
                project_priority = cast(ProjectPriority, data.get("priority"))
                project: Project | None = None

                # Resolve customer to subscriber
                subscriber_id = None
                erpnext_customer = data.get("_erpnext_customer")
                if erpnext_customer:
                    subscriber_id = self._resolve_subscriber_id(db, erpnext_customer)

                if is_new:
                    # Fallback match: if this ERP project ID already exists as
                    # our project number/code, link instead of creating a duplicate.
                    candidates = {external_id}
                    m = re.match(r"^(PROJ-)(\d+)$", external_id, flags=re.IGNORECASE)
                    if m:
                        prefix = "PROJ-"
                        numeric = int(m.group(2))
                        candidates.add(f"{prefix}{numeric}")

                    existing_project = (
                        db.query(Project)
                        .filter(or_(Project.number.in_(candidates), Project.code.in_(candidates)))
                        .first()
                    )
                    if existing_project:
                        existing_project.name = data["name"]
                        existing_project.description = data.get("description") or existing_project.description
                        existing_project.status = project_status
                        existing_project.priority = project_priority
                        if not existing_project.erpnext_id:
                            existing_project.erpnext_id = external_id
                        if subscriber_id and not existing_project.subscriber_id:
                            existing_project.subscriber_id = subscriber_id
                        ref.entity_id = existing_project.id
                        db.add(ref)
                        self._project_cache[external_id] = existing_project.id
                        stats.updated += 1
                        # Import comments for existing project
                        self._import_project_comments(db, doc, existing_project.id, comment_stats)
                        db.commit()
                        continue

                    # Generate DotMac number
                    number = generate_number(
                        db=db,
                        domain=SettingDomain.numbering,
                        sequence_key="project_number",
                        enabled_key="project_number_enabled",
                        prefix_key="project_number_prefix",
                        padding_key="project_number_padding",
                        start_key="project_number_start",
                    )

                    project = Project(
                        name=data["name"],
                        description=data.get("description"),
                        status=project_status,
                        priority=project_priority,
                        start_at=data.get("start_at"),
                        due_at=data.get("due_at"),
                        subscriber_id=subscriber_id,
                        is_active=data.get("is_active", True),
                        number=number,
                        erpnext_id=external_id,
                    )
                    db.add(project)
                    db.flush()

                    ref.entity_id = project.id
                    db.add(ref)

                    self._project_cache[external_id] = project.id
                    stats.created += 1

                    # Import comments from child table
                    self._import_project_comments(db, doc, project.id, comment_stats)
                else:
                    project = db.get(Project, ref.entity_id)
                    if project:
                        project.name = data["name"]
                        project.description = data.get("description") or project.description
                        project.status = project_status
                        project.priority = project_priority
                        if not project.erpnext_id:
                            project.erpnext_id = external_id
                        self._project_cache[external_id] = project.id
                        stats.updated += 1

                        # Import any new comments
                        self._import_project_comments(db, doc, project.id, comment_stats)

                db.commit()

            except Exception as e:
                db.rollback()
                stats.errors += 1
                stats.error_messages.append(f"Project {stub.get('name')}: {e}")
                logger.warning("erpnext_import_project_error doc=%s error=%s", stub.get("name"), e)

        return stats, comment_stats

    def _import_project_comments(
        self,
        db: Session,
        doc: dict[str, Any],
        project_id: UUID,
        stats: ImportStats,
    ) -> None:
        """Import comments from an ERPNext Project child table."""
        from app.models.projects import ProjectComment

        for comment_doc in doc.get("comments", []):
            try:
                comment_name = comment_doc.get("name")
                if not comment_name:
                    continue

                ref, is_new = self._get_or_create_external_ref(db, ExternalEntityType.project_comment, comment_name)
                if not is_new:
                    stats.skipped += 1
                    continue

                data = map_project_comment(comment_doc)
                body = data.get("body") or ""
                if not body.strip():
                    stats.skipped += 1
                    continue

                comment = ProjectComment(
                    project_id=project_id,
                    body=body,
                )
                db.add(comment)
                db.flush()

                ref.entity_id = comment.id
                db.add(ref)
                stats.created += 1

            except Exception as e:
                stats.errors += 1
                stats.error_messages.append(f"Project comment {comment_doc.get('name')}: {e}")
                logger.warning(
                    "erpnext_import_project_comment_error doc=%s error=%s",
                    comment_doc.get("name"),
                    e,
                )

    # ─────────────────────────────────────────────────────────────────
    # Tasks (with comments)
    # ─────────────────────────────────────────────────────────────────

    def _import_tasks(self, db: Session) -> tuple[ImportStats, ImportStats]:
        """Import ERPNext Tasks as ProjectTasks. Returns (task_stats, comment_stats)."""
        from app.models.domain_settings import SettingDomain
        from app.models.external import ExternalEntityType
        from app.models.projects import ProjectTask, TaskPriority, TaskStatus
        from app.services.numbering import generate_number

        stats = ImportStats()
        comment_stats = ImportStats()

        # Phase 1: lightweight name + project list
        for stub in self.client.get_all("Task", fields=["name", "project"]):
            try:
                external_id = stub.get("name")
                if not external_id:
                    continue

                # Tasks require a project
                erpnext_project = stub.get("project")
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

                # Phase 2: full doc with child tables
                doc = self.client.get_doc("Task", external_id)

                ref, is_new = self._get_or_create_external_ref(db, ExternalEntityType.project_task, external_id)

                data = map_task(doc)
                task_status = cast(TaskStatus, data.get("status"))
                task_priority = cast(TaskPriority, data.get("priority"))
                task: ProjectTask | None = None

                if is_new:
                    number = generate_number(
                        db=db,
                        domain=SettingDomain.numbering,
                        sequence_key="project_task_number",
                        enabled_key="project_task_number_enabled",
                        prefix_key="project_task_number_prefix",
                        padding_key="project_task_number_padding",
                        start_key="project_task_number_start",
                    )
                    task = ProjectTask(
                        project_id=project_id,
                        title=data["name"],  # ERPNext 'name' maps to 'title'
                        description=data.get("description"),
                        status=task_status,
                        priority=task_priority,
                        due_at=data.get("due_date"),  # ERPNext 'due_date' maps to 'due_at'
                        is_active=data.get("is_active", True),
                        number=number,
                        erpnext_id=external_id,
                    )
                    db.add(task)
                    db.flush()

                    ref.entity_id = task.id
                    db.add(ref)

                    stats.created += 1

                    # Import task comments
                    self._import_task_comments(db, doc, task.id, comment_stats)
                else:
                    task = db.get(ProjectTask, ref.entity_id)
                    if task:
                        task.title = data["name"]
                        task.description = data.get("description") or task.description
                        task.status = task_status
                        task.priority = task_priority
                        if not task.erpnext_id:
                            task.erpnext_id = external_id
                        stats.updated += 1

                        # Import any new comments
                        self._import_task_comments(db, doc, task.id, comment_stats)

                db.commit()

            except Exception as e:
                db.rollback()
                stats.errors += 1
                stats.error_messages.append(f"Task {stub.get('name')}: {e}")
                logger.warning("erpnext_import_task_error doc=%s error=%s", stub.get("name"), e)

        return stats, comment_stats

    def _import_task_comments(
        self,
        db: Session,
        doc: dict[str, Any],
        task_id: UUID,
        stats: ImportStats,
    ) -> None:
        """Import comments from an ERPNext Task child table."""
        from app.models.projects import ProjectTaskComment

        for comment_doc in doc.get("comments", []):
            try:
                comment_name = comment_doc.get("name")
                if not comment_name:
                    continue

                ref, is_new = self._get_or_create_external_ref(
                    db, ExternalEntityType.project_task_comment, comment_name
                )
                if not is_new:
                    stats.skipped += 1
                    continue

                data = map_project_comment(comment_doc)
                body = data.get("body") or ""
                if not body.strip():
                    stats.skipped += 1
                    continue

                comment = ProjectTaskComment(
                    task_id=task_id,
                    body=body,
                )
                db.add(comment)
                db.flush()

                ref.entity_id = comment.id
                db.add(ref)
                stats.created += 1

            except Exception as e:
                stats.errors += 1
                stats.error_messages.append(f"Task comment {comment_doc.get('name')}: {e}")
                logger.warning(
                    "erpnext_import_task_comment_error doc=%s error=%s",
                    comment_doc.get("name"),
                    e,
                )

    # ─────────────────────────────────────────────────────────────────
    # Tickets (with comments and communications)
    # ─────────────────────────────────────────────────────────────────

    def _import_tickets(self, db: Session) -> tuple[ImportStats, ImportStats]:
        """Import ERPNext HD Tickets. Returns (ticket_stats, comment_stats)."""
        from app.models.domain_settings import SettingDomain
        from app.models.external import ExternalEntityType
        from app.models.tickets import Ticket, TicketChannel, TicketPriority, TicketStatus
        from app.services.numbering import generate_number

        stats = ImportStats()
        comment_stats = ImportStats()

        # Phase 1: lightweight name list
        for stub in self.client.get_all("HD Ticket", fields=["name"]):
            try:
                external_id = stub.get("name")
                if not external_id:
                    continue
                # ERPNext HD Ticket IDs are integers, convert to string
                external_id = str(external_id)

                # Phase 2: full doc with child tables (comments)
                doc = self.client.get_doc("HD Ticket", external_id)

                ref, is_new = self._get_or_create_external_ref(db, ExternalEntityType.ticket, external_id)

                data = map_hd_ticket(doc)
                ticket_status = cast(TicketStatus, data.get("status"))
                ticket_priority = cast(TicketPriority, data.get("priority"))
                ticket_channel = cast(TicketChannel, data.get("channel"))
                ticket: Ticket | None = None

                # Resolve customer to subscriber
                subscriber_id = None
                erpnext_customer = data.get("_erpnext_customer")
                if erpnext_customer:
                    subscriber_id = self._resolve_subscriber_id(db, erpnext_customer)

                if is_new:
                    # Generate DotMac number
                    number = generate_number(
                        db=db,
                        domain=SettingDomain.numbering,
                        sequence_key="ticket_number",
                        enabled_key="ticket_number_enabled",
                        prefix_key="ticket_number_prefix",
                        padding_key="ticket_number_padding",
                        start_key="ticket_number_start",
                    )

                    ticket = Ticket(
                        title=data["title"],
                        description=data.get("description"),
                        status=ticket_status,
                        priority=ticket_priority,
                        channel=ticket_channel,
                        tags=data.get("tags"),
                        subscriber_id=subscriber_id,
                        is_active=data.get("is_active", True),
                        number=number,
                        erpnext_id=external_id,
                    )
                    db.add(ticket)
                    db.flush()

                    ref.entity_id = ticket.id
                    ref.metadata_ = {
                        "erpnext_raised_by": data.get("_erpnext_raised_by"),
                    }
                    db.add(ref)

                    stats.created += 1

                    # Import comments from child table + communications
                    self._import_ticket_comments(db, doc, ticket.id, comment_stats)
                    self._import_ticket_communications(db, external_id, ticket.id, comment_stats)
                else:
                    ticket = db.get(Ticket, ref.entity_id)
                    if ticket:
                        ticket.title = data["title"]
                        ticket.description = data.get("description") or ticket.description
                        ticket.status = ticket_status
                        ticket.priority = ticket_priority
                        if not ticket.erpnext_id:
                            ticket.erpnext_id = external_id
                        stats.updated += 1

                        # Import any new comments/communications
                        self._import_ticket_comments(db, doc, ticket.id, comment_stats)
                        self._import_ticket_communications(db, external_id, ticket.id, comment_stats)

                db.commit()

            except Exception as e:
                db.rollback()
                stats.errors += 1
                stats.error_messages.append(f"HD Ticket {stub.get('name')}: {e}")
                logger.warning("erpnext_import_ticket_error doc=%s error=%s", stub.get("name"), e)

        return stats, comment_stats

    def _import_ticket_comments(
        self,
        db: Session,
        doc: dict[str, Any],
        ticket_id: UUID,
        stats: ImportStats,
    ) -> None:
        """Import comments from an ERPNext HD Ticket child table."""
        from app.models.tickets import TicketComment

        for comment_doc in doc.get("comments", []):
            try:
                comment_name = comment_doc.get("name")
                if not comment_name:
                    continue

                ref, is_new = self._get_or_create_external_ref(db, ExternalEntityType.ticket_comment, comment_name)
                if not is_new:
                    stats.skipped += 1
                    continue

                data = map_hd_ticket_comment(comment_doc)
                body = data.get("body") or ""
                if not body.strip():
                    stats.skipped += 1
                    continue

                comment = TicketComment(
                    ticket_id=ticket_id,
                    body=body,
                    is_internal=data.get("is_internal", False),
                )
                db.add(comment)
                db.flush()

                ref.entity_id = comment.id
                db.add(ref)
                stats.created += 1

            except Exception as e:
                stats.errors += 1
                stats.error_messages.append(f"Ticket comment {comment_doc.get('name')}: {e}")
                logger.warning(
                    "erpnext_import_ticket_comment_error doc=%s error=%s",
                    comment_doc.get("name"),
                    e,
                )

    def _import_ticket_communications(
        self,
        db: Session,
        ticket_name: str,
        ticket_id: UUID,
        stats: ImportStats,
    ) -> None:
        """Import Communications (email threads) linked to an HD Ticket."""
        from app.models.tickets import TicketComment

        try:
            comms = self.client.get_all(
                "Communication",
                fields=["name", "subject", "content", "sender", "sent_or_received", "creation"],
                filters={
                    "reference_doctype": "HD Ticket",
                    "reference_name": ticket_name,
                },
            )
        except ERPNextError as e:
            logger.warning(
                "erpnext_import_ticket_comms_fetch_error ticket=%s error=%s",
                ticket_name,
                e,
            )
            return

        for comm_doc in comms:
            try:
                comm_name = comm_doc.get("name")
                if not comm_name:
                    continue

                # Prefix with "comm-" to avoid ID collision with child-table comments
                ref_key = f"comm-{comm_name}"
                ref, is_new = self._get_or_create_external_ref(db, ExternalEntityType.ticket_comment, ref_key)
                if not is_new:
                    stats.skipped += 1
                    continue

                data = map_communication(comm_doc)
                body = data.get("body") or ""
                if not body.strip():
                    stats.skipped += 1
                    continue

                comment = TicketComment(
                    ticket_id=ticket_id,
                    body=body,
                    is_internal=data.get("is_internal", False),
                )
                db.add(comment)
                db.flush()

                ref.entity_id = comment.id
                db.add(ref)
                stats.created += 1

            except Exception as e:
                stats.errors += 1
                stats.error_messages.append(f"Communication {comm_doc.get('name')}: {e}")
                logger.warning(
                    "erpnext_import_communication_error doc=%s error=%s",
                    comm_doc.get("name"),
                    e,
                )

    # ─────────────────────────────────────────────────────────────────
    # Leads
    # ─────────────────────────────────────────────────────────────────

    def _import_leads(self, db: Session) -> ImportStats:
        """Import ERPNext Leads as CRM Leads."""
        from app.models.crm.sales import Lead, LeadStatus
        from app.models.external import ExternalEntityType
        from app.models.person import Person

        stats = ImportStats()

        for doc in self.client.get_all("Lead", fields=LEAD_FIELDS):
            try:
                external_id = doc.get("name")
                if not external_id:
                    continue

                ref, is_new = self._get_or_create_external_ref(db, ExternalEntityType.lead, external_id)

                data = map_lead(doc)
                lead_status = cast(LeadStatus, data.get("status"))
                lead: Lead | None = None

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
                        status=lead_status,
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
                        lead.status = lead_status
                        lead.notes = data.get("notes") or lead.notes
                        stats.updated += 1

                db.commit()

            except Exception as e:
                db.rollback()
                stats.errors += 1
                stats.error_messages.append(f"Lead {doc.get('name')}: {e}")
                logger.warning("erpnext_import_lead_error doc=%s error=%s", doc.get("name"), e)

        return stats

    # ─────────────────────────────────────────────────────────────────
    # Quotations
    # ─────────────────────────────────────────────────────────────────

    def _import_quotations(self, db: Session) -> ImportStats:
        """Import ERPNext Quotations as CRM Quotes."""
        from decimal import Decimal

        from app.models.crm.enums import QuoteStatus
        from app.models.crm.sales import CrmQuoteLineItem, Quote
        from app.models.external import ExternalEntityType, ExternalReference
        from app.models.subscriber import Subscriber

        stats = ImportStats()

        for doc in self.client.get_all("Quotation", fields=QUOTATION_FIELDS):
            try:
                external_id = doc.get("name")
                if not external_id:
                    continue

                ref, is_new = self._get_or_create_external_ref(db, ExternalEntityType.quote, external_id)

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

                quote: Quote | None = None
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
