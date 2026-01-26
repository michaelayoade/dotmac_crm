from __future__ import annotations

from playwright.sync_api import Page, expect


class AdminTicketsPage:
    def __init__(self, page: Page, base_url: str) -> None:
        self.page = page
        self.base_url = base_url

    def goto(self) -> None:
        self.page.goto(f"{self.base_url}/admin/support/tickets", wait_until="domcontentloaded")

    def expect_loaded(self) -> None:
        expect(self.page.get_by_role("heading", name="Support Tickets", exact=True)).to_be_visible()

    def open_create_ticket(self) -> None:
        self.page.locator("a[href='/admin/support/tickets/create']").first.click()
        self.page.wait_for_url("**/admin/support/tickets/create")
