from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.models.material_request import MaterialRequestERPSyncStatus, MaterialRequestStatus
from app.web.templates import Jinja2Templates


class _Request:
    state = SimpleNamespace(branding=SimpleNamespace(favicon_url=None, company_name="DotMac"))
    url = SimpleNamespace(path="/admin/operations/material-requests")

    @staticmethod
    def url_for(name: str, **_path_params: object) -> str:
        return f"/{name}"


def _material_request(sync_status: MaterialRequestERPSyncStatus) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        number="MR-0001",
        status=MaterialRequestStatus.issued,
        erp_sync_status=sync_status,
        erp_material_status=None,
        erp_material_request_id=None,
        erp_sync_attempts=1,
        erp_synced_at=None,
        erp_sync_error="ERP offline" if sync_status == MaterialRequestERPSyncStatus.failed else None,
        priority=SimpleNamespace(value="medium"),
        requested_by=SimpleNamespace(first_name="Ada", last_name="Ops"),
        approved_by=None,
        collected_by=None,
        ticket=None,
        project=None,
        work_order=None,
        source_location=None,
        destination_location=None,
        items=[],
        notes=None,
        submitted_at=None,
        approved_at=now,
        rejected_at=None,
        fulfilled_at=None,
        created_at=now,
        updated_at=now,
    )


def _submitted_material_request_with_item() -> SimpleNamespace:
    mr = _material_request(MaterialRequestERPSyncStatus.pending)
    mr.status = MaterialRequestStatus.submitted
    mr.erp_sync_status = None
    mr.erp_sync_attempts = 0
    mr.erp_sync_error = None
    mr.items = [
        SimpleNamespace(
            id=uuid4(),
            quantity=2,
            serial_numbers=None,
            notes=None,
            item=SimpleNamespace(name="ONT", sku="ONT-001"),
        )
    ]
    return mr


def _base_context(**kwargs: object) -> dict[str, object]:
    return {
        "request": _Request(),
        "current_user": None,
        "sidebar_stats": {},
        "csrf_token": "csrf",
        "active_page": "material-requests",
        **kwargs,
    }


def test_material_request_list_marks_pending_erp_issue():
    templates = Jinja2Templates(directory="templates")
    template = templates.env.get_template("admin/material_requests/index.html")

    html = template.render(
        _base_context(
            items=[_material_request(MaterialRequestERPSyncStatus.pending)],
            filter_status="",
            filter_erp_status="",
            filter_date_from="",
            filter_date_to="",
        )
    )

    assert "Awaiting ERP issue" in html
    assert "ERP: Pending issue" in html
    assert "Pending ERP issue" in html


def test_material_request_detail_warns_when_erp_has_not_confirmed_issue():
    templates = Jinja2Templates(directory="templates")
    template = templates.env.get_template("admin/material_requests/detail.html")

    html = template.render(
        _base_context(
            mr=_material_request(MaterialRequestERPSyncStatus.failed),
            warehouses=[],
            collectors=[],
        )
    )

    assert "Awaiting ERP issue" in html
    assert "ERP has not confirmed this issue yet." in html
    assert "Retry ERP Sync" in html


def test_material_request_detail_renders_searchable_serial_picker():
    templates = Jinja2Templates(directory="templates")
    template = templates.env.get_template("admin/material_requests/detail.html")

    html = template.render(
        _base_context(
            mr=_submitted_material_request_with_item(),
            warehouses=[SimpleNamespace(id=uuid4(), name="Stores", code="Stores - DT")],
            collectors=[],
        )
    )

    assert "data-serial-search" in html
    assert "data-serial-results" in html
    assert "data-load-more" in html
    assert 'name="serial_numbers_' in html
