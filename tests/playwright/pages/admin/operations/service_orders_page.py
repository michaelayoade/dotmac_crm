"""Service orders list page object."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.base_page import BasePage


class ServiceOrdersPage(BasePage):
    """Page object for the service orders list page."""

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto(self) -> None:
        """Navigate to the service orders list."""
        super().goto("/admin/operations/service-orders")

    def expect_loaded(self) -> None:
        """Assert the service orders page is loaded."""
        expect(self.page.get_by_role("heading", name="Service Orders", exact=True)).to_be_visible()

    def filter_by_status(self, status: str) -> None:
        """Filter by order status."""
        self.page.get_by_label("Status").select_option(status)

    def search(self, query: str) -> None:
        """Search service orders."""
        search_input = self.page.get_by_placeholder("Search")
        search_input.fill(query)
        self.page.keyboard.press("Enter")

    def click_new_order(self) -> None:
        """Click new service order button."""
        self.page.get_by_role("link", name="New").first.click()

    def click_order_row(self, order_id: str) -> None:
        """Click on a service order row."""
        self.page.get_by_role("row").filter(has_text=order_id).click()

    def expect_order_in_list(self, order_id: str) -> None:
        """Assert a service order is visible in the list."""
        expect(self.page.get_by_role("row").filter(has_text=order_id)).to_be_visible()

    def get_order_count(self) -> int:
        """Get the count of orders in the table."""
        rows = self.page.locator("tbody tr")
        return rows.count()

    def expect_stats_visible(self) -> None:
        """Assert stats cards are visible."""
        expect(self.page.locator("[data-stat], .stat-card").first).to_be_visible()

    def get_draft_count(self) -> str | None:
        """Get draft orders count."""
        stat = self.page.locator("[data-stat='draft']").first
        if stat.is_visible():
            return stat.inner_text()
        return None
