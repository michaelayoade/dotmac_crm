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

## UI/UX Design Guide

**Full visual reference:** `docs/design-guide.html` (open in browser for interactive component demos)

### Design System - "Industrial Modern"
The DotMac platform follows an **Industrial Modern** aesthetic: clean, functional, with subtle gradients, noise textures, and color-tinted shadows. This is NOT a generic admin template.

### Color System

**Primary palette:** Teal/Cyan (`primary-500: #06b6d4`) — used for active states, primary CTAs, focus rings, navigation highlights.

**Accent palette:** Warm Orange (`accent-500: #f97316`) — used for secondary highlights, gradient endpoints, CRM inbox decoration.

**Domain color assignments** (must be used consistently per section):
| Domain | Colors | Tailwind |
|--------|--------|----------|
| Customers / Subscribers | amber + orange | `color="amber" color2="orange"` |
| Network / IP Management | cyan + blue | `color="cyan" color2="blue"` |
| Fiber / OLTs | violet + purple | `color="violet" color2="purple"` |
| POP Sites / Assignments | teal + emerald | `color="teal" color2="emerald"` |
| System / IP Pools | indigo + violet | `color="indigo" color2="violet"` |
| Billing / Invoices | rose + pink | `color="rose" color2="pink"` |
| Success / Active | green + emerald | `color="green" color2="emerald"` |

**CRM channel colors** (defined in `main.css` as CSS custom properties):
- Email: violet | WhatsApp: green | SMS: orange | Telegram: sky
- Webchat: amber | Facebook Messenger: blue | Instagram: pink

### Typography

| Usage | Font | Weight | CSS Class |
|-------|------|--------|-----------|
| Display headings (h1-h3) | Outfit | 600-800 | `.font-display` or `<h1-h3>` (auto) |
| Body text | Plus Jakarta Sans | 400-700 | `.font-body` (default) |
| Metric values | Outfit | 700 | `.font-display text-3xl font-bold` |
| Labels/captions | Plus Jakarta Sans | 600, uppercase | `text-xs font-semibold uppercase tracking-wide` |

**Font files:** Self-hosted in `static/fonts/` (no external CDN requests in production).

### Spacing & Border Radius

**Standard spacing tokens:**
- `gap-3` / `gap-4`: Icon-to-text gaps, component spacing
- `px-6 py-4`: Card header padding
- `p-6`: Standard card body padding
- `p-5`: Compact card padding

**Border radius scale:**
- `rounded-lg` (8px): Small elements, badges, table header icons
- `rounded-xl` (12px): **Buttons**, **inputs**, tabs, icon badges
- `rounded-2xl` (16px): **Cards**, table wrappers, filter bars
- `rounded-full`: Avatars, notification dots

### Component Usage (Jinja2 Macros)

**Always import from `components/ui/macros.html`** — never write raw HTML for these patterns:

```html
{% from "components/ui/macros.html" import page_header, stats_card, data_table,
    table_head, table_row, row_actions, row_action, empty_state, pagination,
    status_badge, action_button, card, filter_bar, search_input, filter_select,
    info_row, detail_header, tabs, icon_badge, avatar, type_badge,
    submit_button, danger_button, warning_button, loading_button, spinner,
    info_banner, validated_input %}
```

**Macro parameter conventions:**
1. `variant` (string enum) — For semantic meaning: `status_badge(variant="success")`
2. `color` + `color2` (Tailwind colors) — For decorative gradients: `page_header(color="amber", color2="orange")`

### Dark Mode Rules

Every UI element **must** have light + dark variants:

| Element | Light | Dark |
|---------|-------|------|
| Page background | `bg-slate-100` | `dark:bg-slate-900` |
| Card surface | `bg-white` | `dark:bg-slate-800` |
| Card border | `border-slate-200/60` | `dark:border-slate-700/60` |
| Primary text | `text-slate-900` | `dark:text-white` |
| Secondary text | `text-slate-500` | `dark:text-slate-400` |
| Input bg | `bg-slate-50/50` | `dark:bg-slate-700/50` |
| Input border | `border-slate-200` | `dark:border-slate-600` |
| Subtle badge bg | `bg-{color}-500/10` | `dark:bg-{color}-500/20` |
| Subtle badge text | `text-{color}-700` | `dark:text-{color}-400` |

