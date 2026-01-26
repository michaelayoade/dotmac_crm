from __future__ import annotations

from playwright.sync_api import Page, expect


class AdminTicketFormPage:
    def __init__(self, page: Page) -> None:
        self.page = page

    def expect_loaded(self) -> None:
        expect(self.page.get_by_role("heading", name="Create Support Ticket", exact=True)).to_be_visible()

    def fill_title(self, title: str) -> None:
        self.page.get_by_label("Title").fill(title)

    def fill_description(self, description: str) -> None:
        self.page.get_by_label("Description").fill(description)

    def assign_to(self, agent_name: str) -> None:
        self.page.get_by_label("Assigned To").select_option(label=agent_name)

    def submit_create(self) -> None:
        self.page.get_by_role("button", name="Create Ticket").click()

    def create_ticket(self, title: str, description: str, agent_name: str | None = None) -> None:
        self.fill_title(title)
        self.fill_description(description)
        if agent_name:
            self.assign_to(agent_name)
        self.submit_create()
