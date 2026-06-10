from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.field import DevicePlatform, FieldAttachmentKind


class FieldAttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_order_id: UUID | None
    installation_project_id: UUID | None
    note_id: UUID | None
    kind: FieldAttachmentKind
    file_name: str
    mime_type: str
    size_bytes: int
    latitude: float | None
    longitude: float | None
    captured_at: datetime | None
    signer_name: str | None
    uploaded_by_person_id: UUID | None
    uploaded_by_vendor_user_id: UUID | None
    client_ref: UUID | None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def download_path(self) -> str:
        return f"/api/v1/field/attachments/{self.id}/content"


class DeviceTokenRegister(BaseModel):
    platform: str = Field(min_length=1, max_length=20)
    fcm_token: str = Field(min_length=1, max_length=512)
    app_version: str | None = Field(default=None, max_length=40)


class DeviceTokenRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    person_id: UUID | None
    vendor_user_id: UUID | None
    platform: DevicePlatform
    app_version: str | None
    last_seen_at: datetime | None
    created_at: datetime
