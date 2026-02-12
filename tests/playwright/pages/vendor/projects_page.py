"""Vendor portal projects page object."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.base_page import BasePage


class VendorProjectsPage(BasePage):
    """Page object for the vendor projects pages."""

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto_available(self) -> None:
        """Navigate to available projects."""
        super().goto("/vendor/projects/available")

    def goto_mine(self) -> None:
        """Navigate to my projects."""
        super().goto("/vendor/projects/mine")

    def expect_available_loaded(self) -> None:
        """Assert available projects page is loaded."""
        expect(self.page.get_by_role("heading", name="Available", exact=True)).to_be_visible()

    def expect_mine_loaded(self) -> None:
        """Assert my projects page is loaded."""
        expect(
            self.page.get_by_role("heading", name="My Project", exact=True)
            .or_(self.page.get_by_role("heading", name="Project", exact=True))
            .first
        ).to_be_visible()

    def get_project_count(self) -> int:
        """Get count of projects displayed."""
        rows = self.page.locator("table tbody tr").or_(self.page.locator("[data-testid='project-item']"))
        return rows.count()

    def click_project(self, project_name: str) -> None:
        """Click on a project to view details."""
        self.page.get_by_text(project_name).first.click()

    def expect_project_in_list(self, project_name: str) -> None:
        """Assert a project is visible in the list."""
        expect(self.page.get_by_text(project_name)).to_be_visible()

    def claim_project(self, project_name: str) -> None:
        """Claim an available project."""
        row = self.page.get_by_role("row").filter(has_text=project_name)
        row.get_by_role("button", name="Claim").first.click()

    def submit_quote(self, project_name: str) -> None:
        """Navigate to submit quote for a project."""
        row = self.page.get_by_role("row").filter(has_text=project_name)
        row.get_by_role("link", name="Quote").first.click()

    def submit_as_built(self, project_name: str) -> None:
        """Navigate to submit as-built for a project."""
        row = self.page.get_by_role("row").filter(has_text=project_name)
        row.get_by_role("link", name="As-Built").first.click()
