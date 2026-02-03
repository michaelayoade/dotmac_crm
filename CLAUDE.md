# CLAUDE.md - DotMac CRM Project Guidelines

This file provides guidance for Claude Code when working on this codebase.

## Project Overview

DotMac CRM is an **omni-channel field service and CRM platform** for telcos/utilities:
- **Backend**: FastAPI + SQLAlchemy 2.0 + PostgreSQL/PostGIS
- **Frontend**: Jinja2 templates + HTMX + Alpine.js + Tailwind CSS v4
- **Task Queue**: Celery + Redis
- **Auth**: JWT + Cookies (multi-portal: admin, customer, reseller, vendor)
- **Deployment**: Single-tenant (one instance per organization)
- **Python**: 3.11+ (target 3.12)

### Core Domains
- **Tickets** - Customer support ticket management
- **Projects** - Field service projects with tasks and templates
- **Workforce** - Work orders, technician dispatch, scheduling
- **CRM** - Conversations, inbox, leads, quotes, campaigns, omni-channel messaging
- **Inventory** - Stock management, reservations, work order materials
- **Notifications** - Email, SMS, push, webhooks
- **Network** - Fiber plant, wireless surveys (infrastructure-only)

### Removed Domains (Legacy Subscription Management)
The following have been removed and should NOT be referenced:
- Billing/Invoicing (`app/services/billing/`, `app/models/billing.py`)
- Catalog/Subscriptions (`app/services/catalog/`, `app/models/catalog.py`)
- NAS/RADIUS (`app/services/nas.py`, `app/services/radius.py`)
- Usage/Metering (`app/services/usage.py`, `app/models/usage.py`)
- Collections/Dunning (`app/services/collections/`)
- SNMP/TR069 device management

## Quick Commands

```bash
# ── Development ───────────────────────────────────────────────
docker compose up                        # Start all services
docker compose up app db redis           # App + deps only (no beat/worker)

# ── Linting & Formatting ─────────────────────────────────────
ruff check app/ tests/                   # Lint (pyflakes, pycodestyle, isort, bugbear)
ruff check app/ tests/ --fix             # Lint + auto-fix
ruff format app/ tests/                  # Format (replaces black)

# ── Type Checking ─────────────────────────────────────────────
mypy app/                                # Full type check
mypy app/services/crm/                   # Check specific module

# ── Tests ─────────────────────────────────────────────────────
pytest                                   # Run all tests (quiet)
pytest tests/test_<module>.py -v         # Verbose single module
pytest --cov=app                         # With coverage report
pytest -x                                # Stop on first failure
pytest -k "test_name"                    # Run matching tests
pytest tests/playwright/                 # E2E tests (needs running app)

# ── Database ──────────────────────────────────────────────────
alembic revision --autogenerate -m "Description"
alembic upgrade head
alembic downgrade -1
alembic history                          # Show migration chain

# ── CSS (Tailwind v4) ────────────────────────────────────────
npm run css:build                        # One-time build (minified)
npm run css:watch                        # Watch mode for development

# ── Useful Checks ────────────────────────────────────────────
python -c "from app.models import *"     # Verify all models compile
python -c "from app.main import app"     # Verify app boots
```

## Architecture

```
app/
├── api/          # REST API endpoints (JSON responses)
├── web/          # Web routes (HTML responses via Jinja2)
│   ├── admin/    # Staff portal (/admin/*)
│   ├── customer/ # Customer portal (/portal/*)
│   ├── reseller/ # Reseller portal (/reseller/*)
│   └── vendor/   # Vendor portal (/vendor/*)
├── models/       # SQLAlchemy ORM models
├── schemas/      # Pydantic schemas for validation
├── services/     # Business logic (manager pattern)
├── tasks/        # Celery background tasks
├── middleware/    # Request middleware (rate limiting, etc.)
├── websocket/    # WebSocket manager and events
└── container.py  # Dependency injection container
templates/        # Jinja2 templates (mirrors web/ structure)
├── layouts/      # Base layouts per portal (admin, customer, auth)
├── components/   # Reusable UI components (navigation, macros)
└── admin/        # Admin portal templates
static/           # CSS, JS, fonts, images
├── css/src/      # Tailwind source CSS
├── css/          # Compiled CSS output
└── js/           # Alpine.js components, HTMX helpers
scripts/          # One-off utility scripts (imports, seeding, migration)
tests/            # Test suite
├── playwright/   # E2E browser tests (page objects + helpers)
└── *.py          # Unit/integration tests
```

