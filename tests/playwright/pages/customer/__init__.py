"""Customer portal page objects."""

from __future__ import annotations

from tests.playwright.pages.customer.dashboard_page import CustomerDashboardPage
from tests.playwright.pages.customer.support_page import CustomerSupportPage
from tests.playwright.pages.customer.ticket_page import CustomerTicketPage
from tests.playwright.pages.customer.profile_page import CustomerProfilePage

__all__ = [
    "CustomerDashboardPage",
    "CustomerSupportPage",
    "CustomerTicketPage",
    "CustomerProfilePage",
]
