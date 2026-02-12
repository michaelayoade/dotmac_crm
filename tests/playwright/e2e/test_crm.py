"""CRM/Inbox e2e tests."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.admin.crm import ConversationPage, InboxPage


class TestCRMInbox:
    """Tests for the CRM inbox page."""

    def test_inbox_page_loads(self, admin_page: Page, settings):
        """Inbox page should load successfully."""
        page = InboxPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()

    def test_inbox_shows_conversations(self, admin_page: Page, settings):
        """Inbox should show conversations list."""
        page = InboxPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        page.expect_conversations_visible()

    def test_inbox_search(self, admin_page: Page, settings):
        """Should be able to search conversations."""
        page = InboxPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # Search input should be available
        expect(admin_page.get_by_placeholder("Search")).to_be_visible()

    def test_inbox_filter_options(self, admin_page: Page, settings):
        """Filter options should be visible."""
        page = InboxPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # Filter elements should be present
        expect(admin_page.get_by_text("All", exact=False).first).to_be_visible()


class TestCRMConversation:
    """Tests for the CRM conversation view."""

    def test_conversation_thread_visible(self, admin_page: Page, settings):
        """Conversation thread should be visible when a conversation is selected."""
        inbox = InboxPage(admin_page, settings.base_url)
        inbox.goto()
        inbox.expect_loaded()
        # If there are conversations, the thread should be visible
        ConversationPage(admin_page, settings.base_url)
        # Thread area should exist
        expect(
            admin_page.locator("[data-testid='message-thread']")
            .or_(admin_page.locator(".message-thread").or_(admin_page.get_by_role("textbox")))
            .first
        ).to_be_visible()

    def test_reply_input_visible(self, admin_page: Page, settings):
        """Reply input should be visible."""
        inbox = InboxPage(admin_page, settings.base_url)
        inbox.goto()
        inbox.expect_loaded()
        # Reply input should exist
        expect(admin_page.get_by_role("textbox").or_(admin_page.get_by_placeholder("message")).first).to_be_visible()


class TestCRMAPI:
    """API-level tests for CRM."""

    def test_list_conversations_api(self, api_context, admin_token):
        """API should return conversations list."""
        from tests.playwright.helpers.api import api_get, bearer_headers

        response = api_get(
            api_context,
            "/api/v1/crm/conversations?limit=10",
            headers=bearer_headers(admin_token),
        )
        assert response.status == 200
        data = response.json()
        assert "items" in data or isinstance(data, list)

    def test_list_contacts_api(self, api_context, admin_token):
        """API should return contacts list."""
        from tests.playwright.helpers.api import api_get, bearer_headers

        response = api_get(
            api_context,
            "/api/v1/crm/contacts?limit=10",
            headers=bearer_headers(admin_token),
        )
        assert response.status == 200
        data = response.json()
        assert "items" in data or isinstance(data, list)