**Never** use inline `style="color: #xxx"` without a dark mode equivalent.

### Animation & Motion

**Entry animations** (applied to cards/sections on load):
- `animate-fade-in-up`: Standard card entrance (0.5s)
- `stagger-children`: Parent class for sequential child reveals (50ms delays)
- `stagger-in`: Individual stagger class

**Hover effects** (defined in `main.css`):
- `.btn-hover`: translateY(-1px) + brand shadow
- `.card-hover`: translateY(-2px) + deeper shadow
- `.list-item-hover`: translateX(2px) + tint background
- `.icon-btn-hover`: scale(1.05)

**Loading states:**
- Use `spinner` macro (sm/md/lg sizes)
- Submit buttons: use `submit_button()` or `loading_button()` macros
- HTMX indicators: `.htmx-indicator` class with opacity transition

**All animations must respect `@media (prefers-reduced-motion: reduce)`** — this is already handled in `main.css`.

### Accessibility Requirements (WCAG 2.1 AA)

- Status badges use **icon + color** (not color alone) — the `status_badge` macro handles this
- All icon-only buttons need `aria-label`
- Dropdowns/collapsibles need `aria-expanded` and `aria-controls`
- Toast container uses `aria-live="polite"`
- Form inputs need `aria-invalid` + `aria-describedby` for error states
- Decorative SVGs use `aria-hidden="true"`
- Table headers use `<th scope="col">`
- Global `#aria-live-region` exists in `base.html` for dynamic screen reader announcements
- Keyboard navigation: all interactive elements must be focusable + operable via keyboard
- Focus rings: `focus:ring-2 focus:ring-{color}-500/20 focus:outline-none`

### Responsive Patterns

- **Sidebar**: Fixed desktop (`lg:` breakpoint), overlay mobile with backdrop
- **Content max-width**: `max-w-7xl` (80rem)
- **Grid layouts**: `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3` (or lg:grid-cols-4 for stats cards)
- **Page headers**: `flex-col gap-4 sm:flex-row sm:items-center sm:justify-between`
- **Tables**: Always wrap in `overflow-x-auto`
- **Touch targets**: Minimum 40px (`h-10 w-10`) for mobile buttons

### Layout & Structural Patterns

**App Shell** (defined in `layouts/admin.html`):
- Root: `flex h-screen overflow-hidden` with sidebar + main column
- Sidebar: `w-64` expanded / `w-20` collapsed, Alpine.js state in localStorage
- Top bar: `h-16` with breadcrumbs (left), search + dark mode + notifications + user (right)
- Main content: `flex-1 overflow-y-auto bg-slate-100 dark:bg-slate-900 bg-noise bg-mesh`
- Content wrapper: `max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6`

**Page layout patterns** (choose the correct one for each page type):

| Layout | Grid | Use Case | Example |
|--------|------|----------|---------|
| List page | Vertical stack | Index pages with tables | Tickets, Contacts |
| Detail page | `grid gap-6 lg:grid-cols-3` (2/3 + 1/3) | View/detail pages | Ticket detail, Project detail |
| 3-panel inbox | `flex h-[calc(100vh-4rem)]` (sidebar + thread + contact) | Real-time messaging | CRM Inbox |
| Settings page | Pill filters + grouped form cards | Configuration pages | System Settings |
| Hub/dashboard | `grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4` | Navigation hubs | Admin Hub |

