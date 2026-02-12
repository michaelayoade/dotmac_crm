import asyncio
import glob
import os

import pytest
from playwright.sync_api import sync_playwright


def _base_url() -> str:
    return os.getenv("PLAYWRIGHT_BASE_URL", "http://localhost:8000").rstrip("/")


def _browser_name() -> str:
    return os.getenv("PLAYWRIGHT_BROWSER", "chromium").lower()


def _chromium_executable() -> str | None:
    override = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if override:
        return override
    candidates = glob.glob(
        "/root/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"
    )
    return candidates[0] if candidates else None


def test_admin_tickets_create_form_loads():
    base_url = _base_url()
    browser_name = _browser_name()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        pytest.skip("Playwright sync API cannot run inside an active asyncio loop.")
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name, playwright.chromium)
        launch_kwargs = {"timeout": 10000}
        if browser_type is playwright.chromium:
            chromium_exec = _chromium_executable()
            if chromium_exec:
                launch_kwargs["executable_path"] = chromium_exec
            # Disable Chromium sandboxing to run in restricted environments (e.g., containers).
            launch_kwargs.update(
                {
                    "chromium_sandbox": False,
                    "headless": True,
                    "args": [
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                    ],
                }
            )
        try:
            browser = browser_type.launch(**launch_kwargs)
        except Exception as exc:
            message = str(exc)
            if "Operation not permitted" in message or "sandbox" in message or "X server" in message:
                pytest.skip(f"Playwright browser launch blocked in this environment: {message}")
            raise
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.set_default_navigation_timeout(10000)

        page.goto(f"{base_url}/admin/support/tickets", wait_until="domcontentloaded")
        page.locator("a[href='/admin/support/tickets/create']").first.click()
        page.wait_for_url("**/admin/support/tickets/create", timeout=10000)

        page.wait_for_selector("form")
        page.wait_for_selector("input[name='title']")
        page.wait_for_selector("textarea[name='description']")

        context.close()
        browser.close()
