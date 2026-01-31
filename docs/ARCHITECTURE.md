# DotMac Omni - Architecture Documentation

> **Generated**: January 2026
> **Version**: Post-refactoring (omni-channel focus)

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Overview](#2-system-overview)
   - [Identity Model & External Sync Boundary](#21-identity-model--external-sync-boundary)
   - [Identity Data Flow](#22-identity-data-flow)
   - [Identity Migration Checklist](#23-identity-migration-checklist)
3. [Core Application Structure](#3-core-application-structure)
4. [Database Architecture](#4-database-architecture)
5. [Service Layer](#5-service-layer)
6. [API Architecture](#6-api-architecture)
7. [Web Portal Architecture](#7-web-portal-architecture)
8. [Frontend Architecture](#8-frontend-architecture)
9. [Background Jobs & Scheduling](#9-background-jobs--scheduling)
10. [Event System](#10-event-system)
11. [Authentication & Authorization](#11-authentication--authorization)
12. [Testing Architecture](#12-testing-architecture)
13. [Deployment & Infrastructure](#13-deployment--infrastructure)
14. [Key Design Patterns](#14-key-design-patterns)

---

## 1. Executive Summary

DotMac Omni is an **omni-channel field service and CRM platform** designed for telcos/utilities. The platform has been refactored from a subscription management system to focus on:

- **Core Domains**: Tickets, Projects, Workforce, CRM, Inventory, Notifications
- **Network Infrastructure**: Fiber plant management (infrastructure-only, no RADIUS/billing)
- **Multi-Portal Architecture**: Admin, Customer, Reseller, Vendor portals

### Technology Stack

| Layer | Technology |
|-------|------------|
| **Backend** | FastAPI 0.111.0, Python 3.12+ |
| **ORM** | SQLAlchemy 2.0.31 |
| **Database** | PostgreSQL 16 + PostGIS 3.4 |
| **Task Queue** | Celery 5.4.0 + Redis 7 |
| **Frontend** | Jinja2 3.1.4 + HTMX 2.0 + Alpine.js 3.x + Tailwind CSS v4 |
| **Monitoring** | OpenTelemetry + Prometheus |
| **Testing** | pytest 8.2 + Playwright 1.46 |

### Statistics

- **Python Files**: 325+
- **SQLAlchemy Models**: 120+ tables
- **REST API Endpoints**: 350+
- **Web Routes**: 250+
- **Pydantic Schemas**: 398 BaseModel classes
- **Service Managers**: 70+ modules
- **Background Tasks**: 11 task modules

---

## 2. System Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Load Balancer / Reverse Proxy                   │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────────────┐
│                           FastAPI Application                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │  REST APIs  │  │ Web Portals │  │ WebSocket   │  │ Static Assets       │ │
│  │  /api/v1/*  │  │ /admin/*    │  │ /ws/*       │  │ /static/*           │ │
│  │             │  │ /portal/*   │  │             │  │                     │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────────────────────┘ │
│         │                │                │                                  │
│  ┌──────▼────────────────▼────────────────▼──────┐                          │
│  │              Service Layer (Managers)          │                          │
│  │  tickets, projects, workforce, crm, inventory  │                          │
│  └──────────────────────┬────────────────────────┘                          │
│                         │                                                    │
│  ┌──────────────────────▼────────────────────────┐                          │
│  │              SQLAlchemy ORM                    │                          │
│  └──────────────────────┬────────────────────────┘                          │
└─────────────────────────┼───────────────────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          │               │               │
┌─────────▼───────┐ ┌─────▼─────┐ ┌───────▼───────┐
│   PostgreSQL    │ │   Redis   │ │ Celery Workers│
│   + PostGIS     │ │  (broker) │ │ + Beat        │
└─────────────────┘ └───────────┘ └───────────────┘
```

### 2.1 Identity Model & External Sync Boundary

The system is centered around **Subscriber** as the canonical customer identity for
operations. A subscriber can be linked to a **Person** (individual) or an
**Organization** (business), but operational domains (tickets, projects, work
orders, service orders) should always reference `subscriber_id`.

Key identity rules:

- **Canonical key**: `subscriber_id` is the primary operational identifier.
- **Contacts**: `Person` and `Organization` are contact/party records; they do not
  replace subscribers as the operational primary key.
- **External sync**: external billing/CRM systems sync into Subscriber records
  via `external_system` + `external_id` pairs. This keeps external IDs isolated
  while allowing local operational records to remain stable.

Boundary expectations:

- Inbound sync should **create/update subscribers** and link contact details.
- Downstream services should **not infer subscriber_id from person_id** unless a
  deterministic rule exists (for example, a designated primary subscriber).
- Event payloads and operational tables should **prefer subscriber_id** and treat
  legacy fields like `account_id`/`subscription_id` as compatibility only.

This boundary keeps identity consistent when the operations app is separated
from the billing/subscription system.

### 2.2 Identity Data Flow

```
External Billing / CRM
        │
        │  (sync webhook / batch import)
        ▼
Subscriber Sync Adapter
  - external_system + external_id
  - optional contact linkage
        │
        ▼
Subscriber (canonical record)
        │
        ├── Tickets        (subscriber_id)
        ├── Projects       (subscriber_id)
        ├── Service Orders (subscriber_id)
        ├── Work Orders    (subscriber_id)
        └── CRM Context    (subscriber_id + person/org)
```

Design notes:

- Subscriber sync is the only ingress for external identity.
- Operational domains do not store external IDs; they store `subscriber_id`.
- Contact and organization data are linked to Subscriber to enrich context,
  not to drive operational identity.

### 2.3 Identity Migration Checklist

1. **Schema**
   - Ensure operational tables store `subscriber_id` for customer identity.
   - Add indexes on `subscriber_id` for high-volume tables (tickets, work_orders, service_orders).
2. **Data Backfill**
   - Backfill `subscriber_id` in batches with idempotent scripts where needed.
3. **Service Layer**
   - Replace implicit inference (`person_id` → subscriber_id) with explicit selection or
     a single “primary subscriber” rule.
   - Validate `subscriber_id` on creates and updates.
4. **Events & Integrations**
   - Emit `subscriber_id` in all operational events and payloads.
5. **UX & API**
   - Update admin forms and typeahead lookups to use subscriber search only.
   - Remove or hide account/subscription filters from operational views.

### Directory Structure

```
app/
├── api/              # REST API endpoints (JSON responses)
│   └── crm/          # CRM sub-domain APIs
├── web/              # Web routes (HTML responses)
│   ├── admin/        # Staff portal (/admin/*)
│   ├── customer/     # Customer portal (/portal/*)
│   ├── reseller/     # Reseller portal (/reseller/*)
│   └── vendor/       # Vendor portal (/vendor/*)
├── models/           # SQLAlchemy ORM models
│   └── crm/          # CRM sub-domain models
├── schemas/          # Pydantic validation schemas
│   └── crm/          # CRM sub-domain schemas
├── services/         # Business logic (manager pattern)
│   ├── crm/          # CRM services
│   └── events/       # Event dispatcher & handlers
├── tasks/            # Celery background tasks
├── websocket/        # Real-time WebSocket support
└── validators/       # Input validation utilities

templates/            # Jinja2 templates
├── layouts/          # Base layouts per portal
├── components/       # Reusable UI components
├── admin/            # Admin portal templates
├── customer/         # Customer portal templates
└── ...

static/               # Frontend assets
├── css/              # Tailwind CSS
├── js/               # JavaScript modules
└── fonts/            # Typography

tests/                # Test suite
├── playwright/       # E2E tests
│   ├── pages/        # Page objects
│   └── e2e/          # Test specs
└── test_*.py         # Unit/integration tests
```

---

## 3. Core Application Structure

### Entry Point (`app/main.py`)

The FastAPI application is configured in `app/main.py` (414 lines):

```python
# Key components:
app = FastAPI(title="dotmac_omni API")

# Middleware stack (order matters):
1. ObservabilityMiddleware  # Request tracking, metrics
2. AuditMiddleware          # Action logging (30s cache)
3. CSRFMiddleware           # Double-submit cookie pattern

# Route registration:
- 42 API routers (dual /endpoint and /api/v1/endpoint)
- Web portal routers (admin, customer, reseller, vendor)
- WebSocket router
- Static files mount
```

### Configuration (`app/config.py`)

Frozen dataclass `Settings` with environment variable defaults:

```python
@dataclass(frozen=True)
class Settings:
    database_url: str           # PostgreSQL connection
    db_pool_size: int = 15      # Connection pool
    db_max_overflow: int = 20   # Max overflow connections
    # ... avatar, attachment, API settings
```

### Database Session (`app/db.py`)

Standard SQLAlchemy 2.0 setup:

```python
Base = declarative_base()
engine = create_engine(url, pool_pre_ping=True, ...)
SessionLocal = sessionmaker(bind=engine, autoflush=False)

def get_db():  # FastAPI dependency
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

### Error Handling (`app/errors.py`)

Structured error responses:

```python
{
    "code": "validation_error",      # Machine-readable
    "message": "Invalid input",      # Human-readable
    "details": [...]                 # Field-level errors
}
```

---

## 4. Database Architecture

### Model Organization

Models are organized by domain in `app/models/`:

| Category | Files | Key Models |
|----------|-------|------------|
| **Auth** | `auth.py` | UserCredential, Session, MFAMethod, ApiKey |
| **Person** | `person.py` | Person, PersonChannel, PersonStatusLog |
| **Subscriber** | `subscriber.py` | Subscriber, Organization, Reseller |
| **Tickets** | `tickets.py` | Ticket, TicketComment, TicketSlaEvent |
| **Projects** | `projects.py` | Project, ProjectTask, ProjectTemplate |
| **Workforce** | `workforce.py` | WorkOrder, WorkOrderAssignment |
| **Dispatch** | `dispatch.py` | Shift, Skill, TechnicianProfile |
| **CRM** | `crm/*.py` | Conversation, Message, Lead, Quote, Team |
| **Inventory** | `inventory.py` | InventoryItem, Stock, Reservation |
| **Network** | `network.py` | OLTDevice, OntUnit, FiberAccessPoint |
| **Provisioning** | `provisioning.py` | ServiceOrder, InstallAppointment |
| **Notification** | `notification.py` | Notification, Template, AlertPolicy |

### Database Conventions

**Primary Keys**: UUID (as_uuid=True)
```python
id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
```

**Timestamps**: DateTime with timezone
```python
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))
```

**Soft Deletes**: `is_active` boolean field

**JSON Metadata**: `metadata_` column with serialization alias
```python
metadata_: Mapped[dict | None] = mapped_column("metadata", MutableDict.as_mutable(JSON))
```

**Geographic Data**: GeoAlchemy2 with PostGIS
```python
geom = mapped_column(Geometry("POINT", srid=4326), nullable=True)
```

### Key Relationships

```
Person ──1:N── PersonChannel (email, phone, WhatsApp)
       └──1:N── Ticket (created_by, assigned_to)
       └──1:N── Conversation (contact)
       └──1:N── Lead, Quote (unified party model)

Subscriber ──N:1── Person (contact)
           └──N:1── Organization
           └──1:N── Ticket, WorkOrder, Project

Project ──1:N── ProjectTask (hierarchical via parent_task_id)
        └──N:1── ProjectTemplate

WorkOrder ──N:1── Ticket, Project, ServiceOrder
          └──1:N── WorkOrderAssignment

Conversation ──1:N── Message
             └──N:1── Person (contact)
             └──1:N── ConversationAssignment (team/agent routing)
```

---

## 5. Service Layer

### Manager Pattern

Services use singleton manager classes with standardized CRUD operations:

```python
# app/services/example.py
class ExampleManager(ListResponseMixin):
    def list(self, db: Session, **filters) -> list[Model]:
        query = db.query(Model)
        # Apply filters, ordering, pagination
        return query.all()

    def get(self, db: Session, id: UUID) -> Model | None:
        return db.query(Model).filter(Model.id == id).first()

    def create(self, db: Session, data: Schema) -> Model:
        item = Model(**data.model_dump())
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def update(self, db: Session, id: UUID, data: Schema) -> Model:
        item = self.get(db, id)
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        db.commit()
        return item

# Singleton export
example = ExampleManager()
```

### Active Service Modules

| Domain | Service Module | Key Managers |
|--------|----------------|--------------|
| **Tickets** | `services/tickets.py` | Tickets, TicketComments, TicketSlaEvents |
| **Projects** | `services/projects.py` | Projects, ProjectTasks, ProjectTemplates |
| **Workforce** | `services/workforce.py` | WorkOrders, WorkOrderAssignments |
| **Dispatch** | `services/dispatch.py` | Skills, TechnicianProfiles, Shifts, DispatchRules |
| **CRM** | `services/crm/` | Contacts, Conversations, Messages, Teams, Leads, Quotes |
| **Inventory** | `services/inventory.py` | InventoryItems, Stock, Reservations |
| **Notifications** | `services/notification.py` | Templates, Notifications, AlertPolicies |
| **Provisioning** | `services/provisioning.py` | ServiceOrders, ProvisioningWorkflows |
| **Auth** | `services/auth.py` | UserCredentials, Sessions, MFAMethods, ApiKeys |
| **Person** | `services/person.py` | People (unified party model) |
| **Subscriber** | `services/subscriber.py` | External system sync |

### Cross-Service Dependencies

```
Events System (dispatcher, handlers)
         │
         ▼
Core Services (tickets, projects, workforce)
         │
         ▼
Validation Layer (common.py utilities)
         │
         ▼
SQLAlchemy ORM → PostgreSQL
```

---

## 6. API Architecture

### REST API Structure

APIs are registered at both root (`/endpoint`) and versioned (`/api/v1/endpoint`) paths:

```python
# Pattern in main.py
def _include_api_router(router, dependencies=[]):
    app.include_router(router, dependencies=dependencies)
    app.include_router(router, prefix="/api/v1", dependencies=dependencies)
```

### Endpoint Patterns

```python
# Standard CRUD endpoints
GET    /tickets                 # List (paginated)
POST   /tickets                 # Create
GET    /tickets/{id}            # Get single
PATCH  /tickets/{id}            # Update
DELETE /tickets/{id}            # Delete

# Nested resources
GET    /tickets/{id}/comments
POST   /tickets/{id}/comments

# Bulk operations
POST   /tickets/bulk-update
```

### Response Format

```python
# Single item
{
    "id": "uuid",
    "title": "...",
    "status": "open",
    "created_at": "2026-01-27T..."
}

# List with pagination
{
    "items": [...],
    "count": 100,
    "limit": 50,
    "offset": 0
}

# Error
{
    "code": "not_found",
    "message": "Ticket not found",
    "details": null
}
```

### Authentication

All APIs require authentication via `require_user_auth` dependency:

```python
@router.get("/tickets")
def list_tickets(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_user_auth)
):
    # current_user contains: person_id, session_id, roles, scopes
    ...
```

---

## 7. Web Portal Architecture

### Multi-Portal System

| Portal | URL Prefix | Purpose | Auth |
|--------|------------|---------|------|
| **Admin** | `/admin/*` | Staff operations | Role-based |
| **Customer** | `/portal/*` | Self-service | Account-scoped |
| **Reseller** | `/reseller/*` | Partner management | Multi-account |
| **Vendor** | `/vendor/*` | Contractor access | Project-scoped |

### Web Route Patterns

Routes follow POST-Redirect-GET for form submissions:

```python
@router.get("/admin/tickets")              # List view
@router.get("/admin/tickets/new")          # Create form
@router.post("/admin/tickets")             # Create → redirect
@router.get("/admin/tickets/{id}")         # Detail view
@router.get("/admin/tickets/{id}/edit")    # Edit form
@router.post("/admin/tickets/{id}")        # Update → redirect
@router.post("/admin/tickets/{id}/delete") # Delete → redirect

# Always use status_code=303 for redirects after POST
return RedirectResponse(url=f"/admin/tickets/{id}", status_code=303)
```

### Template Rendering

```python
@router.get("/admin/tickets")
def tickets_list(request: Request, db: Session = Depends(get_db)):
    tickets = tickets_service.list(db, ...)
    return templates.TemplateResponse(
        "admin/tickets/index.html",
        {
            "request": request,
            "tickets": tickets,
            "current_user": get_current_user(request),
            "active_page": "tickets",
        }
    )
```

---

## 8. Frontend Architecture

### Template Hierarchy

```
templates/base.html              # Global: meta, fonts, CSS vars, dark mode
    └── templates/layouts/admin.html    # Admin: sidebar, header, breadcrumbs
        └── templates/admin/tickets/index.html   # Page content
```

### Block Structure

```jinja2
{% extends "layouts/admin.html" %}

{% block title %}Tickets{% endblock %}
{% block breadcrumbs %}...{% endblock %}
{% block page_header %}...{% endblock %}
{% block content %}
    <!-- Page content -->
{% endblock %}
```

### Component Library

Located in `templates/components/`:

| Category | Components |
|----------|------------|
| **Forms** | input, select, textarea, checkbox, repeatable_group |
| **Data** | table, card, stats_card, empty_state |
| **Feedback** | alert, toast, loading, skeleton |
| **Modals** | confirm_modal, modal |
| **Charts** | line_chart, bar_chart, doughnut_chart, sparkline |
| **Navigation** | dropdown, admin_sidebar |

### HTMX Integration

HTMX powers dynamic updates without full page reloads:

```html
<!-- Search with debounce -->
<input hx-get="/admin/tickets"
       hx-target="#tickets-table"
       hx-trigger="input changed delay:300ms"
       hx-include="[name='status']" />

<!-- Pagination -->
<button hx-get="/admin/tickets?page=2"
        hx-target="#tickets-body">Next</button>

<!-- Status update -->
<form hx-post="/admin/tickets/{{ id }}/status"
      hx-target="#ticket-detail"
      hx-swap="innerHTML">
```

### Alpine.js Patterns

Alpine.js handles client-side interactivity:

```javascript
// Global stores
Alpine.store('darkMode', { on: false, toggle() {...} })
Alpine.store('dirtyForms', { register(id), hasDirtyForms() })

// Component patterns
Alpine.data('globalSearch', () => ({
    query: '',
    results: [],
    loading: false,
    async search() {...},
    navigateDown() {...}
}))

Alpine.data('confirmModal', () => ({
    isOpen: false,
    title: '',
    async confirm() {...}
}))
```

### Tailwind CSS v4

Custom theme in `static/css/src/main.css`:

```css
@theme {
    --font-sans: 'Plus Jakarta Sans';
    --font-display: 'Outfit';
    --color-primary-500: #06b6d4;  /* Cyan */
    --color-accent-500: #f97316;   /* Orange */
}
```

Custom utilities: `.animate-fade-in-up`, `.bg-mesh`, `.card-hover`, `.btn-hover`

---

## 9. Background Jobs & Scheduling

### Celery Configuration

```python
# app/celery_app.py
celery_app = Celery("dotmac_omni")
celery_app.conf.update(get_celery_config())
celery_app.conf.beat_schedule = build_beat_schedule()
celery_app.conf.beat_scheduler = "app.celery_scheduler.DbScheduler"
```

### Task Modules

| Module | Tasks | Schedule |
|--------|-------|----------|
| `notifications.py` | `deliver_notification_queue` | Every 60s |
| `webhooks.py` | `deliver_webhook` | On-demand + retry |
| `gis.py` | `sync_gis_sources` | Every 60 min |
| `workflow.py` | `detect_sla_breaches` | Every 30 min |
| `bandwidth.py` | `process_bandwidth_stream` | Every 5s |
| `events.py` | `retry_failed_events` | Every 5 min |
| `oauth.py` | `refresh_expiring_tokens` | Daily |
| `wireguard.py` | `cleanup_connection_logs` | Daily |
| `subscribers.py` | `sync_subscribers_from_*` | Configurable |

### Dynamic Scheduling

The `DbScheduler` class loads schedules from database:

```python
class DbScheduler(Scheduler):
    def _refresh_schedule(self):
        # Called every 30 seconds
        schedule = build_beat_schedule()  # From ScheduledTask records
        self.merge_inplace(schedule)
```

### Task Pattern

```python
@celery_app.task(name="app.tasks.example.my_task")
def my_task():
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    try:
        # Perform work
        result = do_work(session)
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("my_task", status, time.monotonic() - start)
```

---

## 10. Event System

### Event Dispatcher

Located in `app/services/events/`:

```python
# Emit events from services
from app.services.events import emit_event
from app.services.events.types import EventType

emit_event(
    db,
    EventType.ticket_created,
    {"ticket_id": str(ticket.id), "title": ticket.title},
    ticket_id=ticket.id,
    subscriber_id=ticket.subscriber_id,
)
```

### Event Flow

```
Service calls emit_event()
         │
         ▼
Dispatcher persists to EventStore table
         │
         ▼
Calls registered handlers (sync)
         │
         ├── NotificationHandler → Queue notifications
         ├── ProvisioningHandler → Trigger workflows
         └── WebhookHandler → Queue webhook deliveries
         │
         ▼
Updates event status (completed/failed)
```

### Event Types

~40 event types covering:
- Subscriber lifecycle (created, updated, suspended)
- Ticket workflow (created, escalated, resolved)
- Service order (created, assigned, completed)
- Provisioning (started, completed, failed)
- CRM (conversation, message events)

---

## 11. Authentication & Authorization

### Multi-Portal Authentication

| Portal | Login URL | Session Storage |
|--------|-----------|-----------------|
| Admin | `/auth/login` | JWT in HTTP-only cookie |
| Customer | `/portal/auth/login` | JWT in HTTP-only cookie |
| Reseller | `/reseller/auth/login` | JWT in HTTP-only cookie |
| Vendor | `/vendor/auth/login` | JWT in HTTP-only cookie |
| API | Bearer token | Authorization header |

### JWT Token Flow

```
Login Request
      │
      ▼
Validate credentials (local/SSO)
      │
      ▼
Check MFA if enabled
      │
      ▼
Create Session record
      │
      ▼
Generate JWT (access: 15min, refresh: 30 days)
      │
      ▼
Set HTTP-only cookies (web) or return tokens (API)
```

### RBAC System

```python
# Models in app/models/rbac.py
Role → RolePermission → Permission
Person → PersonRole → Role

# Permission key format
"domain:action" or "domain:entity:action"
Each part starts with a lowercase letter and may include lowercase letters, numbers, underscores, and hyphens.
Examples: "tickets:read", "projects:task:create", "sales-orders:read"
```

### CSRF Protection

Double-submit cookie pattern for web forms:
- Cookie: `csrf_token` (HttpOnly=false for JS access)
- Form field: `_csrf_token`
- Header: `X-CSRF-Token` (for HTMX)

---

## 12. Testing Architecture

### Test Structure

```
tests/
├── conftest.py           # Database fixtures, auth setup
├── mocks.py              # Mock services (SMTP, Redis, etc.)
├── test_*_services.py    # Unit/integration tests (60 files)
└── playwright/
    ├── conftest.py       # Browser, auth, page fixtures
    ├── helpers/          # API, auth, data utilities
    ├── pages/            # Page Object Model
    └── e2e/              # E2E test specs (17 files)
```

### Fixture Patterns

```python
@pytest.fixture(scope="session")
def engine():
    # SQLite with Spatialite for local, PostgreSQL for CI

@pytest.fixture()
def db_session(engine):
    # Transaction-based isolation (auto-rollback)

@pytest.fixture()
def person(db_session):
    # Base entity fixture

@pytest.fixture()
def ticket(db_session, person):
    # Composed fixture with dependencies
```

### Running Tests

```bash
# All unit tests
pytest

# Specific module
pytest tests/test_projects_services.py -v

# With coverage
pytest --cov=app

# E2E tests
export PLAYWRIGHT_BASE_URL=http://localhost:8000
pytest tests/playwright/e2e/ -v
```

---

## 13. Deployment & Infrastructure

### Docker Compose Services

```yaml
services:
  db:
    image: postgis/postgis:16-3.4
    ports: ["5432:5432"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  app:
    build: .
    command: uvicorn app.main:app --host 0.0.0.0
    ports: ["8000:8000"]
    depends_on: [db, redis]

  celery-worker:
    command: celery -A app.celery_app worker
    depends_on: [db, redis]

  celery-beat:
    command: celery -A app.celery_app beat --scheduler=app.celery_scheduler.DbScheduler
    depends_on: [redis]
```

### Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/dotmac

# Redis
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0

# Auth
JWT_SECRET=your-secret-key
JWT_ALGORITHM=HS256

# Observability
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
```

---

## 14. Key Design Patterns

### 1. Manager Singleton Pattern

All services export singleton manager instances:

```python
# In app/services/tickets.py
class TicketsManager: ...
tickets = TicketsManager()

# Usage
from app.services import tickets
result = tickets.tickets.list(db, ...)
```

### 2. Schema Inheritance Pattern

Four-schema pattern for entities:

```python
class TicketBase(BaseModel):      # Common fields
class TicketCreate(TicketBase):   # POST input
class TicketUpdate(BaseModel):    # PATCH input (all optional)
class TicketRead(TicketBase):     # Response output
```

### 3. POST-Redirect-GET Pattern

Web forms always redirect after successful submission:

```python
@router.post("/admin/tickets")
def create_ticket(...):
    ticket = tickets.create(db, data)
    return RedirectResponse(
        url=f"/admin/tickets/{ticket.id}",
        status_code=303
    )
```

### 4. Event-Driven Architecture

Services emit events, handlers react:

```python
# Service emits
emit_event(db, EventType.ticket_created, {...})

# Handler processes
class NotificationHandler:
    def handle_ticket_created(self, event):
        queue_notification(...)
```

### 5. Settings-Driven Configuration

Dynamic configuration from database:

```python
# Read setting with fallback
value = settings_spec.resolve_value(
    db,
    SettingDomain.projects,
    "default_project_status",
    default="planned"
)
```

### 6. Transaction-Per-Request Pattern

Database sessions scoped to request lifecycle:

```python
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

---

## Appendix: Removed Domains

The following were removed during the omni-channel refactoring and should NOT be referenced:

- **Billing/Invoicing** (`app/services/billing/`, `app/models/billing.py`)
- **Catalog/Subscriptions** (`app/services/catalog/`, `app/models/catalog.py`)
- **NAS/RADIUS** (`app/services/nas.py`, `app/services/radius.py`)
- **Usage/Metering** (`app/services/usage.py`, `app/models/usage.py`)
- **Collections/Dunning** (`app/services/collections/`)
- **SNMP/TR069** (`app/services/snmp.py`, `app/services/tr069.py`)

See `CLAUDE.md` for cleanup tasks related to orphaned references.
