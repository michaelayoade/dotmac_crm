"""Vendor portal as-built submission page object."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.base_page import BasePage


class VendorAsBuiltPage(BasePage):
    """Page object for the vendor as-built submission page."""

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto(self, project_id: str = "") -> None:
        """Navigate to as-built submission for a project."""
        super().goto(f"/vendor/as-built/submit?project_id={project_id}")

    def expect_loaded(self) -> None:
        """Assert the as-built form is loaded."""
        expect(self.page.get_by_role("heading", name="As-Built", exact=True).or_(
            self.page.get_by_role("heading", name="Built", exact=True)
        ).first).to_be_visible()

    def fill_completion_date(self, date: str) -> None:
        """Fill completion date."""
        self.page.get_by_label("Completion").or_(
            self.page.get_by_label("Date")
        ).first.fill(date)

    def fill_notes(self, notes: str) -> None:
        """Fill as-built notes."""
        self.page.get_by_label("Notes").or_(
            self.page.locator("textarea")
        ).first.fill(notes)

    def upload_photo(self, file_path: str) -> None:
        """Upload installation photo."""
        self.page.get_by_label("Photo").or_(
            self.page.locator("input[type='file']")
        ).first.set_input_files(file_path)

    def upload_document(self, file_path: str) -> None:
        """Upload supporting document."""
        self.page.get_by_label("Document").or_(
            self.page.locator("input[type='file']")
        ).first.set_input_files(file_path)

    def fill_fiber_length(self, length: str) -> None:
        """Fill fiber length installed."""
        self.page.get_by_label("Fiber Length").or_(
            self.page.get_by_label("Length")
        ).first.fill(length)

    def fill_ont_serial(self, serial: str) -> None:
        """Fill ONT serial number."""
        self.page.get_by_label("ONT Serial").or_(
            self.page.get_by_label("Serial")
        ).first.fill(serial)

    def submit_as_built(self) -> None:
        """Submit the as-built report."""
        self.page.get_by_role("button", name="Submit").first.click()

    def save_draft(self) -> None:
        """Save as-built as draft."""
        self.page.get_by_role("button", name="Save").or_(
            self.page.get_by_role("button", name="Draft")
        ).first.click()

    def expect_as_built_submitted(self) -> None:
        """Assert as-built was submitted successfully."""
        expect(self.page.get_by_text("submitted", exact=False).or_(
            self.page.get_by_text("success", exact=False)
        ).first).to_be_visible()
