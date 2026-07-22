"""Session metadata tagging for trusted-caller chat mints.

``surface`` is the routing key the outbound-reply push to dotmac_sub branches
on. Prod had 37k conversations with a NULL surface, so that push had never
fired for a customer; these pin each surface the mint can produce.
"""

from uuid import uuid4

from app.api.crm.widget_internal import (
    CUSTOMER_SURFACE,
    WidgetInternalSessionCreate,
    build_session_metadata,
)
from app.services.crm.widget.service import RESELLER_PORTAL_SURFACE
from app.services.field.chat import FIELD_CHAT_SURFACE


def _payload(**kwargs) -> WidgetInternalSessionCreate:
    return WidgetInternalSessionCreate(config_id=uuid4(), email="customer@example.com", **kwargs)


def test_subscriber_session_is_tagged_customer_surface():
    """The regression: without this the push to sub never fires."""
    subscriber_id = uuid4()
    metadata = build_session_metadata(None, _payload(crm_subscriber_id=subscriber_id))

    assert metadata["surface"] == CUSTOMER_SURFACE
    assert metadata["subscriber_id"] == str(subscriber_id)
    assert metadata["crm_subscriber_id"] == str(subscriber_id)


def test_field_visit_keeps_field_surface_even_with_a_subscriber():
    """Field sessions also carry a subscriber — they must not be relabelled."""
    metadata = build_session_metadata(
        None,
        _payload(crm_subscriber_id=uuid4(), field_work_order_id=uuid4()),
    )

    assert metadata["surface"] == FIELD_CHAT_SURFACE


def test_caller_supplied_surface_wins():
    """Reseller portal declares its own surface through payload metadata."""
    metadata = build_session_metadata(
        None,
        _payload(
            crm_subscriber_id=uuid4(),
            metadata={"surface": RESELLER_PORTAL_SURFACE, "reseller_id": str(uuid4())},
        ),
    )

    assert metadata["surface"] == RESELLER_PORTAL_SURFACE


def test_anonymous_session_gets_no_surface():
    """No subscriber, no work order — nothing to route, so no surface claim."""
    assert "surface" not in build_session_metadata(None, _payload())


def test_existing_metadata_is_preserved():
    metadata = build_session_metadata({"locale": "en-NG"}, _payload(crm_subscriber_id=uuid4()))

    assert metadata["locale"] == "en-NG"
    assert metadata["surface"] == CUSTOMER_SURFACE
