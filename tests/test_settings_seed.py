from sqlalchemy import select

from app.models.auth import AuthProvider, UserCredential
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.services import settings_seed
from app.services.auth_flow import verify_password


def test_seed_bootstrap_admin_user_repairs_existing_local_credential(db_session, monkeypatch):
    monkeypatch.setattr(settings_seed, "BOOTSTRAP_ADMIN_USERNAME", "codexadmin")
    monkeypatch.setattr(settings_seed, "BOOTSTRAP_ADMIN_EMAIL", "codexadmin@local.invalid")
    monkeypatch.setattr(settings_seed, "BOOTSTRAP_ADMIN_PASSWORD", "TempAdmin!2026")

    person = Person(
        first_name="Legacy",
        last_name="Admin",
        email="codexadmin@local.invalid",
        is_active=True,
        email_verified=True,
    )
    db_session.add(person)
    db_session.flush()

    credential = UserCredential(
        person_id=person.id,
        provider=AuthProvider.local,
        username="old-admin",
        password_hash=settings_seed.hash_password("OldPassword!1"),
        is_active=False,
        must_change_password=True,
        failed_login_attempts=5,
    )
    db_session.add(credential)
    db_session.commit()

    settings_seed.seed_bootstrap_admin_user(db_session)

    db_session.refresh(credential)
    admin_role = db_session.scalar(select(Role).where(Role.name == "admin"))
    role_link = db_session.scalar(
        select(PersonRole).where(
            PersonRole.person_id == person.id,
            PersonRole.role_id == admin_role.id,
        )
    )

    assert credential.username == "codexadmin"
    assert verify_password("TempAdmin!2026", credential.password_hash) is True
    assert credential.is_active is True
    assert credential.must_change_password is False
    assert credential.failed_login_attempts == 0
    assert credential.locked_until is None
    assert role_link is not None
