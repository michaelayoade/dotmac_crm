"""Admin page objects."""

from tests.playwright.pages.admin.dashboard_page import AdminDashboardPage
from tests.playwright.pages.admin.login_page import AdminLoginPage
from tests.playwright.pages.admin.ticket_detail_page import AdminTicketDetailPage
from tests.playwright.pages.admin.ticket_form_page import AdminTicketFormPage
from tests.playwright.pages.admin.tickets_page import AdminTicketsPage

__all__ = [
    "AdminDashboardPage",
    "AdminLoginPage",
    "AdminTicketDetailPage",
    "AdminTicketFormPage",
    "AdminTicketsPage",
]