## Active Service Modules

| Domain | Models | Services | API |
|--------|--------|----------|-----|
| Tickets | `models/tickets.py` | `services/tickets.py` | `api/tickets.py` |
| Projects | `models/projects.py` | `services/projects.py` | `api/projects.py` |
| Workforce | `models/workforce.py` | `services/workforce.py` | `api/workforce.py` |
| Dispatch | `models/dispatch.py` | `services/dispatch.py` | `api/dispatch.py` |
| CRM | `models/crm/` | `services/crm/` | `api/crm/` |
| Inventory | `models/inventory.py` | `services/inventory.py` | `api/inventory.py` |
| Notifications | `models/notification.py` | `services/notification.py` | `api/notifications.py` |
| Provisioning | `models/provisioning.py` | `services/provisioning.py` | - |
| Network | `models/network.py` | `services/network/` | `api/fiber_plant.py` |
| Persons | `models/person.py` | `services/person.py` | `api/persons.py` |

## Code Patterns

### Service Layer Rule
**All business logic MUST live in the service layer** (`app/services/`). API routes (`app/api/`) and web routes (`app/web/`) are thin wrappers that only handle request parsing, response formatting, and delegation to services. Routes must never contain business logic, database queries, or domain calculations directly.

### Service Layer Pattern
Services use singleton manager classes with CRUD operations:

```python
# app/services/example.py
from app.services.common import apply_ordering, apply_pagination, coerce_uuid
from app.services.response import ListResponseMixin

class ExampleManager(ListResponseMixin):
    @staticmethod
    def list(db: Session, **filters) -> list[Model]:
        query = db.query(Model)
        query = apply_ordering(query, "created_at", "desc", {...})
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def get(db: Session, item_id: str) -> Model:
        item = db.get(Model, coerce_uuid(item_id))
        if not item:
            raise HTTPException(status_code=404, detail="Not found")
        return item

    @staticmethod
    def create(db: Session, payload) -> Model:
        item = Model(**payload.model_dump())
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

# Singleton instance
example = ExampleManager()
```

### Web Route Pattern
Routes follow RESTful conventions with POST-Redirect-GET:

```python
@router.get("")                    # List
@router.get("/new")               # Create form
@router.post("")                  # Create (redirect after)
@router.get("/{id}")              # Detail view
@router.get("/{id}/edit")         # Edit form
@router.post("/{id}")             # Update (redirect after)
@router.post("/{id}/delete")      # Delete (redirect after)
```

Always use `status_code=303` for POST redirects:
```python
return RedirectResponse(url=f"/admin/items/{item.id}", status_code=303)
```

Route context helpers follow this pattern:
```python
def _base_ctx(request: Request, db: Session, **kwargs) -> dict:
    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)
    return {"request": request, "current_user": current_user,
            "sidebar_stats": sidebar_stats, "active_page": "page_name", **kwargs}
```

### Template Pattern
Templates extend portal-specific layouts:

```html
{% extends "layouts/admin.html" %}

{% block title %}Page Title - Admin{% endblock %}
{% block content %}
    <!-- Breadcrumb navigation -->
    <nav class="flex items-center gap-2 text-sm text-slate-500">...</nav>
    <!-- Page content -->
{% endblock %}
```

Use HTMX for dynamic updates:
```html
<div hx-get="/admin/stats" hx-trigger="load, every 30s" hx-swap="innerHTML">
```

### Celery Task Pattern
```python
@celery_app.task(name="app.tasks.module.task_name")
def task_name(arg: str):
    session = SessionLocal()
    try:
        # ... business logic ...
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Error in task_name")
        raise
    finally:
        session.close()
```

