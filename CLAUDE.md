# DotMac Omni CRM — Claude Agent Guide

FastAPI + SQLAlchemy 2.0 + Jinja2/HTMX/Alpine.js + Celery + PostgreSQL + PostGIS.
Single-tenant ISP CRM: 4 portals (admin/customer/reseller/vendor), 184 tables, AI intelligence layer.

## Plugins
`frontend-design`, `playwright`

## Non-Negotiable Rules

### Single-tenant — no org_id filtering
This is NOT multi-tenant. No `organization_id` filter needed on queries.

### Manager singleton pattern — NOT class-per-request
```python
class Entities(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: EntityCreate) -> Entity: ...
    @staticmethod
    def get(db: Session, entity_id: str) -> Entity: ...

entities = Entities()   # Singleton export
```

### SQLAlchemy 2.0 + `db.commit()` in services
Unlike ERP, CRM services DO commit:
```python
session.commit()    # in the task/service
```

### POST redirects — always 303
```python
return RedirectResponse(url=f"/admin/items/{item.id}", status_code=303)
```

### Commands — always `poetry run`
```bash
poetry run ruff check app/ --fix
poetry run mypy app/
poetry run pytest tests/ -x -q
```

### Docker containers
- App: `dotmac_crm_app` (or check `docker-compose.yml`)

### Never reference removed domains
These no longer exist: billing, catalog, NAS/RADIUS, usage, collections, SNMP.

## Common Utilities (`app/services/common.py`)
| Function | Purpose |
|----------|---------|
| `coerce_uuid(value)` | String → UUID, None-safe |
| `apply_ordering(query, col, dir, allowed)` | Validated ORDER BY |
| `apply_pagination(query, limit, offset)` | LIMIT/OFFSET |
| `validate_enum(value, enum_cls, label)` | Enum validation |
| `get_or_404(db, model, id)` | Get or raise 404 |
| `round_money(value)` | Banker's rounding |

## Design System — "Industrial Modern"

**Fonts**: Outfit (display/metrics) + Plus Jakarta Sans (body), self-hosted in `static/fonts/`.

**Domain colors** (use consistently):
| Domain | Colors |
|--------|--------|
| Customers/Subscribers | amber + orange |
| Network/IP | cyan + blue |
| Fiber/OLTs | violet + purple |
| Tickets/Support | rose + pink |
| Projects | emerald + teal |
| System/Settings | indigo + violet |

**CRM channel colors** (CSS custom properties):
Email=violet, WhatsApp=green, SMS=orange, Telegram=sky, Webchat=amber, Facebook=blue, Instagram=pink

**Border radius**: badges=`rounded-lg`, buttons+inputs=`rounded-xl`, cards=`rounded-2xl`, avatars=`rounded-full`

### Macros — always import from `components/ui/macros.html`
```html
{% from "components/ui/macros.html" import page_header, stats_card, data_table,
    table_head, table_row, row_actions, row_action, empty_state, pagination,
    status_badge, action_button, card, filter_bar, search_input, filter_select,
    submit_button, danger_button, warning_button, loading_button, spinner %}
```
Never duplicate these in raw HTML.

### Alpine.js — single quotes critical
```html
<div x-data='{{ data | tojson }}'>   <!-- correct -->
<div x-data="{{ data | tojson }}">   <!-- WRONG -->
```

### Enum display
```html
{{ item.status.value | replace('_', ' ') | title }}
```

### None handling
```html
{{ var if var else '' }}
```

### Dynamic Tailwind classes — dict lookup
```html
{% set colors = {'active': 'bg-green-100 text-green-800'} %}
<span class="{{ colors.get(item.status.value, 'bg-gray-100') }}">
```

### Dark mode — always pair
```html
<div class="bg-white dark:bg-slate-800 text-slate-900 dark:text-white">
```

### Toast via HX-Trigger
```python
headers = {"HX-Trigger": json.dumps({"showToast": {"message": "Saved!", "type": "success"}})}
return RedirectResponse(url=url, status_code=303, headers=headers)
```

## Celery Tasks

```python
@celery_app.task(name="app.tasks.module.task_name")
def task_name() -> dict:
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    results = {"processed": 0, "errors": []}
    try:
        from app.services.module import service   # import inside task
        # ... delegate to service ...
        session.commit()
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("task_name", status, time.monotonic() - start)
    return results
```
Register schedules via `_sync_scheduled_task()` in `app/services/scheduler_config.py`.

## Database

- UUIDs for all PKs
- Soft deletes via `is_active` boolean
- PostGIS: SRID 4326, `ST_DWithin()` for proximity, cast to `::geography` for meter distances
- Migrations: `alembic revision --autogenerate -m "desc"`, always idempotent, `checkfirst=True` for enums

## Testing

```
tests/
├── conftest.py          # db_session, person, ticket, project, CRM fixtures
├── mocks.py             # External service mocks
└── playwright/
    ├── pages/           # Page Object Model
    └── e2e/             # E2E scenarios
```
```bash
pytest -x -v
pytest -k "campaign"
pytest tests/playwright/ --headed
```

## Security
- All routes: `Depends(require_permission("domain:resource:action"))`
- CSRF: double-submit cookie (automatic via `base.html` for HTMX)
- File uploads: validate size first, UUID storage names, `resolve_safe_path()`
- Never bare `except:`, never f-strings in SQL, never hardcoded secrets
- `| safe` only for CSRF tokens, `tojson`, admin CSS

## Common Mistakes
- `db.query()` instead of `select()` + `scalars()`
- Forgetting `status_code=303` on POST redirects
- Double quotes on `x-data` with `tojson`
- Inline badge/table HTML instead of macros
- Referencing removed domains (billing, NAS/RADIUS, catalog)
- Missing `is_active` filter on soft-deleted entities
