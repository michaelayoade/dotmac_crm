"""Customer portal support page object."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.base_page import BasePage


class CustomerSupportPage(BasePage):
    """Page object for the customer support page."""

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto(self, path: str = "") -> None:
        """Navigate to the support page."""
        super().goto("/customer/support")

    def expect_loaded(self) -> None:
        """Assert the support page is loaded."""
        expect(self.page.get_by_role("heading", name="Support", exact=True)).to_be_visible()

    def expect_tickets_visible(self) -> None:
        """Assert tickets section is visible."""
        expect(self.page.get_by_text("Ticket", exact=False).first).to_be_visible()

    def click_new_ticket(self) -> None:
        """Click new ticket button."""
        self.page.get_by_role("button", name="New").or_(
            self.page.get_by_role("link", name="New")
        ).first.click()

    def get_ticket_count(self) -> int:
        """Get count of tickets displayed."""
        rows = self.page.locator("table tbody tr").or_(
            self.page.locator("[data-testid='ticket-item']")
        )
        return rows.count()

    def click_ticket_row(self, ticket_id: str) -> None:
        """Click on a ticket to view details."""
        self.page.get_by_role("row").filter(has_text=ticket_id).click()

    def expect_ticket_in_list(self, ticket_id: str) -> None:
        """Assert a ticket is visible in the list."""
        expect(self.page.get_by_text(ticket_id)).to_be_visible()

    def filter_by_status(self, status: str) -> None:
        """Filter tickets by status."""
        self.page.get_by_label("Status").select_option(status)

    def search_tickets(self, query: str) -> None:
        """Search tickets."""
        search_input = self.page.get_by_placeholder("Search")
        search_input.fill(query)
        self.page.keyboard.press("Enter")

    def view_faq(self) -> None:
        """View FAQ section."""
        self.page.get_by_role("link", name="FAQ").first.click()

    def contact_support(self) -> None:
        """Open contact support form."""
        self.page.get_by_role("button", name="Contact").or_(
            self.page.get_by_role("link", name="Contact")
        ).first.click()
