from __future__ import annotations

from playwright.sync_api import Page, expect


class AdminTicketDetailPage:
    def __init__(self, page: Page) -> None:
        self.page = page

    def expect_loaded(self) -> None:
        expect(self.page.locator("h1", has_text="Ticket #")).to_be_visible()

    def expect_title(self, title: str) -> None:
        expect(self.page.get_by_text(title)).to_be_visible()

    def _status_badge(self):
        return self.page.locator("h1", has_text="Ticket #").locator("xpath=following-sibling::span[1]")

    def expect_status(self, label: str) -> None:
        expect(self._status_badge()).to_have_text(label)

    def expect_assigned_to(self, agent_name: str) -> None:
        expect(self.page.get_by_text(agent_name)).to_be_visible()

    def add_comment(self, body: str, internal: bool = False) -> None:
        self.page.locator("textarea[name='body']").fill(body)
        if internal:
            self.page.locator("input[name='is_internal']").check()
        self.page.get_by_role("button", name="Post Comment").click()

    def expect_comment(self, body: str) -> None:
        expect(self.page.get_by_text(body)).to_be_visible()

    def update_status(self, status_value: str) -> None:
        self.page.locator(
            "form",
            has=self.page.get_by_role("button", name="Update Status"),
        ).locator("select[name='status']").select_option(value=status_value)
        self.page.get_by_role("button", name="Update Status").click()

    def update_priority(self, priority_value: str) -> None:
        self.page.locator(
            "form",
            has=self.page.get_by_role("button", name="Update Priority"),
        ).locator("select[name='priority']").select_option(value=priority_value)
        self.page.get_by_role("button", name="Update Priority").click()
