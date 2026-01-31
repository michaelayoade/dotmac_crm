# CLAUDE.md - DotMac Omni Project Guidelines

This file provides guidance for Claude Code when working on this codebase.

## Project Overview

DotMac Omni is an **omni-channel field service and CRM platform** for telcos/utilities:
- **Backend**: FastAPI + SQLAlchemy 2.0 + PostgreSQL/PostGIS
- **Frontend**: Jinja2 templates + HTMX + Alpine.js + Tailwind CSS v4
- **Task Queue**: Celery + Redis
- **Auth**: JWT + Cookies (multi-portal: admin, customer, reseller, vendor)
- **Deployment**: Single-tenant (one instance per organization)

### Core Domains
- **Tickets** - Customer support ticket management
- **Projects** - Field service projects with tasks and templates
- **Workforce** - Work orders, technician dispatch, scheduling
- **CRM** - Conversations, inbox, leads, quotes, omni-channel messaging
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
# Development server
docker compose up

# Run tests (quiet mode by default)
pytest
pytest tests/test_<module>.py -v      # Verbose single module
pytest --cov=app                       # With coverage

# Database migrations
alembic revision --autogenerate -m "Description"
alembic upgrade head
alembic downgrade -1

# CSS (Tailwind v4)
npm run css:build
npm run css:watch
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
└── tasks/        # Celery background tasks
templates/        # Jinja2 templates (mirrors web/ structure)
static/           # CSS, JS, fonts, images
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

### Service Layer Pattern
Services use singleton manager classes with CRUD operations:

```python
# app/services/example.py
class ExampleManager:
    def list(self, db: Session, **filters) -> list[Model]:
        query = db.query(Model)
        return query.all()

    def get(self, db: Session, id: UUID) -> Model | None:
        return db.query(Model).filter(Model.id == id).first()

    def create(self, db: Session, data: dict) -> Model:
        item = Model(**data)
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

### Template Pattern
Templates extend portal-specific layouts:

```html
{% extends "layouts/admin.html" %}

{% block breadcrumbs %}...{% endblock %}
{% block page_header %}...{% endblock %}
{% block content %}...{% endblock %}
```

Use HTMX for dynamic updates:
```html
<div hx-get="/admin/stats" hx-trigger="load, every 30s" hx-swap="innerHTML">
```

## Database Conventions

- UUIDs for all primary keys (using `UUID(as_uuid=True)`)
- Timestamps: `created_at`, `updated_at` with timezone
- Soft deletes where appropriate: `deleted_at`
- Use SQLAlchemy relationships with `back_populates`
- Geographic data uses GeoAlchemy2 with PostGIS

## Testing

- Tests live in `tests/` directory
- Use `conftest.py` fixtures for db sessions and test clients
- Mock external services in `tests/mocks.py`
- Async mode enabled via `pytest-asyncio`

## Style Guidelines

- Type hints for function signatures
- Pydantic for request/response validation
- Enum classes for status fields
- Manager classes export singleton instances
- Templates use Tailwind utility classes (dark mode with `dark:` prefix)

## Multi-Portal Authentication

Each portal has separate auth:
- Admin: `/auth/login` with role-based access
- Customer: `/portal/auth/login` with account-scoped access
- Reseller: `/reseller/auth/login` with multi-account access
- Vendor: `/vendor/auth/login` with project-scoped access

## Key Files

- `app/main.py` - FastAPI app setup and route registration
- `app/config.py` - Environment configuration
- `app/db.py` - Database session management
- `app/errors.py` - Exception handlers
- `templates/layouts/*.html` - Base layouts for each portal
- `templates/components/` - Reusable UI components

## Refactoring Status

The project has transitioned from subscription management to omni-channel. Legacy cleanup completed:

- ✅ SNMP/TR069 modules removed
- ✅ Billing/Catalog/Usage services removed
- ✅ All orphaned imports cleaned
- ✅ Subscriber model cleaned (no stubs)

## Architectural Improvements

### N+1 Query Prevention
Use window functions for batch loading related records:
```python
# Good: Batch load with window function
subq = db.query(
    Model.parent_id,
    Model.value,
    func.row_number().over(partition_by=Model.parent_id, order_by=Model.created_at).label("rn")
).subquery()
first_per_parent = db.query(subq).filter(subq.c.rn == 1).all()

# Bad: N+1 loop
for parent in parents:
    child = db.query(Child).filter(Child.parent_id == parent.id).first()  # N queries!
```

### Middleware DB Session
Middleware shares a single DB session via `request.state.middleware_db`:
```python
# Access shared session in middleware
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
2. **CRM Module Split** - Break 24-file CRM into logical submodules (contacts, inbox, sales, teams)
3. **Dependency Injection** - Enable proper testing via `dependency-injector` library
4. **Resource Ownership** - Add ownership checks (user can edit own tickets) where needed

**NOT needed** (single-tenant):
- Tenant isolation / row-level security
- Multi-tenant permission scoping
- Cross-tenant data access controls
