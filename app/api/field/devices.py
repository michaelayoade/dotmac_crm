from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.field import DeviceTokenRead, DeviceTokenRegister
from app.services.auth_dependencies import require_user_auth
from app.services.push import push_devices

router = APIRouter(tags=["field-devices"])


@router.post("/devices", response_model=DeviceTokenRead, status_code=status.HTTP_201_CREATED)
def register_device(
    payload: DeviceTokenRegister,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return push_devices.register(
        db,
        platform=payload.platform,
        fcm_token=payload.fcm_token,
        app_version=payload.app_version,
        person_id=auth["person_id"],
    )


@router.get("/devices", response_model=ListResponse[DeviceTokenRead])
def list_devices(
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    """The caller's own registered devices (for review / managing logins)."""
    items = push_devices.list_for_person(db, auth["person_id"])
    return {"items": items, "count": len(items), "limit": len(items), "offset": 0}


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def deregister_device(
    device_id: str,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    """Deregister one of the caller's own devices (logout / lost phone)."""
    push_devices.deregister(db, device_id=device_id, person_id=auth["person_id"])
