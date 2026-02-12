"""Service order detail page object."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.base_page import BasePage


class ServiceOrderDetailPage(BasePage):
    """Page object for the service order detail page."""

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto(self, order_id: str = "") -> None:
        """Navigate to a specific service order's detail page."""
        super().goto(f"/admin/operations/service-orders/{order_id}")

    def expect_loaded(self) -> None:
        """Assert the detail page is loaded."""
        expect(self.page.locator(".order-detail, [data-testid='order-detail']").first).to_be_visible()

    def expect_order_status(self, status: str) -> None:
        """Assert the order has a specific status."""
        expect(self.page.get_by_text(status, exact=False)).to_be_visible()

    def click_submit(self) -> None:
        """Click the submit button."""
        self.page.get_by_role("button", name="Submit").click()

    def click_provision(self) -> None:
        """Click the provision button."""
        self.page.get_by_role("button", name="Provision").click()

    def click_complete(self) -> None:
        """Click the complete button."""
        self.page.get_by_role("button", name="Complete").click()

    def click_cancel(self) -> None:
        """Click the cancel order button."""
        self.page.get_by_role("button", name="Cancel Order").click()

    def expect_appointments_section(self) -> None:
        """Assert appointments section is visible."""
        expect(self.page.get_by_text("Appointments")).to_be_visible()

    def expect_tasks_section(self) -> None:
        """Assert tasks section is visible."""
        expect(self.page.get_by_text("Tasks")).to_be_visible()

    def click_add_appointment(self) -> None:
        """Click to add an appointment."""
        self.page.get_by_role("button", name="Add Appointment").click()

    def click_add_task(self) -> None:
        """Click to add a task."""
        self.page.get_by_role("button", name="Add Task").click()
