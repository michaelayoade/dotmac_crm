"""Vendor push: device registration route guard + quote-approved fan-out."""

import uuid

from app.models.vendor import (
    InstallationProject,
    InstallationProjectStatus,
    ProjectQuote,
    ProjectQuoteStatus,
    Vendor,
    VendorUser,
)


def _walk(dependant):
    for dep in dependant.dependencies:
        yield dep
        yield from _walk(dep)


def test_vendor_device_route_uses_vendor_token_guard():
    from fastapi.routing import APIRoute

    from app.api.field.vendor_devices import router
    from app.services.vendor_auth_tokens import require_vendor_token

    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert len(routes) == 1
    for route in routes:
        assert any(dep.call is require_vendor_token for dep in _walk(route.dependant))


def test_register_stores_vendor_user_id(db_session, person):
    from app.services.push import push_devices

    vendor = Vendor(name="FiberWorks", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    vu = VendorUser(vendor_id=vendor.id, person_id=person.id, is_active=True)
    db_session.add(vu)
    db_session.commit()

    device = push_devices.register(db_session, platform="android", fcm_token="tok-1", vendor_user_id=str(vu.id))
    assert str(device.vendor_user_id) == str(vu.id)
    assert device.person_id is None


def test_quote_approved_push_targets_active_vendor_users(db_session, person, project, monkeypatch):
    vendor = Vendor(name="FiberWorks", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    active = VendorUser(vendor_id=vendor.id, person_id=person.id, is_active=True)
    # A second, inactive login must NOT be pushed.
    from app.models.person import Person

    other = Person(first_name="B", last_name="C", email=f"b-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(other)
    db_session.commit()
    inactive = VendorUser(vendor_id=vendor.id, person_id=other.id, is_active=False)
    db_session.add_all([active, inactive])
    db_session.commit()

    ip = InstallationProject(
        project_id=project.id, assigned_vendor_id=vendor.id, status=InstallationProjectStatus.in_progress
    )
    db_session.add(ip)
    db_session.commit()
    quote = ProjectQuote(project_id=ip.id, vendor_id=vendor.id, status=ProjectQuoteStatus.approved)
    db_session.add(quote)
    db_session.commit()

    sent: list[str] = []

    class _Task:
        def delay(self, *, vendor_user_id, title, body, data):
            sent.append(vendor_user_id)

    import app.tasks.push as push_tasks

    monkeypatch.setattr(push_tasks, "send_push_to_vendor_user", _Task())

    from app.services.push import queue_vendor_quote_approved_push

    queue_vendor_quote_approved_push(db_session, quote)

    assert sent == [str(active.id)]  # only the active vendor user
