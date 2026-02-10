"""Vendor portal e2e tests."""

from __future__ import annotations

from playwright.sync_api import Page, expect
from tests.playwright.pages.vendor import (
    VendorDashboardPage,
    VendorLoginPage,
    VendorProjectsPage,
)


class TestVendorLogin:
    """Tests for vendor portal login."""

    def test_vendor_login_page_loads(self, anon_page: Page, settings):
        """Vendor login page should load."""
        page = VendorLoginPage(anon_page, settings.base_url)
        page.goto()
        page.expect_loaded()

    def test_vendor_login_requires_credentials(self, anon_page: Page, settings):
        """Login should require valid credentials."""
        page = VendorLoginPage(anon_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # Should stay on login page without credentials
        expect(anon_page).to_have_url("**/vendor/auth/login**")


class TestVendorDashboard:
    """Tests for vendor portal dashboard."""

    def test_dashboard_loads(self, vendor_page: Page, settings):
        """Vendor dashboard should load."""
        page = VendorDashboardPage(vendor_page, settings.base_url)
        page.goto()
        page.expect_loaded()

    def test_dashboard_shows_available_projects(self, vendor_page: Page, settings):
        """Dashboard should show available projects section."""
        page = VendorDashboardPage(vendor_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        page.expect_available_projects_visible()

    def test_dashboard_shows_my_projects(self, vendor_page: Page, settings):
        """Dashboard should show my projects section."""
        page = VendorDashboardPage(vendor_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        page.expect_my_projects_visible()


class TestVendorProjects:
    """Tests for vendor projects pages."""

    def test_available_projects_page_loads(self, vendor_page: Page, settings):
        """Available projects page should load."""
        page = VendorProjectsPage(vendor_page, settings.base_url)
        page.goto_available()
        page.expect_available_loaded()

    def test_my_projects_page_loads(self, vendor_page: Page, settings):
        """My projects page should load."""
        page = VendorProjectsPage(vendor_page, settings.base_url)
        page.goto_mine()
        page.expect_mine_loaded()


class TestVendorNavigation:
    """Tests for vendor portal navigation."""

    def test_navigate_to_projects(self, vendor_page: Page, settings):
        """Should navigate from dashboard to projects."""
        dashboard = VendorDashboardPage(vendor_page, settings.base_url)
        dashboard.goto()
        dashboard.expect_loaded()
        dashboard.navigate_to_available_projects()
        vendor_page.wait_for_url("**/projects/available**")
