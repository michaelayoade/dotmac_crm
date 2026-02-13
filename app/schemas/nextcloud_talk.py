from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class NextcloudTalkAuth(BaseModel):
    connector_config_id: UUID | None = None
    base_url: str | None = Field(default=None, max_length=500)
    username: str | None = Field(default=None, max_length=150)
    app_password: str | None = Field(default=None, max_length=255)
    timeout_sec: int | None = Field(default=None, ge=1, le=120)

    @model_validator(mode="after")
    def _validate_auth(self) -> NextcloudTalkAuth:
        if self.connector_config_id is None and (not self.base_url or not self.username or not self.app_password):
            raise ValueError("Provide base_url, username, and app_password when connector_config_id is not set.")
        return self


class NextcloudTalkRoomListRequest(NextcloudTalkAuth):
    pass


class NextcloudTalkRoomCreateRequest(NextcloudTalkAuth):
    room_name: str = Field(min_length=1, max_length=200)
    room_type: str | int = Field(default="public")
    options: dict | None = None


class NextcloudTalkMessageRequest(NextcloudTalkAuth):
    message: str = Field(min_length=1)
    options: dict | None = None


class NextcloudTalkMessageListRequest(NextcloudTalkAuth):
    last_known_message_id: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=200)
    timeout: int = Field(default=0, ge=0, le=60)


class NextcloudTalkLoginRequest(BaseModel):
    base_url: str = Field(min_length=1, max_length=500)
    username: str = Field(min_length=1, max_length=150)
    app_password: str = Field(min_length=1, max_length=255)


class NextcloudTalkRoomCreateMeRequest(BaseModel):
    room_name: str = Field(min_length=1, max_length=200)
    room_type: str | int = Field(default="public")
    options: dict | None = None


class NextcloudTalkMessageSendMeRequest(BaseModel):
    message: str = Field(min_length=1)
    options: dict | None = None


class NextcloudTalkMessageListMeRequest(BaseModel):
    last_known_message_id: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=200)
    timeout: int = Field(default=0, ge=0, le=60)