Register periodic tasks in `app/services/scheduler_config.py` via `_sync_scheduled_task()`.

### Schema Pattern (Pydantic)
```python
class ItemBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None

class ItemCreate(ItemBase):
    pass

class ItemUpdate(BaseModel):  # separate base — all fields optional
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None

class ItemRead(ItemBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    created_at: datetime
    updated_at: datetime
```

### Enum Pattern
```python
import enum

class ItemStatus(enum.Enum):
    draft = "draft"
    active = "active"
    archived = "archived"
```

Register new enums in `app/models/crm/enums.py` (CRM) or inline in domain model files.

## Linting & Formatting

### Ruff (Linter + Formatter)
Configured in `pyproject.toml` under `[tool.ruff]`:
- **Line length**: 120 characters
- **Target**: Python 3.11
- **Rules**: pycodestyle (E/W), pyflakes (F), isort (I), pyupgrade (UP), bugbear (B), simplify (SIM), no-print (T20), ruff-specific (RUF)
- **Ignored**: E501 (line length — formatter handles it), B008 (FastAPI `Depends()` in defaults), B904 (raise-without-from), SIM108 (ternary)
- **Import sorting**: `app` as first-party; ruff replaces isort
- **Migration files**: exempt from line length and unused import rules
- **Test files**: allowed to use `print()`

```bash
ruff check app/ tests/            # Check for issues
ruff check app/ tests/ --fix      # Auto-fix what's possible
ruff format app/ tests/           # Format code (replaces black)
ruff check --select I --fix .     # Fix import order only
```

### Mypy (Type Checking)
Configured in `mypy.ini`:
- **Plugin**: `sqlalchemy.ext.mypy.plugin` (ORM-aware type checking)
- **Python version**: 3.11
- **Strict**: `warn_unused_ignores = True`
- **Ignored imports**: jose, passlib, weasyprint, routeros_api, celery, requests (missing stubs)

```bash
mypy app/                          # Full check
mypy app/services/crm/campaigns.py # Single file
mypy app/ --ignore-missing-imports # Suppress third-party import errors
```

### Pre-Commit Workflow
Before committing, run:
```bash
ruff check app/ tests/ --fix && ruff format app/ tests/ && mypy app/
```

## Database Conventions

- UUIDs for all primary keys (using `UUID(as_uuid=True)`)
- Timestamps: `created_at`, `updated_at` with timezone (`DateTime(timezone=True)`)
- Soft deletes where appropriate: `is_active` bool or `deleted_at` timestamp
- Use SQLAlchemy relationships with `back_populates`
- Geographic data uses GeoAlchemy2 with PostGIS
- JSON columns: use `mapped_column(JSON)` for flexible metadata (`metadata_`, `tags`, `segment_filter`)
- Counter columns: use `Integer` with `default=0` for denormalized counts
- Enum columns: create PostgreSQL ENUM types in migrations with `create_type=False` + `checkfirst=True`

### Migration Conventions
```bash
alembic revision --autogenerate -m "Add table_name"  # Auto-detect model changes
alembic revision -m "Add index on table.column"       # Hand-written for indexes/constraints
```

- Hand-write migrations for: indexes, partial unique indexes, data migrations, enum type creation
- Use `checkfirst=True` when creating PostgreSQL ENUM types
- For nullable unique constraints (NULL != NULL in PostgreSQL), add a partial unique index:
  ```python
  op.execute("CREATE UNIQUE INDEX ix_name ON table (col_a, col_b) WHERE col_c IS NULL")
  ```
- Always include `downgrade()` that reverses all changes
- Use `op.get_bind()` for enum creation, not raw SQL

## Testing

### Test Structure
```
tests/
├── conftest.py                    # Fixtures: db_session, person, ticket, project, CRM objects
├── mocks.py                       # External service mocks
├── test_*.py                      # Unit/integration tests
└── playwright/
    ├── conftest.py                # E2E fixtures: browser, pages, auth tokens
    ├── pages/                     # Page Object Model classes
    │   ├── base_page.py           # BasePage with common helpers
    │   ├── admin/                 # Admin portal page objects
    │   ├── customer/              # Customer portal page objects
    │   └── vendor/                # Vendor portal page objects
    ├── helpers/                   # API helpers for test setup
    └── e2e/                       # End-to-end test scenarios
```

