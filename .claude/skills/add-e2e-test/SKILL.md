---
name: add-e2e-test
description: Scaffold a Playwright E2E test with page objects, fixtures, and test scenarios
arguments:
  - name: test_info
    description: "Page or flow to test (e.g. 'admin tickets list and detail pages')"
---

# Add E2E Test

Scaffold a Playwright end-to-end test for the DotMac Omni CRM.

## Steps

### 1. Understand the request
Parse `$ARGUMENTS` to determine:
- **Portal**: admin, customer, vendor, reseller
- **Pages to test**: list, detail, form, flow (e.g. create → detail → update)
- **Existing page objects**: check `tests/playwright/pages/admin/` for reusable POM classes

### 2. Study the existing patterns
Read these reference files to match conventions:

- **Base page**: `tests/playwright/pages/base_page.py` — all page objects extend this
- **Conftest**: `tests/playwright/conftest.py` — session-scoped auth fixtures, storage state
- **Admin page objects**: `tests/playwright/pages/admin/` — `dashboard_page.py`, `tickets_page.py`, `ticket_detail_page.py`
- **Helpers**: `tests/playwright/helpers/` — `api.py`, `auth.py`, `config.py`, `data.py`
- **Existing E2E tests**: `tests/playwright/e2e/` — test scenario patterns

### 3. Create page object (if needed)
Create `tests/playwright/pages/admin/{page_name}_page.py`:

```python
from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.base_page import BasePage


class Admin{Entity}Page(BasePage):
    """Page object for the admin {entity} list page."""

    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def goto(self, path: str = "") -> None:
        self.page.goto(f"{self.base_url}/admin/{route_path}", wait_until="domcontentloaded")

    def expect_loaded(self) -> None:
        expect(self.page.get_by_role("heading", name="Page Title", exact=True)).to_be_visible()

    # --- Accessors ---

    def expect_items_visible(self) -> None:
        """Assert the table has at least one row."""
        expect(self.page.locator("table tbody tr").first).to_be_visible()

    def expect_empty_state(self) -> None:
        """Assert the empty state message is shown."""
        expect(self.page.get_by_text("No items found")).to_be_visible()

    def open_create(self) -> None:
        self.page.locator("a[href='/admin/{route_path}/new']").first.click()
        self.page.wait_for_url("**/admin/{route_path}/new")

    def click_first_item(self) -> None:
        self.page.locator("table tbody tr a").first.click()
```

For detail pages:
```python
class Admin{Entity}DetailPage(BasePage):
    def __init__(self, page: Page, base_url: str) -> None:
        super().__init__(page, base_url)

    def expect_loaded(self) -> None:
        expect(self.page.locator("h1").first).to_be_visible()

    def expect_status(self, label: str) -> None:
        expect(self.page.get_by_text(label)).to_be_visible()

    def add_comment(self, body: str) -> None:
        self.page.locator("textarea[name='body']").fill(body)
        self.page.get_by_role("button", name="Post Comment").click()

    def expect_comment(self, body: str) -> None:
        expect(self.page.get_by_text(body)).to_be_visible()
```

### 4. Create the E2E test
Create `tests/playwright/e2e/test_{domain}_{flow}.py`:

```python
"""E2E test: {description}.

Requires: running app (docker compose up), E2E_ADMIN_USERNAME/PASSWORD env vars.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.playwright.helpers.config import E2ESettings
from tests.playwright.pages.admin.{page_name}_page import Admin{Entity}Page


@pytest.fixture()
def {entity}_page(admin_page: Page, settings: E2ESettings) -> Admin{Entity}Page:
    return Admin{Entity}Page(admin_page, settings.base_url)


class TestAdmin{Entity}List:
    """Tests for the {entity} list page."""

    def test_page_loads(self, {entity}_page: Admin{Entity}Page) -> None:
        {entity}_page.goto()
        {entity}_page.expect_loaded()

    def test_items_visible(self, {entity}_page: Admin{Entity}Page) -> None:
        {entity}_page.goto()
        {entity}_page.expect_items_visible()


class TestAdmin{Entity}Detail:
    """Tests for the {entity} detail page."""

    def test_detail_loads_from_list(self, {entity}_page: Admin{Entity}Page) -> None:
        {entity}_page.goto()
        {entity}_page.click_first_item()
        # Verify detail page loaded
        expect({entity}_page.page.locator("h1").first).to_be_visible()
```

### 5. Add data helpers (if the test needs test data)
Add to `tests/playwright/helpers/data.py`:

```python
def ensure_{entity}(api_context, token: str, **kwargs) -> dict[str, Any]:
    """Create or find a test {entity} via API."""
    headers = bearer_headers(token)
    response = api_post_json(
        api_context,
        "/api/v1/{route_path}",
        kwargs,
        headers=headers,
    )
    if not response.ok:
        raise AuthError(f"Failed to create {entity}: {response.status}")
    return response.json()
```

### 6. Fixture patterns

**Available auth fixtures** (from `conftest.py`):
| Fixture | Portal | Auth Method |
|---------|--------|-------------|
| `admin_page` | Admin | JWT cookie via `session_token` |
| `agent_page` | Admin (agent role) | JWT cookie |
| `user_page` | Admin (user role) | JWT cookie |
| `customer_page` | Customer portal | Impersonation cookie |
| `anon_page` | None | No auth (login page tests) |
| `vendor_page` | Vendor portal | Vendor auth (skipped if unavailable) |

**API test fixtures**:
| Fixture | Purpose |
|---------|---------|
| `api_context` | Playwright `APIRequestContext` |
| `admin_token` | Bearer token string |
| `settings` | `E2ESettings` from env vars |
| `test_identities` | Pre-created agent, user, customer personas |

### 7. BasePage helper methods
Available from `BasePage` (inherited by all page objects):
- `goto(path)` — navigate to URL
- `expect_loaded()` — assert page loaded (override in subclass)
- `expect_toast(message)` — assert toast notification appeared
- `expect_no_errors()` — assert no error banners
- `fill_form(fields)` — fill multiple form fields
- `submit_form(button_name)` — click submit and wait for navigation

### 8. Run the test
```bash
# Start app if not running
docker compose up -d app db redis

# Run the new test
E2E_ADMIN_USERNAME=admin E2E_ADMIN_PASSWORD=Admin123 \
  pytest tests/playwright/e2e/test_{domain}_{flow}.py -v --headed

# Run headless (CI mode)
E2E_ADMIN_USERNAME=admin E2E_ADMIN_PASSWORD=Admin123 \
  pytest tests/playwright/e2e/test_{domain}_{flow}.py -v
```

### 9. Checklist
- [ ] Page objects only contain accessors/assertions (no test logic)
- [ ] Uses `expect()` from Playwright (not assert) for auto-retry
- [ ] Tests use the correct auth fixture for the portal being tested
- [ ] `wait_until="domcontentloaded"` on `goto()` calls
- [ ] Test data created idempotently via `ensure_*` helpers
- [ ] No hardcoded URLs (use `settings.base_url`)
- [ ] No `time.sleep()` — use `expect()` or `wait_for_*` methods
