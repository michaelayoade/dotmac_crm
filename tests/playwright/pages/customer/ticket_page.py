"""Customer portal ticket detail and form page object."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.base_page import BasePage


class CustomerTicketPage(BasePage):
    """Page object for the customer ticket detail/form page."""

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto_new(self) -> None:
        """Navigate to new ticket form."""
        super().goto("/customer/support/tickets/new")

    def goto_detail(self, ticket_id: str) -> None:
        """Navigate to ticket detail page."""
        super().goto(f"/customer/support/tickets/{ticket_id}")

    def expect_form_loaded(self) -> None:
        """Assert the ticket form is loaded."""
        expect(
            self.page.get_by_role("heading", name="Ticket", exact=True).or_(self.page.get_by_label("Subject")).first
        ).to_be_visible()

    def expect_detail_loaded(self) -> None:
        """Assert the ticket detail page is loaded."""
        expect(self.page.get_by_role("heading", name="Ticket", exact=True)).to_be_visible()

    def fill_subject(self, subject: str) -> None:
        """Fill ticket subject."""
        self.page.get_by_label("Subject").fill(subject)

    def fill_description(self, description: str) -> None:
        """Fill ticket description."""
        self.page.get_by_label("Description").or_(self.page.locator("textarea")).first.fill(description)

    def select_category(self, category: str) -> None:
        """Select ticket category."""
        self.page.get_by_label("Category").select_option(category)

    def select_priority(self, priority: str) -> None:
        """Select ticket priority."""
        self.page.get_by_label("Priority").select_option(priority)

    def submit_ticket(self) -> None:
        """Submit the ticket."""
        self.page.get_by_role("button", name="Submit").or_(self.page.get_by_role("button", name="Create")).first.click()

    def expect_ticket_created(self) -> None:
        """Assert ticket was created successfully."""
        expect(
            self.page.get_by_text("created", exact=False).or_(self.page.get_by_text("submitted", exact=False)).first
        ).to_be_visible()

    def add_comment(self, comment: str) -> None:
        """Add a comment to the ticket."""
        self.page.get_by_placeholder("comment").or_(self.page.locator("textarea")).first.fill(comment)
        self.page.get_by_role("button", name="Add").or_(self.page.get_by_role("button", name="Send")).first.click()

    def expect_comment_visible(self, comment_text: str) -> None:
        """Assert a comment is visible."""
        expect(self.page.get_by_text(comment_text)).to_be_visible()

    def get_ticket_status(self) -> str:
        """Get ticket status."""
        status_element = (
            self.page.locator("[data-testid='ticket-status']")
            .or_(self.page.get_by_text("Open").or_(self.page.get_by_text("Closed")))
            .first
        )
        return status_element.text_content() or ""

    def close_ticket(self) -> None:
        """Close the ticket."""
        self.page.get_by_role("button", name="Close").first.click()

    def reopen_ticket(self) -> None:
        """Reopen the ticket."""
        self.page.get_by_role("button", name="Reopen").first.click()

    def upload_attachment(self, file_path: str) -> None:
        """Upload an attachment."""
        self.page.get_by_label("Attach").or_(self.page.locator("input[type='file']")).first.set_input_files(file_path)