### Running Tests
```bash
pytest                                    # All tests, quiet mode
pytest tests/test_auth_flow.py -v         # Single module, verbose
pytest --cov=app --cov-report=term-missing # Coverage with line detail
pytest -x -v                              # Stop on first failure, verbose
pytest -k "campaign"                      # Run tests matching keyword
```

### Test Fixtures (conftest.py)
- `db_session` — transactional session with auto-rollback
- `person` — test Person record
- `ticket` — test Ticket record
- `project`, `project_task` — test Project + Task
- `work_order` — test WorkOrder
- `crm_contact`, `crm_team`, `crm_agent` — CRM test objects

### E2E Tests (Playwright)
```bash
# Requires running app (docker compose up)
pytest tests/playwright/ --headed          # Run with visible browser
pytest tests/playwright/e2e/ -v            # E2E scenarios only
```

Available E2E fixtures: `admin_page`, `agent_page`, `user_page`, `customer_page`, `anon_page`

### Writing Tests
- Use `db_session` fixture for database tests (auto-rollback per test)
- Mock external services via `tests/mocks.py`
- Test files: `tests/test_<domain>_<layer>.py` (e.g., `test_auth_flow.py`, `test_celery_tasks.py`)
- Playwright pages follow Page Object Model pattern in `tests/playwright/pages/`
- `asyncio_mode = "auto"` — async tests work without `@pytest.mark.asyncio`

## Style Guidelines

- Type hints for all function signatures (parameters and return types)
- Pydantic for request/response validation
- Enum classes for status fields (not string literals)
- Manager classes export singleton instances
- Templates use Tailwind utility classes (dark mode with `dark:` prefix)
- Use `from __future__ import annotations` in schemas for forward references
- Prefer `str | None` over `Optional[str]` (Python 3.10+ union syntax)
- Use `Mapped[type]` with `mapped_column()` for SQLAlchemy 2.0 models
- No stray `print()` statements in production code (enforced by ruff T20 rule)

## Frontend Conventions

### Tailwind CSS v4
- Source: `static/css/src/main.css` → Build: `static/css/main.css`
- Dark mode: class-based (`dark:` prefix, toggled via Alpine.js)
- Primary color: teal-cyan palette; Accent: warm orange
- Fonts: Plus Jakarta Sans (body), Outfit (display headings)
- Config: `tailwind.config.js` with safelist for dynamic Jinja2 color classes

### HTMX Patterns
- Use `hx-get` for data loading, `hx-post` for mutations
- `hx-trigger="load"` for lazy-loading partials
- `hx-swap="innerHTML"` for replacing content within containers
- HTMX partials are prefixed with `_` (e.g., `_campaign_recipients_table.html`)

### Alpine.js
- Used for sidebar state, modals, dropdowns, form interactivity
- Sidebar collapse state persisted in `localStorage`
- Section expand/collapse via `x-show` + `x-collapse`

## Multi-Portal Authentication

Each portal has separate auth:
- Admin: `/auth/login` with role-based access (RBAC)
- Customer: `/portal/auth/login` with account-scoped access
- Reseller: `/reseller/auth/login` with multi-account access
- Vendor: `/vendor/auth/login` with project-scoped access

### Sidebar Navigation
- Sidebar permissions defined in `templates/components/navigation/admin_sidebar.html`
- Permission format: `domain:resource:action` (e.g., `crm:campaign:read`)
- Sidebar sections: Customers, Network, Operations, Reports, System
- Add new pages: update `section_for_page` mapping + add `can_<feature>` permission check

## Key Files

