from uuid import UUID

from pydantic import BaseModel


class TypeaheadItem(BaseModel):
    id: UUID
    label: str


class CatalogOfferItem(BaseModel):
    """A dotmac_sub subscription plan (offer) for the quote/sale plan picker.

    ``id`` is the sub CatalogOffer id (stored on the quote line as
    ``metadata_.sub_offer_id`` and pushed back on subscription creation). Kept as
    a string — it's an opaque id from another app, not a local UUID column."""

    id: str
    code: str | None = None
    label: str
    recurring_price: str | None = None
    currency: str | None = None
    billing_cycle: str | None = None
    speed_download_mbps: int | None = None
    speed_upload_mbps: int | None = None
