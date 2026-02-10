"""Work orders list page object."""

from __future__ import annotations

from playwright.sync_api import Page, expect
from tests.playwright.pages.base_page import BasePage


class WorkOrdersPage(BasePage):
    """Page object for the work orders list page."""

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto(self, path: str = "") -> None:
        """Navigate to the work orders list."""
        super().goto("/admin/operations/work-orders")

    def expect_loaded(self) -> None:
        """Assert the work orders page is loaded."""
        expect(self.page.get_by_role("heading", name="Work Orders", exact=True)).to_be_visible()

    def filter_by_status(self, status: str) -> None:
        """Filter by order status."""
        self.page.get_by_label("Status").select_option(status)

    def filter_by_technician(self, technician: str) -> None:
        """Filter by assigned technician."""
        self.page.get_by_label("Technician").select_option(technician)

    def click_new_work_order(self) -> None:
        """Click new work order button."""
        self.page.get_by_role("link", name="New").first.click()

    def click_work_order_row(self, order_id: str) -> None:
        """Click on a work order row."""
        self.page.get_by_role("row").filter(has_text=order_id).click()

    def expect_order_in_list(self, order_id: str) -> None:
        """Assert a work order is visible in the list."""
        expect(self.page.get_by_role("row").filter(has_text=order_id)).to_be_visible()

    def get_order_count(self) -> int:
        """Get the count of work orders in the table."""
        rows = self.page.locator("tbody tr")
        return rows.count()