- `app/main.py` - FastAPI app setup and route registration
- `app/config.py` - Environment configuration (Settings class)
- `app/db.py` - Database session management (SessionLocal, Base)
- `app/errors.py` - Exception handlers
- `app/container.py` - Dependency injection container
- `app/services/common.py` - Shared utilities (coerce_uuid, apply_ordering, apply_pagination, validate_enum)
- `app/services/response.py` - ListResponseMixin for paginated queries
- `app/services/scheduler_config.py` - Celery beat schedule registration
- `app/celery_app.py` - Celery app setup with autodiscovery
- `templates/layouts/*.html` - Base layouts for each portal
- `templates/components/` - Reusable UI components (navigation, macros)
- `mypy.ini` - Mypy configuration with SQLAlchemy plugin
- `alembic.ini` - Alembic migration configuration
- `tailwind.config.js` - Tailwind CSS theme and safelist
- `docker-compose.yml` - Development services (app, db, redis, celery, nominatim)
- `Dockerfile` - Production image (python:3.12-slim + system deps)

## Docker Development Environment

```yaml
# Services:
app          # FastAPI + uvicorn (port 8000, auto-reload, runs migrations on start)
db           # PostGIS 16 (port 5432)
redis        # Redis 7 (port 6379)
celery-worker # 4 concurrent workers
celery-beat   # Periodic task scheduler
nominatim    # Geocoding (port 8080, profile: geocoding)
openbao      # Secrets manager (port 8200, profile: secrets)
```

Hot-reload volumes: `app/`, `templates/`, `static/`, `alembic/` are bind-mounted.

## Refactoring Status

The project has transitioned from subscription management to omni-channel. Legacy cleanup completed:

- SNMP/TR069 modules removed
- Billing/Catalog/Usage services removed
- All orphaned imports cleaned
- Subscriber model cleaned (no stubs)

## Architectural Improvements

### N+1 Query Prevention
Use batch loading patterns — never query inside a loop:
```python
# Good: Batch load with a single query
person_ids = [r.person_id for r in recipients]
persons = db.query(Person).filter(Person.id.in_(person_ids)).all()
person_map = {p.id: p for p in persons}

# Good: Eager load relationships
from sqlalchemy.orm import joinedload
db.query(Recipient).options(joinedload(Recipient.step)).all()

# Good: Batch existence check
existing_ids = set(pid for (pid,) in db.query(Model.person_id).filter(...).all())
for item in items:
    if item.id in existing_ids:
        continue

# Bad: N+1 loop
for parent in parents:
    child = db.query(Child).filter(Child.parent_id == parent.id).first()  # N queries!
```

### Race Condition Prevention
Use `with_for_update` for concurrent task safety:
```python
campaign = db.query(Campaign).filter(Campaign.id == cid).with_for_update(skip_locked=True).first()
```

### Middleware DB Session
Middleware shares a single DB session via `request.state.middleware_db`:
```python
db = getattr(request.state, "middleware_db", None) or SessionLocal()
```

### API Rate Limiting
Global rate limiting via `APIRateLimitMiddleware` (100 req/min default):
- Configure via `API_RATE_LIMIT` and `API_RATE_WINDOW` env vars
- Uses Redis with in-memory fallback
- Adds `X-RateLimit-*` headers to responses
- Exempt paths: `/health`, `/metrics`, `/static/`, `/docs`

## Architectural Decisions

### Single-Tenant Deployment
This is a **single-tenant application** (one instance per organization). This means:
- No tenant isolation or multi-tenancy patterns needed
- No tenant_id scoping in queries
- Resource-level permissions focus on user ownership, not tenant boundaries
- Simpler permission model: RBAC + optional resource ownership checks

### Medium-Term Improvements (Planned)
Focus areas for maintainability and testability:

1. **Query Builders** - Extract filter logic from services into composable builders
2. **CRM Module Split** - Break CRM into logical submodules (contacts, inbox, sales, teams, campaigns)
3. **Dependency Injection** - Enable proper testing via `dependency-injector` library
4. **Resource Ownership** - Add ownership checks (user can edit own tickets) where needed

**NOT needed** (single-tenant):
- Tenant isolation / row-level security
- Multi-tenant permission scoping
- Cross-tenant data access controls
