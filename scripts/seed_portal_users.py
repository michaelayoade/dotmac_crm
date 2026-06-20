"""Seed Vendor and Reseller portal login users into the target DB (crm_test QA).

Idempotent: detects existing credential/username and skips re-creation.

Run (against crm_test):
    docker cp scripts/seed_portal_users.py dotmac_omni_app:/app/scripts/
    docker exec -e DATABASE_URL=<crm_test> -e PYTHONPATH=/app -w /app \
        dotmac_omni_app python scripts/seed_portal_users.py
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.db import SessionLocal
from app.models.auth import AuthProvider, UserCredential
from app.models.person import Person
from app.models.vendor import Vendor, VendorUser
from app.services.auth_flow import hash_password

VENDOR_EMAIL = "vendoruser@test.local"
VENDOR_PASSWORD = "VendorQA!2026Secure#Pass"
RESELLER_EMAIL = "reselleruser@test.local"
RESELLER_PASSWORD = "ResellerQA!2026Secure#Pass"


def seed_vendor() -> str:
    db = SessionLocal()
    try:
        existing = db.query(UserCredential).filter(UserCredential.username == VENDOR_EMAIL).first()
        if existing:
            return "vendor: already present, skipped"

        person = db.query(Person).filter(Person.email == VENDOR_EMAIL).first()
        if not person:
            person = Person(
                first_name="Vera",
                last_name="Vendor",
                display_name="Vera Vendor",
                email=VENDOR_EMAIL,
                phone="+15550009001",
            )
            db.add(person)
            db.flush()

        vendor = db.query(Vendor).filter(Vendor.name == "QA Fiber Contractors").first()
        if not vendor:
            vendor = Vendor(name="QA Fiber Contractors", contact_email=VENDOR_EMAIL, is_active=True)
            db.add(vendor)
            db.flush()

        link = db.query(VendorUser).filter(VendorUser.vendor_id == vendor.id, VendorUser.person_id == person.id).first()
        if not link:
            db.add(VendorUser(vendor_id=vendor.id, person_id=person.id, role="admin", is_active=True))

        db.add(
            UserCredential(
                person_id=person.id,
                provider=AuthProvider.local,
                username=VENDOR_EMAIL,
                password_hash=hash_password(VENDOR_PASSWORD),
                password_updated_at=datetime.now(UTC),
                is_active=True,
            )
        )
        db.commit()
        return f"vendor: created {VENDOR_EMAIL} / vendor='QA Fiber Contractors'"
    except Exception as exc:
        db.rollback()
        return f"vendor: FAILED {type(exc).__name__}: {exc}"
    finally:
        db.close()


def seed_reseller() -> str:
    db = SessionLocal()
    try:
        existing = db.query(UserCredential).filter(UserCredential.username == RESELLER_EMAIL).first()
        if existing:
            return "reseller: already present, skipped"
        from app.services.reseller import admin_create_reseller

        org, _person = admin_create_reseller(
            db,
            organization_name="QA Reseller Networks",
            organization_domain="qa-reseller.test",
            user_first_name="Rita",
            user_last_name="Reseller",
            user_email=RESELLER_EMAIL,
            user_phone="+15550009002",
            password=RESELLER_PASSWORD,
        )
        return f"reseller: created {RESELLER_EMAIL} / org='{org.name}'"
    except Exception as exc:
        db.rollback()
        return f"reseller: FAILED {type(exc).__name__}: {exc}"
    finally:
        db.close()


def main() -> None:
    print(seed_vendor())
    print(seed_reseller())


if __name__ == "__main__":
    main()
