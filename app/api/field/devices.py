from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
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
