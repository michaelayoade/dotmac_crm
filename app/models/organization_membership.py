import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class OrganizationMembershipRole(enum.Enum):
    owner = "owner"
    admin = "admin"
    member = "member"


class OrganizationMembership(Base):
    """
    Explicit access link between a Person and an Organization.

    People have a single primary Organization via people.organization_id. This
    table enables one Person (one email/login) to manage multiple Organizations
    (e.g. a reseller managing many child customer orgs).
    """

    __tablename__ = "organization_memberships"
    __table_args__ = (UniqueConstraint("organization_id", "person_id", name="uq_organization_memberships_org_person"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    role: Mapped[OrganizationMembershipRole] = mapped_column(
        Enum(OrganizationMembershipRole, name="organizationmembershiprole"),
        default=OrganizationMembershipRole.member,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    organization = relationship("Organization")
    person = relationship("Person")
