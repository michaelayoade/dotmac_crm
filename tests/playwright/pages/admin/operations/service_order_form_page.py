"""Service order form page object."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.base_page import BasePage


class ServiceOrderFormPage(BasePage):
    """Page object for the service order create/edit form."""

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto_new(self) -> None:
        """Navigate to the new service order form."""
        super().goto("/admin/operations/service-orders/new")

    def goto_edit(self, order_id: str) -> None:
        """Navigate to edit a specific service order."""
        super().goto(f"/admin/operations/service-orders/{order_id}/edit")

    def expect_loaded(self) -> None:
        """Assert the form is loaded."""
        expect(self.page.locator("form")).to_be_visible()

    def select_account(self, account_label: str) -> None:
        """Select a billing account."""
        self.page.get_by_label("Account").select_option(label=account_label)

    def select_subscription(self, subscription_label: str) -> None:
        """Select a subscription."""
        self.page.get_by_label("Subscription").select_option(label=subscription_label)

    def select_status(self, status: str) -> None:
        """Select order status."""
        self.page.get_by_label("Status").select_option(status)

    def fill_notes(self, notes: str) -> None:
        """Fill the notes field."""
        self.page.get_by_label("Notes").fill(notes)

    def submit(self) -> None:
        """Submit the form."""
        self.page.get_by_role("button", name="Create").click()

    def cancel(self) -> None:
        """Cancel and go back."""
        self.page.get_by_role("link", name="Cancel").click()

    def expect_error(self, message: str) -> None:
        """Assert an error message is displayed."""
        expect(self.page.locator(".text-red-500, .text-red-700, .error").filter(has_text=message)).to_be_visible()
