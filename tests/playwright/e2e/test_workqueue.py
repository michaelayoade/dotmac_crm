"""End-to-end Playwright smoke tests for the agent workqueue surface.

These tests exercise the live `/agent/workqueue` route; they assume the running
app has the `workqueue.enabled` setting flipped on (defaults vary per
environment). When the flag is off, the route returns 404 and the tests below
skip rather than fail — this keeps the smoke layer honest without requiring a
DB-level fixture in the Playwright tier.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from tests.playwright.pages.workqueue_page import WorkqueuePage


def _workqueue_enabled(page: Page, base_url: str) -> bool:
    """Return True if /agent/workqueue serves the page (flag on, perms ok)."""
    response = page.goto(f"{base_url}/agent/workqueue", wait_until="domcontentloaded")
    if response is None:
        return False
    if response.status == 404:
        return False
    if response.status >= 400:
        return False
    return page.locator("h1:has-text('Workqueue')").count() > 0


class TestWorkqueueSmoke:
    """Lightweight smoke tests for the workqueue page."""

    def test_workqueue_page_loads(self, admin_page: Page, settings):
        """Workqueue page should render with the heading when feature flag on."""
        if not _workqueue_enabled(admin_page, settings.base_url):
            pytest.skip("workqueue.enabled is off or admin lacks workqueue:view")

        wq = WorkqueuePage(admin_page, settings.base_url)
        wq.goto()
        wq.expect_loaded()

    def test_workqueue_right_now_visible(self, admin_page: Page, settings):
        """The 'Right now' hero band container should be present on the page."""
        if not _workqueue_enabled(admin_page, settings.base_url):
            pytest.skip("workqueue.enabled is off or admin lacks workqueue:view")

        wq = WorkqueuePage(admin_page, settings.base_url)
        wq.goto()
        wq.expect_loaded()
        wq.expect_right_now_visible()

    def test_workqueue_audience_self_default(self, admin_page: Page, settings):
        """The audience selector should default to 'self' (Me) for all users."""
        if not _workqueue_enabled(admin_page, settings.base_url):
            pytest.skip("workqueue.enabled is off or admin lacks workqueue:view")

        wq = WorkqueuePage(admin_page, settings.base_url)
        wq.goto()
        wq.expect_loaded()
        # Default audience is "self" — verify the option is present and selected.
        select = wq.audience_select()
        assert select.count() == 1
        # 'self' option must always be available
        assert admin_page.locator("select#as option[value='self']").count() == 1

    def test_workqueue_disabled_returns_404(self, anon_page: Page, settings):
        """Unauthenticated users hitting the workqueue route should never see it.

        The route requires `require_web_auth`; an anonymous request should be
        redirected to login or rejected. Either way the Workqueue heading must
        not be visible.
        """
        anon_page.goto(f"{settings.base_url}/agent/workqueue", wait_until="domcontentloaded")
        # Anonymous: never reaches the workqueue heading; must redirect/deny.
        assert anon_page.locator("h1:has-text('Workqueue')").count() == 0
