from pydantic import BaseModel, Field


class VendorLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=255)


class VendorMfaVerifyRequest(BaseModel):
    mfa_token: str = Field(min_length=1)
    code: str = Field(min_length=6, max_length=10)


class VendorRefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class VendorTokenResponse(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    mfa_required: bool = False
    mfa_token: str | None = None
    vendor_id: str | None = None
    vendor_user_id: str | None = None
    vendor_role: str | None = None
