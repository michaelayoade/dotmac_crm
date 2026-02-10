"""Customer portal page objects."""

from __future__ import annotations

from tests.playwright.pages.customer.dashboard_page import CustomerDashboardPage
from tests.playwright.pages.customer.profile_page import CustomerProfilePage
from tests.playwright.pages.customer.support_page import CustomerSupportPage
from tests.playwright.pages.customer.ticket_page import CustomerTicketPage

__all__ = [
    "CustomerDashboardPage",
    "CustomerProfilePage",
    "CustomerSupportPage",
    "CustomerTicketPage",
]
