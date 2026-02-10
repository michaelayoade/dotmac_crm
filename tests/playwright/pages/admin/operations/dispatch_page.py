"""Dispatch page object."""

from __future__ import annotations

from playwright.sync_api import Page, expect
from tests.playwright.pages.base_page import BasePage


class DispatchPage(BasePage):
    """Page object for the dispatch management page."""

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto(self, path: str = "") -> None:
        """Navigate to the dispatch page."""
        super().goto("/admin/operations/dispatch")

    def expect_loaded(self) -> None:
        """Assert the dispatch page is loaded."""
        expect(self.page.get_by_role("heading", name="Dispatch", exact=True)).to_be_visible()

    def select_date(self, date: str) -> None:
        """Select a date for dispatch view."""
        self.page.get_by_label("Date").fill(date)

    def select_technician(self, technician: str) -> None:
        """Filter by technician."""
        self.page.get_by_label("Technician").select_option(technician)

    def expect_calendar_visible(self) -> None:
        """Assert the dispatch calendar is visible."""
        expect(self.page.locator(".calendar, .dispatch-board, [data-testid='calendar']").first).to_be_visible()

    def expect_technician_list_visible(self) -> None:
        """Assert the technician list is visible."""
        expect(self.page.get_by_text("Technicians")).to_be_visible()

    def click_job(self, job_id: str) -> None:
        """Click on a scheduled job."""
        self.page.locator(f"[data-job-id='{job_id}']").click()

    def drag_job_to_slot(self, job_id: str, target_slot: str) -> None:
        """Drag a job to a time slot (for scheduling)."""
        source = self.page.locator(f"[data-job-id='{job_id}']")
        target = self.page.locator(f"[data-slot='{target_slot}']")
        source.drag_to(target)

    def get_scheduled_jobs_count(self) -> int:
        """Get the count of scheduled jobs."""
        jobs = self.page.locator("[data-job-id]")
        return jobs.count()
