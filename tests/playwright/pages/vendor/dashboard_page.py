"""Vendor portal dashboard page object."""

from __future__ import annotations

from playwright.sync_api import Page, expect
from tests.playwright.pages.base_page import BasePage


class VendorDashboardPage(BasePage):
    """Page object for the vendor portal dashboard."""

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto(self, path: str = "") -> None:
        """Navigate to the vendor dashboard."""
        super().goto("/vendor/dashboard")

    def expect_loaded(self) -> None:
        """Assert the dashboard is loaded."""
        expect(self.page.get_by_role("heading", name="Dashboard", exact=True)).to_be_visible()

    def expect_available_projects_visible(self) -> None:
        """Assert available projects section is visible."""
        expect(self.page.get_by_text("Available", exact=False).first).to_be_visible()

    def expect_my_projects_visible(self) -> None:
        """Assert my projects section is visible."""
        expect(self.page.get_by_text("My Project", exact=False).first).to_be_visible()

    def navigate_to_available_projects(self) -> None:
        """Navigate to available projects."""
        self.page.get_by_role("link", name="Available").first.click()

    def navigate_to_my_projects(self) -> None:
        """Navigate to my projects."""
        self.page.get_by_role("link", name="My Project").first.click()

    def get_available_projects_count(self) -> int:
        """Get count of available projects displayed."""
        projects = self.page.locator("[data-testid='available-project']").or_(
            self.page.locator(".available-project")
        )
        return projects.count()

    def get_my_projects_count(self) -> int:
        """Get count of my projects displayed."""
        projects = self.page.locator("[data-testid='my-project']").or_(
            self.page.locator(".my-project")
        )
        return projects.count()

    def logout(self) -> None:
        """Log out of vendor portal."""
        self.page.get_by_role("button", name="Logout").or_(
            self.page.get_by_role("link", name="Logout")
        ).first.click()