**Sidebar navigation** (`templates/components/navigation/admin_sidebar.html`):
- Sections: Dashboard → Customers → Network → Operations → Reports → System
- Active state: `bg-primary-100 text-primary-700 border-l-2 border-primary-600`
- Dark active: `dark:bg-primary-600/20 dark:text-primary-400 dark:border-primary-400`
- Section headers: `text-xs font-semibold uppercase tracking-wider text-slate-400`
- Badge counts: unread conversations (primary), open tickets (red), dispatch jobs (green)
- Permission gating: `{% if can_feature %}` per nav item
- Mobile: overlay with `bg-slate-900/50 backdrop-blur-sm` backdrop

**Breadcrumbs** (in top bar):
- Pattern: `nav.flex.items-center.gap-2.text-sm.text-slate-500`
- Chevron separator SVGs between items
- Last item: `font-semibold text-slate-900 dark:text-white` (current page)

**Global search**: Cmd+K / Ctrl+K modal overlay, `globalSearch()` Alpine.js component, `/api/search/typeahead` endpoint

**Confirmation modal** (`templates/components/modals/confirm_modal.html`):
- Triggered via `$dispatch('confirm-action', { title, message, actionUrl, method, variant })`
- Or use macros: `danger_button()`, `warning_button()`
- Variants: danger (red icon), warning (amber), info (blue)
- Backdrop: `bg-slate-900/60 backdrop-blur-sm`

**Toast notifications** (`toastStore()` Alpine.js):
- Position: fixed bottom-right, auto-dismiss 5s
- Python trigger: `headers["HX-Trigger"] = json.dumps({"showToast": {"message": "...", "type": "success"}})`
- JS trigger: `Alpine.store('toast').show('message', 'success')`
- Types: success, error, warning, info

**Alert banners** (inline, for form feedback):
- Success: `rounded-lg border bg-emerald-50 border-emerald-200 text-emerald-700 dark:bg-emerald-900/20 dark:border-emerald-800 dark:text-emerald-400`
- Error: `rounded-lg border bg-red-50 border-red-200 text-red-700 dark:bg-red-900/20 dark:border-red-800 dark:text-red-400`

### UI/UX Review Checklist

When creating or modifying any template, verify:

**Visual Consistency:**
- [ ] Correct domain color scheme (amber/orange for customers, cyan/blue for network, etc.)
- [ ] Headings use Outfit font (automatic via h1-h3 CSS rule)
- [ ] Buttons: `rounded-xl` | Cards: `rounded-2xl` | Badges: `rounded-lg`
- [ ] Page header uses `page_header()` macro with gradient icon badge
- [ ] Uses shared macros from `components/ui/macros.html` (no duplicate patterns)

**Dark Mode:**
- [ ] Every color class has a `dark:` variant
- [ ] No hardcoded hex colors in inline styles
- [ ] Borders use opacity variants (e.g., `border-slate-200/60 dark:border-slate-700/60`)
- [ ] Visually tested in both light and dark modes

**Interactions:**
- [ ] Cards/sections use `animate-fade-in-up` on initial load
- [ ] Submit buttons show loading state via `submit_button()` macro
- [ ] Destructive actions use `danger_button()` with confirmation modal
- [ ] HTMX requests show loading indicators
- [ ] Toast notifications for user feedback (success/error)

**Accessibility:**
- [ ] Icon-only buttons have `aria-label`
- [ ] Dropdowns use `aria-expanded` + `aria-controls`
- [ ] Status badges include icon differentiation (not just color)
- [ ] Form inputs have visible labels and error states with `aria-invalid`
- [ ] Proper heading hierarchy (h1 > h2 > h3, no skipped levels)
- [ ] Decorative icons use `aria-hidden="true"`

**Responsive:**
- [ ] Mobile layout works below 640px
- [ ] Touch targets are minimum 40px
- [ ] Tables wrapped in `overflow-x-auto`
- [ ] Grids collapse from multi-column to single-column

**HTMX & Alpine.js:**
- [ ] HTMX requests include CSRF token (automatic via `base.html` config)
- [ ] `x-cloak` on initially hidden Alpine.js elements (prevents FOUC)
- [ ] HTMX partials prefixed with `_` (e.g., `_ticket_table.html`)
- [ ] POST mutations use `status_code=303` redirect (PRG pattern)

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
