from __future__ import annotations

import uuid

from tests.playwright.pages.admin.ticket_detail_page import AdminTicketDetailPage
from tests.playwright.pages.admin.ticket_form_page import AdminTicketFormPage
from tests.playwright.pages.admin.tickets_page import AdminTicketsPage


def test_ticket_full_lifecycle(admin_page, settings, test_identities: dict):
    agent = test_identities["agent"]
    agent_name = f"{agent['first_name']} {agent['last_name']}"

    tickets_page = AdminTicketsPage(admin_page, settings.base_url)
    tickets_page.goto()
    tickets_page.open_create_ticket()

    form_page = AdminTicketFormPage(admin_page)
    form_page.expect_loaded()

    ticket_title = f"E2E Ticket {uuid.uuid4().hex[:8]}"
    form_page.create_ticket(
        title=ticket_title,
        description="Customer reports intermittent connectivity.",
        agent_name=agent_name,
    )

    admin_page.wait_for_url("**/admin/support/tickets/**")
    detail_page = AdminTicketDetailPage(admin_page)
    detail_page.expect_loaded()
    detail_page.expect_title(ticket_title)
    detail_page.expect_assigned_to(agent_name)

    detail_page.add_comment("Agent acknowledged the issue.")
    detail_page.expect_comment("Agent acknowledged the issue.")

    detail_page.update_status("open")
    detail_page.expect_status("Open")

    detail_page.update_status("resolved")
    detail_page.expect_status("Resolved")

    detail_page.update_status("closed")
    detail_page.expect_status("Closed")

    detail_page.update_priority("urgent")
