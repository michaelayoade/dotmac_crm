"""Strategic workflow smoke tests for sales, CRM reports, and map consolidation."""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect


def test_sales_orders_dashboard_filters_and_sections(admin_page: Page, settings) -> None:
    admin_page.goto(f"{settings.base_url}/admin/crm/sales/orders?period_days=30")
    admin_page.wait_for_load_state("domcontentloaded")

    expect(admin_page).to_have_url(re.compile(r"/admin/operations/sales-orders"))
    expect(admin_page.get_by_role("heading", name="Sales Orders", exact=True)).to_be_visible()

    for selector in [
        "input[name='search']",
        "select[name='period_days']",
        "input[name='start_date']",
        "input[name='end_date']",
        "select[name='source_type']",
        "select[name='status']",
        "select[name='payment_status']",
        "select[name='owner_agent_id']",
        "select[name='lead_source']",
    ]:
        expect(admin_page.locator(selector)).to_be_visible()

    expect(admin_page.get_by_text("Gross Sales")).to_be_visible()
    expect(admin_page.get_by_text("Sales by Agent")).to_be_visible()
    expect(admin_page.get_by_text("Payment Mix")).to_be_visible()
    expect(admin_page.get_by_text("Source Mix")).to_be_visible()


def test_crm_performance_report_filters_and_summary(admin_page: Page, settings) -> None:
    admin_page.goto(f"{settings.base_url}/admin/reports/crm-performance?days=30")
    admin_page.wait_for_load_state("domcontentloaded")

    expect(admin_page.get_by_role("heading", name="CRM Performance")).to_be_visible()
    expect(admin_page.locator("form#crm-performance-filters")).to_be_visible()
    expect(admin_page.locator("input[name='start_date']")).to_be_visible()
    expect(admin_page.locator("input[name='end_date']")).to_be_visible()
    expect(admin_page.locator("select[name='team_id']")).to_be_visible()
    expect(admin_page.locator("select[name='agent_id']")).to_be_visible()
    expect(admin_page.locator("select[name='channel_type']")).to_be_visible()

    expect(admin_page.get_by_text("Total Conversations")).to_be_visible()
    expect(admin_page.get_by_text("Avg First Response")).to_be_visible()
    expect(admin_page.get_by_text("Messages by Channel")).to_be_visible()


def test_agent_my_performance_filters_and_scorecard_link(admin_page: Page, settings) -> None:
    admin_page.goto(f"{settings.base_url}/agent/my-performance?days=30")
    admin_page.wait_for_load_state("domcontentloaded")

    expect(admin_page.locator("h1").filter(has_text="My Performance")).to_be_visible()
    expect(admin_page.locator("select[name='days']")).to_be_visible()
    expect(admin_page.locator("input[name='start_date']")).to_be_visible()
    expect(admin_page.locator("input[name='end_date']")).to_be_visible()
    expect(admin_page.locator("select[name='channel_type']")).to_be_visible()
    expect(admin_page.get_by_role("link", name="Scorecard")).to_be_visible()

    expect(admin_page.get_by_text("Availability")).to_be_visible()
    expect(admin_page.get_by_text("Total Conversations")).to_be_visible()
    expect(admin_page.get_by_text("Sales Performance")).to_be_visible()


def test_legacy_live_maps_redirect_to_network_operations_map(admin_page: Page, settings) -> None:
    for legacy_path in ["/admin/crm/live-map", "/admin/operations/field-techs/map"]:
        admin_page.goto(f"{settings.base_url}{legacy_path}")
        admin_page.wait_for_load_state("domcontentloaded")

        expect(admin_page).to_have_url(re.compile(r"/admin/network/map$"))
        expect(admin_page.get_by_role("heading", name="Network Operations Map")).to_be_visible()
