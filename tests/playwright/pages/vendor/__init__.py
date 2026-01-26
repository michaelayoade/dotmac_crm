"""Vendor portal page objects."""

from __future__ import annotations

from tests.playwright.pages.vendor.dashboard_page import VendorDashboardPage
from tests.playwright.pages.vendor.login_page import VendorLoginPage
from tests.playwright.pages.vendor.projects_page import VendorProjectsPage
from tests.playwright.pages.vendor.quote_builder_page import VendorQuoteBuilderPage
from tests.playwright.pages.vendor.as_built_page import VendorAsBuiltPage

__all__ = [
    "VendorDashboardPage",
    "VendorLoginPage",
    "VendorProjectsPage",
    "VendorQuoteBuilderPage",
    "VendorAsBuiltPage",
]
