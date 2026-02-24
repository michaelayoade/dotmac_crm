# Testing Rules

## Structure

```
tests/
├── conftest.py          # Fixtures: db_session, person, ticket, project, CRM objects
├── mocks.py             # External service mocks
├── test_*.py            # Unit/integration tests
└── playwright/
    ├── conftest.py      # E2E fixtures: browser, pages, auth tokens
    ├── pages/           # Page Object Model classes
    └── e2e/             # End-to-end test scenarios
```

## Running Tests

```bash
pytest                                    # All tests, quiet
pytest tests/test_auth_flow.py -v         # Single module, verbose
pytest --cov=app --cov-report=term-missing # Coverage
pytest -x -v                              # Stop on first failure
pytest -k "campaign"                      # Keyword match
```

## Test Fixtures

- `db_session` — transactional with auto-rollback
- `person` — test Person record
- `ticket`, `project`, `work_order` — domain fixtures
- `crm_contact`, `crm_team`, `crm_agent` — CRM fixtures

## Writing Tests

- **Always write tests for new services** — no service code without tests
- Use `db_session` fixture (auto-rollback per test)
- Mock external services via `tests/mocks.py`
- File naming: `tests/test_{domain}_{layer}.py`
- `asyncio_mode = "auto"` — async tests work without decorator

## E2E Tests (Playwright)

```bash
pytest tests/playwright/ --headed         # Visible browser
pytest tests/playwright/e2e/ -v           # E2E scenarios only
```

Fixtures: `admin_page`, `agent_page`, `customer_page`, `anon_page`
Page objects follow POM pattern in `tests/playwright/pages/`.

## Test Requirements by Change Type

| Change | Tests Required |
|--------|---------------|
| New service | CRUD, filtering, validation, edge cases |
| New route | Request/response, permissions, error handling |
| Model change | Migration + existing test suite passes |
| Template change | Playwright E2E if critical flow |
| Bug fix | Regression test that reproduces the bug |
