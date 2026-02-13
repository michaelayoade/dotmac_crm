from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.nextcloud_talk import NextcloudTalkAccount
from app.services.auth_flow import _decrypt_secret, _encrypt_secret
from app.services.common import coerce_uuid


def upsert_account(
    db: Session,
    *,
    person_id: str,
    base_url: str,
    username: str,
    app_password: str,
) -> NextcloudTalkAccount:
    base_url_value = (base_url or "").strip().rstrip("/")
    username_value = (username or "").strip()
    app_password_value = (app_password or "").strip()
    if not base_url_value or not username_value or not app_password_value:
        raise ValueError("Missing base_url/username/app_password")

    encrypted = _encrypt_secret(db, app_password_value)
    person_uuid = coerce_uuid(person_id)

    account = (
        db.query(NextcloudTalkAccount)
        .filter(NextcloudTalkAccount.person_id == person_uuid)
        .first()
    )
    if account:
        account.base_url = base_url_value
        account.username = username_value
        account.app_password_enc = encrypted
    else:
        account = NextcloudTalkAccount(
            person_id=person_uuid,
            base_url=base_url_value,
            username=username_value,
            app_password_enc=encrypted,
        )
        db.add(account)
    db.commit()
    db.refresh(account)
    return account


def clear_account(db: Session, *, person_id: str) -> None:
    person_uuid = coerce_uuid(person_id)
    account = (
        db.query(NextcloudTalkAccount)
        .filter(NextcloudTalkAccount.person_id == person_uuid)
        .first()
    )
    if not account:
        return
    db.delete(account)
    db.commit()


def get_account_credentials(db: Session, *, person_id: str) -> dict[str, str] | None:
    person_uuid = coerce_uuid(person_id)
    account = (
        db.query(NextcloudTalkAccount)
        .filter(NextcloudTalkAccount.person_id == person_uuid)
        .first()
    )
    if not account:
        return None
    return {
        "base_url": account.base_url,
        "username": account.username,
        "app_password": _decrypt_secret(db, account.app_password_enc),
    }
