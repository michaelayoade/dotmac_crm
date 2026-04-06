from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.logging import get_logger
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
from app.services.nextcloud_talk import (
    NextcloudTalkClient,
    NextcloudTalkError,
    normalize_and_validate_nextcloud_base_url,
)
from app.services.nextcloud_talk_me import NextcloudTalkNotConnectedError
from app.services.nextcloud_talk_me import connect as talk_connect
from app.services.nextcloud_talk_me import disconnect as talk_disconnect
from app.services.nextcloud_talk_me import get_status as talk_status
from app.services.nextcloud_talk_me import resolve_client as resolve_talk_me_client

router = APIRouter(prefix="/nextcloud-talk", tags=["nextcloud-talk"])
logger = get_logger(__name__)


def _resolve_client(db: Session, payload, auth: dict) -> NextcloudTalkClient:
    roles = {str(role).strip().lower() for role in (auth.get("roles") or [])}
    scopes = {str(scope).strip().lower() for scope in (auth.get("scopes") or [])}
    is_admin = "admin" in roles or "system:settings:read" in scopes or "system:settings:write" in scopes

    base_url = payload.base_url
    username = payload.username
    app_password = payload.app_password
    timeout = payload.timeout_sec

    if payload.connector_config_id:
        if not is_admin:
            raise HTTPException(status_code=403, detail="Connector-based Talk access requires admin privileges.")
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
    try:
        normalized_base_url = normalize_and_validate_nextcloud_base_url(str(base_url))
    except NextcloudTalkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        parsed_timeout = float(timeout or 30.0)
    except (TypeError, ValueError):
        parsed_timeout = 30.0

    return NextcloudTalkClient(
        base_url=normalized_base_url,
        username=username,
        app_password=app_password,
        timeout=parsed_timeout,
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
    actor_id = str(auth.get("person_id") or "")
    logger.info(
        "nextcloud_talk_me_login_requested actor_id=%s base_url=%s username=%s",
        actor_id,
        payload.base_url,
        payload.username,
    )
    try:
        status_obj = talk_connect(
            db,
            person_id=actor_id,
            base_url=payload.base_url,
            username=payload.username,
            app_password=payload.app_password,
        )
    except ValueError as exc:
        logger.warning("nextcloud_talk_me_login_invalid actor_id=%s error=%s", actor_id, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NextcloudTalkError as exc:
        logger.warning("nextcloud_talk_me_login_failed actor_id=%s error=%s", actor_id, str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    logger.info(
        "nextcloud_talk_me_login_completed actor_id=%s base_url=%s username=%s",
        actor_id,
        status_obj.base_url,
        status_obj.username,
    )
    return {
        "connected": True,
        "base_url": status_obj.base_url,
        "username": status_obj.username,
    }


@router.delete("/me/logout", response_model=dict)
def me_logout(db: Session = Depends(get_db), auth: dict = Depends(require_user_auth)):
    actor_id = str(auth.get("person_id") or "")
    logger.info("nextcloud_talk_me_logout_requested actor_id=%s", actor_id)
    talk_disconnect(db, person_id=actor_id)
    logger.info("nextcloud_talk_me_logout_completed actor_id=%s", actor_id)
    return {"connected": False}


@router.get("/me/rooms", response_model=list[dict])
def me_list_rooms(db: Session = Depends(get_db), auth: dict = Depends(require_user_auth)):
    actor_id = str(auth.get("person_id") or "")
    try:
        client = resolve_talk_me_client(db, person_id=actor_id)
    except NextcloudTalkNotConnectedError as exc:
        logger.warning("nextcloud_talk_me_list_rooms_not_connected actor_id=%s error=%s", actor_id, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        rooms = client.list_rooms()
        logger.info("nextcloud_talk_me_list_rooms_completed actor_id=%s count=%d", actor_id, len(rooms))
        return rooms
    except NextcloudTalkError as exc:
        logger.warning("nextcloud_talk_me_list_rooms_failed actor_id=%s error=%s", actor_id, str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/me/rooms", response_model=dict)
def me_create_room(
    payload: NextcloudTalkRoomCreateMeRequest, db: Session = Depends(get_db), auth: dict = Depends(require_user_auth)
):
    actor_id = str(auth.get("person_id") or "")
    try:
        client = resolve_talk_me_client(db, person_id=actor_id)
    except NextcloudTalkNotConnectedError as exc:
        logger.warning("nextcloud_talk_me_create_room_not_connected actor_id=%s error=%s", actor_id, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        room = client.create_room(room_name=payload.room_name, room_type=payload.room_type, options=payload.options)
        logger.info(
            "nextcloud_talk_me_create_room_completed actor_id=%s room_type=%s room_name=%s",
            actor_id,
            payload.room_type,
            payload.room_name,
        )
        return room
    except NextcloudTalkError as exc:
        logger.warning("nextcloud_talk_me_create_room_failed actor_id=%s error=%s", actor_id, str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/me/rooms/{room_token}/messages", response_model=dict)
def me_post_message(
    room_token: str,
    payload: NextcloudTalkMessageSendMeRequest,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_user_auth),
):
    actor_id = str(auth.get("person_id") or "")
    try:
        client = resolve_talk_me_client(db, person_id=actor_id)
    except NextcloudTalkNotConnectedError as exc:
        logger.warning(
            "nextcloud_talk_me_post_message_not_connected actor_id=%s room_token=%s error=%s",
            actor_id,
            room_token,
            str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = client.post_message(room_token=room_token, message=payload.message, options=payload.options)
        logger.info("nextcloud_talk_me_post_message_completed actor_id=%s room_token=%s", actor_id, room_token)
        return result
    except NextcloudTalkError as exc:
        logger.warning(
            "nextcloud_talk_me_post_message_failed actor_id=%s room_token=%s error=%s", actor_id, room_token, str(exc)
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/me/rooms/{room_token}/messages/list", response_model=list[dict])
def me_list_messages(
    room_token: str,
    payload: NextcloudTalkMessageListMeRequest,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_user_auth),
):
    actor_id = str(auth.get("person_id") or "")
    try:
        client = resolve_talk_me_client(db, person_id=actor_id)
    except NextcloudTalkNotConnectedError as exc:
        logger.warning(
            "nextcloud_talk_me_list_messages_not_connected actor_id=%s room_token=%s error=%s",
            actor_id,
            room_token,
            str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        messages = client.list_messages(
            room_token,
            last_known_message_id=int(payload.last_known_message_id or 0),
            limit=int(payload.limit or 100),
            timeout=int(payload.timeout or 0),
        )
        logger.info(
            "nextcloud_talk_me_list_messages_completed actor_id=%s room_token=%s count=%d",
            actor_id,
            room_token,
            len(messages),
        )
        return messages
    except NextcloudTalkError as exc:
        logger.warning(
            "nextcloud_talk_me_list_messages_failed actor_id=%s room_token=%s error=%s", actor_id, room_token, str(exc)
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/rooms/list", response_model=list[dict])
def list_rooms(
    payload: NextcloudTalkRoomListRequest,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_user_auth),
):
    client = _resolve_client(db, payload, auth)
    try:
        return client.list_rooms()
    except NextcloudTalkError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/rooms", response_model=dict)
def create_room(
    payload: NextcloudTalkRoomCreateRequest,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_user_auth),
):
    client = _resolve_client(db, payload, auth)
    try:
        return client.create_room(
            room_name=payload.room_name,
            room_type=payload.room_type,
            options=payload.options,
        )
    except NextcloudTalkError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/rooms/{room_token}/messages", response_model=dict)
def post_message(
    room_token: str,
    payload: NextcloudTalkMessageRequest,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_user_auth),
):
    client = _resolve_client(db, payload, auth)
    try:
        return client.post_message(
            room_token=room_token,
            message=payload.message,
            options=payload.options,
        )
    except NextcloudTalkError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/rooms/{room_token}/messages/list", response_model=list[dict])
def list_messages(
    room_token: str,
    payload: NextcloudTalkMessageListRequest,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_user_auth),
):
    """List messages for a room token (for Talk floater polling)."""
    client = _resolve_client(db, payload, auth)
    try:
        return client.list_messages(
            room_token,
            last_known_message_id=int(payload.last_known_message_id or 0),
            limit=int(payload.limit or 100),
            timeout=int(payload.timeout or 0),
        )
    except NextcloudTalkError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
