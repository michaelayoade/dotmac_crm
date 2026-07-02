"""Vendor push-device registration for the field app.

The technician device route registers a token against a person; a vendor crew
registers against their VendorUser so vendor-targeted pushes (e.g. bid
approved) reach them. Guarded by require_vendor_token — same seam as the other
vendor field routers.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.field import DeviceTokenRead, DeviceTokenRegister
from app.services.push import push_devices
from app.services.vendor_auth_tokens import require_vendor_token

router = APIRouter(tags=["field-vendor-devices"])


@router.post("/vendor/devices", response_model=DeviceTokenRead, status_code=status.HTTP_201_CREATED)
def register_vendor_device(
    payload: DeviceTokenRegister,
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    return push_devices.register(
        db,
        platform=payload.platform,
        fcm_token=payload.fcm_token,
        app_version=payload.app_version,
        vendor_user_id=vendor["vendor_user_id"],
    )
