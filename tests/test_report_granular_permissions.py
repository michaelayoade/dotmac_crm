from scripts.seed_rbac import DEFAULT_PERMISSIONS

from app.web.admin import reports


def test_report_granular_permissions_are_seeded():
    permission_keys = {key for key, _description in DEFAULT_PERMISSIONS}

    assert "reports:billing-risk:read" in permission_keys
    assert "reports:billing-risk:write" in permission_keys
    assert "reports:postpaid-customers:read" in permission_keys
    assert "reports:online-last-24h:read" in permission_keys
    assert "reports:online-last-24h:write" in permission_keys


def test_report_routes_use_granular_permission_keys():
    assert "reports:billing-risk:read" in reports.REPORTS_BILLING_RISK_READ_PERMISSIONS
    assert "reports:billing-risk:write" in reports.REPORTS_BILLING_RISK_WRITE_PERMISSIONS
    assert "reports:online-last-24h:read" in reports.REPORTS_ONLINE_LAST_24H_READ_PERMISSIONS
    assert "reports:online-last-24h:write" in reports.REPORTS_ONLINE_LAST_24H_WRITE_PERMISSIONS
