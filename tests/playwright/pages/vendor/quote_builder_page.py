"""Vendor portal quote builder page object."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.base_page import BasePage


class VendorQuoteBuilderPage(BasePage):
    """Page object for the vendor quote builder page."""

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto(self, project_id: str) -> None:
        """Navigate to quote builder for a project."""
        super().goto(f"/vendor/quotes/builder?project_id={project_id}")

    def expect_loaded(self) -> None:
        """Assert the quote builder is loaded."""
        expect(self.page.get_by_role("heading", name="Quote", exact=True)).to_be_visible()

    def fill_labor_cost(self, amount: str) -> None:
        """Fill labor cost."""
        self.page.get_by_label("Labor").fill(amount)

    def fill_materials_cost(self, amount: str) -> None:
        """Fill materials cost."""
        self.page.get_by_label("Material").fill(amount)

    def fill_equipment_cost(self, amount: str) -> None:
        """Fill equipment cost."""
        self.page.get_by_label("Equipment").fill(amount)

    def fill_notes(self, notes: str) -> None:
        """Fill quote notes."""
        self.page.get_by_label("Notes").or_(
            self.page.locator("textarea")
        ).first.fill(notes)

    def add_line_item(self, description: str, quantity: str, unit_price: str) -> None:
        """Add a line item to the quote."""
        self.page.get_by_role("button", name="Add").first.click()
        # Fill the new line item fields
        rows = self.page.locator("[data-testid='line-item']").or_(
            self.page.locator(".line-item")
        )
        last_row = rows.last
        last_row.get_by_label("Description").fill(description)
        last_row.get_by_label("Quantity").fill(quantity)
        last_row.get_by_label("Price").fill(unit_price)

    def submit_quote(self) -> None:
        """Submit the quote."""
        self.page.get_by_role("button", name="Submit").first.click()

    def save_draft(self) -> None:
        """Save quote as draft."""
        self.page.get_by_role("button", name="Save").or_(
            self.page.get_by_role("button", name="Draft")
        ).first.click()

    def expect_quote_submitted(self) -> None:
        """Assert quote was submitted successfully."""
        expect(self.page.get_by_text("submitted", exact=False).or_(
            self.page.get_by_text("success", exact=False)
        ).first).to_be_visible()

    def get_total(self) -> str:
        """Get quote total."""
        total_element = self.page.locator("[data-testid='quote-total']").or_(
            self.page.get_by_text("Total", exact=False)
        ).first
        return total_element.text_content() or ""
