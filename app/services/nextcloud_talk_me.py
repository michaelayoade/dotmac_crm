from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.services.nextcloud_talk import NextcloudTalkClient
from app.services.nextcloud_talk_accounts import clear_account, get_account_credentials, upsert_account


class NextcloudTalkNotConnectedError(Exception):
    pass


@dataclass(frozen=True)
class NextcloudTalkStatus:
    connected: bool
    base_url: str | None = None
    username: str | None = None


def get_status(db: Session, *, person_id: str) -> NextcloudTalkStatus:
    creds = get_account_credentials(db, person_id=person_id)
    if not creds:
        return NextcloudTalkStatus(connected=False)
    return NextcloudTalkStatus(connected=True, base_url=creds["base_url"], username=creds["username"])


def resolve_client(db: Session, *, person_id: str) -> NextcloudTalkClient:
    creds = get_account_credentials(db, person_id=person_id)
    if not creds:
        raise NextcloudTalkNotConnectedError("Nextcloud Talk is not connected for this user.")
    return NextcloudTalkClient(
        base_url=creds["base_url"],
        username=creds["username"],
        app_password=creds["app_password"],
        db=db,
    )


def connect(
    db: Session,
    *,
    person_id: str,
    base_url: str,
    username: str,
    app_password: str,
) -> NextcloudTalkStatus:
    """Verify credentials, then persist for the current user."""
    client = NextcloudTalkClient(base_url=base_url, username=username, app_password=app_password, db=db)
    # Verification step
    client.list_rooms()

    upsert_account(
        db,
        person_id=person_id,
        base_url=base_url,
        username=username,
        app_password=app_password,
    )
    return NextcloudTalkStatus(connected=True, base_url=base_url.rstrip("/"), username=username)


def disconnect(db: Session, *, person_id: str) -> None:
    clear_account(db, person_id=person_id)
