from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.connector import ConnectorConfig
from app.schemas.nextcloud_talk import (
    NextcloudTalkLoginRequest,
    NextcloudTalkMessageListMeRequest,
    NextcloudTalkMessageListRequest,
    NextcloudTalkMessageRequest,
    NextcloudTalkMessageSendMeRequest,
    NextcloudTalkRoomCreateMeRequest,
    NextcloudTalkRoomCreateRequest,
    NextcloudTalkRoomListRequest,
)
from app.services.auth_dependencies import require_user_auth
from app.services.common import coerce_uuid
from app.services.nextcloud_talk import NextcloudTalkClient, NextcloudTalkError
from app.services.nextcloud_talk_me import NextcloudTalkNotConnectedError
from app.services.nextcloud_talk_me import connect as talk_connect
from app.services.nextcloud_talk_me import disconnect as talk_disconnect
from app.services.nextcloud_talk_me import get_status as talk_status
from app.services.nextcloud_talk_me import resolve_client as resolve_talk_me_client

router = APIRouter(prefix="/nextcloud-talk", tags=["nextcloud-talk"])


def _resolve_client(db: Session, payload) -> NextcloudTalkClient:
    base_url = payload.base_url
    username = payload.username
    app_password = payload.app_password
    timeout = payload.timeout_sec

    if payload.connector_config_id:
        config = db.get(ConnectorConfig, coerce_uuid(payload.connector_config_id))
        if not config:
            raise HTTPException(status_code=404, detail="Connector config not found")
        auth_config = dict(config.auth_config or {})
        base_url = base_url or config.base_url
        username = username or auth_config.get("username")
        app_password = app_password or auth_config.get("app_password") or auth_config.get("password")
        timeout = timeout or config.timeout_sec or auth_config.get("timeout_sec")

    if not base_url or not username or not app_password:
        raise HTTPException(
            status_code=400,
            detail="Nextcloud Talk credentials are incomplete.",
        )

    return NextcloudTalkClient(
        base_url=base_url,
        username=username,
        app_password=app_password,
        timeout=float(timeout or 30.0),
    )


@router.get("/me/status", response_model=dict)
def me_status(db: Session = Depends(get_db), auth: dict = Depends(require_user_auth)):
    status_obj = talk_status(db, person_id=str(auth.get("person_id") or ""))
    if not status_obj.connected:
        return {"connected": False}
    return {"connected": True, "base_url": status_obj.base_url, "username": status_obj.username}


@router.post("/me/login", response_model=dict)
def me_login(
    payload: NextcloudTalkLoginRequest, db: Session = Depends(get_db), auth: dict = Depends(require_user_auth)
):
    """Store Nextcloud Talk credentials for the current user after verifying connectivity."""
    try:
        status_obj = talk_connect(
            db,
            person_id=str(auth.get("person_id") or ""),
            base_url=payload.base_url,
            username=payload.username,
            app_password=payload.app_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NextcloudTalkError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return {
        "connected": True,
        "base_url": status_obj.base_url,
        "username": status_obj.username,
    }


@router.delete("/me/logout", response_model=dict)
def me_logout(db: Session = Depends(get_db), auth: dict = Depends(require_user_auth)):
    talk_disconnect(db, person_id=str(auth.get("person_id") or ""))
    return {"connected": False}


@router.get("/me/rooms", response_model=list[dict])
def me_list_rooms(db: Session = Depends(get_db), auth: dict = Depends(require_user_auth)):
    try:
        client = resolve_talk_me_client(db, person_id=str(auth.get("person_id") or ""))
    except NextcloudTalkNotConnectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return client.list_rooms()
    except NextcloudTalkError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/me/rooms", response_model=dict)
def me_create_room(
    payload: NextcloudTalkRoomCreateMeRequest, db: Session = Depends(get_db), auth: dict = Depends(require_user_auth)
):
    try:
        client = resolve_talk_me_client(db, person_id=str(auth.get("person_id") or ""))
    except NextcloudTalkNotConnectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return client.create_room(room_name=payload.room_name, room_type=payload.room_type, options=payload.options)
    except NextcloudTalkError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/me/rooms/{room_token}/messages", response_model=dict)
def me_post_message(
    room_token: str,
    payload: NextcloudTalkMessageSendMeRequest,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_user_auth),
):
    try:
        client = resolve_talk_me_client(db, person_id=str(auth.get("person_id") or ""))
    except NextcloudTalkNotConnectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return client.post_message(room_token=room_token, message=payload.message, options=payload.options)
    except NextcloudTalkError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/me/rooms/{room_token}/messages/list", response_model=list[dict])
def me_list_messages(
    room_token: str,
    payload: NextcloudTalkMessageListMeRequest,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_user_auth),
):
    try:
        client = resolve_talk_me_client(db, person_id=str(auth.get("person_id") or ""))
    except NextcloudTalkNotConnectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return client.list_messages(
            room_token,
            last_known_message_id=int(payload.last_known_message_id or 0),
            limit=int(payload.limit or 100),
            timeout=int(payload.timeout or 0),
        )
    except NextcloudTalkError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/rooms/list", response_model=list[dict])
def list_rooms(payload: NextcloudTalkRoomListRequest, db: Session = Depends(get_db)):
    client = _resolve_client(db, payload)
    try:
        return client.list_rooms()
    except NextcloudTalkError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/rooms", response_model=dict)
def create_room(payload: NextcloudTalkRoomCreateRequest, db: Session = Depends(get_db)):
    client = _resolve_client(db, payload)
    try:
        return client.create_room(
            room_name=payload.room_name,
            room_type=payload.room_type,
            options=payload.options,
        )
    except NextcloudTalkError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/rooms/{room_token}/messages", response_model=dict)
def post_message(room_token: str, payload: NextcloudTalkMessageRequest, db: Session = Depends(get_db)):
    client = _resolve_client(db, payload)
    try:
        return client.post_message(
            room_token=room_token,
            message=payload.message,
            options=payload.options,
        )
    except NextcloudTalkError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/rooms/{room_token}/messages/list", response_model=list[dict])
def list_messages(room_token: str, payload: NextcloudTalkMessageListRequest, db: Session = Depends(get_db)):
    """List messages for a room token (for Talk floater polling)."""
    client = _resolve_client(db, payload)
    try:
        return client.list_messages(
            room_token,
            last_known_message_id=int(payload.last_known_message_id or 0),
            limit=int(payload.limit or 100),
            timeout=int(payload.timeout or 0),
        )
    except NextcloudTalkError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
