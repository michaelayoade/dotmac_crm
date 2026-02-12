"""Operations management e2e tests."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.admin.operations import (
    DispatchPage,
    ServiceOrderFormPage,
    ServiceOrdersPage,
    WorkOrdersPage,
)


class TestServiceOrdersList:
    """Tests for the service orders list page."""

    def test_service_orders_page_loads(self, admin_page: Page, settings):
        """Service orders page should load."""
        page = ServiceOrdersPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()

    def test_service_orders_table_visible(self, admin_page: Page, settings):
        """Service orders table should be visible."""
        page = ServiceOrdersPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        expect(admin_page.locator("table")).to_be_visible()

    def test_new_service_order_button(self, admin_page: Page, settings):
        """New service order button should navigate to form."""
        page = ServiceOrdersPage(admin_page, settings.base_url)
        page.goto()
        page.click_new_order()
        admin_page.wait_for_url("**/service-orders/new**")


class TestServiceOrderForm:
    """Tests for the service order form."""

    def test_service_order_form_loads(self, admin_page: Page, settings):
        """Service order form should load."""
        form = ServiceOrderFormPage(admin_page, settings.base_url)
        form.goto_new()
        form.expect_loaded()

    def test_service_order_form_has_required_fields(self, admin_page: Page, settings):
        """Form should have account selector."""
        form = ServiceOrderFormPage(admin_page, settings.base_url)
        form.goto_new()
        form.expect_loaded()
        expect(admin_page.get_by_label("Account")).to_be_visible()

    def test_service_order_form_cancel(self, admin_page: Page, settings):
        """Cancel should return to list."""
        form = ServiceOrderFormPage(admin_page, settings.base_url)
        form.goto_new()
        form.expect_loaded()
        form.cancel()
        admin_page.wait_for_url("**/service-orders**")


class TestWorkOrders:
    """Tests for the work orders page."""

    def test_work_orders_page_loads(self, admin_page: Page, settings):
        """Work orders page should load."""
        page = WorkOrdersPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()

    def test_work_orders_table_visible(self, admin_page: Page, settings):
        """Work orders table should be visible."""
        page = WorkOrdersPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        expect(admin_page.locator("table")).to_be_visible()


class TestDispatch:
    """Tests for the dispatch page."""

    def test_dispatch_page_loads(self, admin_page: Page, settings):
        """Dispatch page should load."""
        page = DispatchPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()

    def test_dispatch_calendar_visible(self, admin_page: Page, settings):
        """Dispatch calendar/board should be visible."""
        page = DispatchPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        page.expect_calendar_visible()


class TestOperationsAPI:
    """API-level tests for operations."""

    def test_list_service_orders_api(self, api_context, admin_token):
        """API should return service orders list."""
        from tests.playwright.helpers.api import api_get, bearer_headers

        response = api_get(
            api_context,
            "/api/v1/provisioning/service-orders?limit=10",
            headers=bearer_headers(admin_token),
        )
        assert response.status == 200
        data = response.json()
        assert "items" in data

    def test_list_install_appointments_api(self, api_context, admin_token):
        """API should return install appointments list."""
        from tests.playwright.helpers.api import api_get, bearer_headers

        response = api_get(
            api_context,
            "/api/v1/provisioning/install-appointments?limit=10",
            headers=bearer_headers(admin_token),
        )
        assert response.status == 200
        data = response.json()
        assert "items" in data

    def test_list_provisioning_tasks_api(self, api_context, admin_token):
        """API should return provisioning tasks list."""
        from tests.playwright.helpers.api import api_get, bearer_headers

        response = api_get(
            api_context,
            "/api/v1/provisioning/tasks?limit=10",
            headers=bearer_headers(admin_token),
        )
        assert response.status == 200
        data = response.json()
        assert "items" in data


class TestOperationsWorkflows:
    """Tests for operations workflows."""

    def test_navigate_to_service_orders(self, admin_page: Page, settings):
        """Should navigate to service orders from dashboard."""
        from tests.playwright.pages.admin.dashboard_page import AdminDashboardPage

        dashboard = AdminDashboardPage(admin_page, settings.base_url)
        dashboard.goto()
        dashboard.expect_loaded()
        admin_page.get_by_role("link", name="Operations").first.click()
        admin_page.wait_for_url("**/operations**")

    def test_create_service_order_workflow(self, admin_page: Page, settings):
        """Should be able to start service order creation."""
        orders = ServiceOrdersPage(admin_page, settings.base_url)
        orders.goto()
        orders.click_new_order()

        form = ServiceOrderFormPage(admin_page, settings.base_url)
        form.expect_loaded()
        expect(admin_page.get_by_label("Account")).to_be_visible()
