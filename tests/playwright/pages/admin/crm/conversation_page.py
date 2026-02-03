"""CRM Conversation detail page object."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.base_page import BasePage


class ConversationPage(BasePage):
    """Page object for the conversation detail view."""

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto(self, conversation_id: str = "") -> None:
        """Navigate to a specific conversation."""
        super().goto(f"/admin/crm/inbox?conversation_id={conversation_id}")

    def expect_loaded(self) -> None:
        """Assert the conversation view is loaded."""
        expect(self.page.locator("[data-testid='message-thread']").or_(
            self.page.locator(".message-thread").or_(
                self.page.get_by_role("textbox")
            )
        ).first).to_be_visible()

    def expect_messages_visible(self) -> None:
        """Assert messages are visible."""
        expect(self.page.locator("[data-testid='message']").or_(
            self.page.locator(".message")
        ).first).to_be_visible()

    def get_message_count(self) -> int:
        """Get count of messages in the thread."""
        messages = self.page.locator("[data-testid='message']").or_(
            self.page.locator(".message")
        )
        return messages.count()

    def send_reply(self, message: str) -> None:
        """Send a reply message."""
        self.page.get_by_placeholder("message").or_(
            self.page.get_by_role("textbox")
        ).first.fill(message)
        self.page.get_by_role("button", name="Send").first.click()

    def expect_message_sent(self, message_text: str) -> None:
        """Assert a message was sent."""
        expect(self.page.get_by_text(message_text)).to_be_visible()

    def change_status(self, status: str) -> None:
        """Change conversation status."""
        button = self.page.get_by_role("button", name=status)
        if button.count():
            button.first.click()
        else:
            self.page.get_by_label("Status").select_option(status)

    def expect_contact_details_visible(self) -> None:
        """Assert contact details sidebar is visible."""
        expect(self.page.locator("[data-testid='contact-details']").or_(
            self.page.get_by_text("Contact", exact=False)
        ).first).to_be_visible()

    def get_contact_name(self) -> str:
        """Get contact name from details."""
        name_element = self.page.locator("[data-testid='contact-name']").or_(
            self.page.get_by_role("heading").first
        )
        return name_element.text_content() or ""

    def get_contact_email(self) -> str:
        """Get contact email from details."""
        email_element = self.page.locator("[data-testid='contact-email']").or_(
            self.page.get_by_text("@")
        ).first
        return email_element.text_content() or ""

    def add_note(self, note: str) -> None:
        """Add a note to the contact."""
        self.page.get_by_role("button", name="Note").first.click()
        self.page.get_by_placeholder("note").or_(
            self.page.get_by_role("textbox")
        ).first.fill(note)
        self.page.get_by_role("button", name="Save").first.click()

    def assign_to(self, agent_name: str) -> None:
        """Assign conversation to an agent."""
        self.page.get_by_role("button", name="Assign").first.click()
        self.page.get_by_text(agent_name).click()

    def mark_as_resolved(self) -> None:
        """Mark conversation as resolved."""
        self.page.get_by_role("button", name="Resolve").or_(
            self.page.get_by_role("button", name="Close")
        ).first.click()

    def snooze(self) -> None:
        """Snooze the conversation."""
        self.page.get_by_role("button", name="Snooze").first.click()
