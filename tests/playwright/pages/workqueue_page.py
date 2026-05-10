"""Page object for the agent workqueue surface."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.base_page import BasePage


class WorkqueuePage(BasePage):
    """Page object for /agent/workqueue."""

    PATH = "/agent/workqueue"

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto(self, audience: str | None = None) -> None:
        """Navigate to the workqueue page (optionally with audience query)."""
        url = f"{self.base_url}{self.PATH}"
        if audience:
            url = f"{url}?as={audience}"
        self.page.goto(url, wait_until="domcontentloaded")

    def expect_loaded(self) -> None:
        """Assert the workqueue page is loaded."""
        expect(self.page.get_by_role("heading", name="Workqueue", exact=True)).to_be_visible()

    def expect_disabled(self) -> None:
        """Assert the workqueue heading is NOT present (flag-off path)."""
        expect(self.page.get_by_role("heading", name="Workqueue", exact=True)).to_have_count(0)

    def expect_right_now_visible(self) -> None:
        """Assert the 'Right now' hero band container is visible."""
        expect(self.page.locator("#workqueue-right-now")).to_be_visible()

    def right_now_item_count(self) -> int:
        """Count of items currently rendered in the Right Now band."""
        return self.page.locator("#workqueue-right-now article").count()

    def section_headings(self) -> list[str]:
        """All per-kind section headings currently rendered."""
        return self.page.locator("section[id^='workqueue-section-'] h3").all_inner_texts()

    def audience_select(self):
        """Locator for the audience scope select."""
        return self.page.locator("select#as")

    def select_audience(self, value: str) -> None:
        """Change audience scope; the form auto-submits via onchange."""
        self.audience_select().select_option(value=value)
        self.page.wait_for_load_state("domcontentloaded")
