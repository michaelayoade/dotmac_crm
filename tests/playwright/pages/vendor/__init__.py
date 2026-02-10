"""Vendor portal page objects."""

from __future__ import annotations

from tests.playwright.pages.vendor.as_built_page import VendorAsBuiltPage
from tests.playwright.pages.vendor.dashboard_page import VendorDashboardPage
from tests.playwright.pages.vendor.login_page import VendorLoginPage
from tests.playwright.pages.vendor.projects_page import VendorProjectsPage
from tests.playwright.pages.vendor.quote_builder_page import VendorQuoteBuilderPage

__all__ = [
    "VendorAsBuiltPage",
    "VendorDashboardPage",
    "VendorLoginPage",
    "VendorProjectsPage",
    "VendorQuoteBuilderPage",
]
