"""CRM Inbox page object."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.base_page import BasePage


class InboxPage(BasePage):
    """Page object for the CRM inbox page."""

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto(self, path: str = "") -> None:
        """Navigate to the inbox."""
        super().goto("/admin/crm/inbox")

    def expect_loaded(self) -> None:
        """Assert the inbox is loaded."""
        expect(
            self.page.locator("[data-testid='inbox']")
            .or_(self.page.get_by_text("Inbox", exact=False).or_(self.page.get_by_text("Conversation", exact=False)))
            .first
        ).to_be_visible()

    def expect_conversations_visible(self) -> None:
        """Assert conversations list is visible."""
        expect(
            self.page.locator("[data-testid='conversations']")
            .or_(self.page.locator(".conversation-list").or_(self.page.get_by_role("list")))
            .first
        ).to_be_visible()

    def get_conversation_count(self) -> int:
        """Get count of conversations displayed."""
        conversations = self.page.locator("[data-testid='conversation-item']").or_(
            self.page.locator(".conversation-item")
        )
        return conversations.count()

    def click_conversation(self, contact_name: str) -> None:
        """Click on a conversation."""
        self.page.get_by_text(contact_name).first.click()

    def search_conversations(self, query: str) -> None:
        """Search conversations."""
        search_input = self.page.get_by_placeholder("Search")
        search_input.fill(query)
        self.page.keyboard.press("Enter")

    def filter_by_channel(self, channel: str) -> None:
        """Filter by channel type."""
        button = self.page.get_by_role("button", name=channel)
        if button.count():
            button.first.click()
        else:
            self.page.get_by_label("Channel").select_option(channel)

    def filter_by_status(self, status: str) -> None:
        """Filter by conversation status."""
        button = self.page.get_by_role("button", name=status)
        if button.count():
            button.first.click()
        else:
            self.page.get_by_label("Status").select_option(status)

    def get_unread_count(self) -> str:
        """Get unread count."""
        unread_element = (
            self.page.locator("[data-testid='unread-count']").or_(self.page.get_by_text("unread", exact=False)).first
        )
        return unread_element.text_content() or "0"

    def expect_open_count(self, count: int) -> None:
        """Assert count of open conversations."""
        expect(
            self.page.get_by_text(f"{count}").or_(self.page.locator("[data-testid='open-count']")).first
        ).to_be_visible()

    def create_new_conversation(self) -> None:
        """Create a new conversation."""
        self.page.get_by_role("button", name="New").or_(self.page.get_by_role("link", name="New")).first.click()
