---
name: add-web-route
description: Add a new admin web route with template, sidebar integration, and HTMX support
arguments:
  - name: route_info
    description: "Route info (e.g. 'admin/inventory/reservations list+detail+form')"
---

# Add Web Route

Add a server-rendered admin web route to DotMac Omni CRM.

## Steps

### 1. Determine the module and domain colors
Map the route to the correct domain color scheme:

| Domain | Colors | Tailwind |
|--------|--------|----------|
| Customers / Subscribers | amber + orange | `color="amber" color2="orange"` |
| Network / IP Management | cyan + blue | `color="cyan" color2="blue"` |
| Fiber / OLTs | violet + purple | `color="violet" color2="purple"` |
| POP Sites / Assignments | teal + emerald | `color="teal" color2="emerald"` |
| System / Settings | indigo + violet | `color="indigo" color2="violet"` |
| Tickets / Support | rose + pink | `color="rose" color2="pink"` |
| Projects | emerald + teal | `color="emerald" color2="teal"` |

### 2. Create web route
In `app/web/admin/{module}.py`:

```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.auth_dependencies import require_permission
from app.services import {module} as {module}_service
from app.web.admin import build_admin_context, get_current_user, get_sidebar_stats

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/{module}", tags=["web-admin-{module}"])


def _base_ctx(request: Request, db: Session, **kwargs) -> dict:
    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)
    return {
        "request": request,
        "current_user": current_user,
        "sidebar_stats": sidebar_stats,
        "active_page": "{module}",
        **kwargs,
    }


@router.get("", dependencies=[Depends(require_permission("domain:resource:read"))])
def list_view(request: Request, db: Session = Depends(get_db)):
    items = {module}_service.entities.list(db)
    ctx = _base_ctx(request, db, items=items)
    return templates.TemplateResponse(request, "admin/{module}/index.html", ctx)


@router.get("/new", dependencies=[Depends(require_permission("domain:resource:create"))])
def create_form(request: Request, db: Session = Depends(get_db)):
    ctx = _base_ctx(request, db)
    return templates.TemplateResponse(request, "admin/{module}/form.html", ctx)


@router.post("", dependencies=[Depends(require_permission("domain:resource:create"))])
def create(request: Request, db: Session = Depends(get_db)):
    # Parse form data, create entity
    return RedirectResponse(url=f"/admin/{module}", status_code=303)


@router.get("/{item_id}", dependencies=[Depends(require_permission("domain:resource:read"))])
def detail_view(item_id: str, request: Request, db: Session = Depends(get_db)):
    item = {module}_service.entities.get(db, item_id)
    ctx = _base_ctx(request, db, item=item)
    return templates.TemplateResponse(request, "admin/{module}/detail.html", ctx)


@router.get("/{item_id}/edit", dependencies=[Depends(require_permission("domain:resource:update"))])
def edit_form(item_id: str, request: Request, db: Session = Depends(get_db)):
    item = {module}_service.entities.get(db, item_id)
    ctx = _base_ctx(request, db, item=item)
    return templates.TemplateResponse(request, "admin/{module}/form.html", ctx)


@router.post("/{item_id}", dependencies=[Depends(require_permission("domain:resource:update"))])
def update(item_id: str, request: Request, db: Session = Depends(get_db)):
    # Parse form data, update entity
    return RedirectResponse(url=f"/admin/{module}/{item_id}", status_code=303)


@router.post("/{item_id}/delete", dependencies=[Depends(require_permission("domain:resource:delete"))])
def delete(item_id: str, request: Request, db: Session = Depends(get_db)):
    {module}_service.entities.delete(db, item_id)
    return RedirectResponse(url=f"/admin/{module}", status_code=303)
```

### 3. Register route
Add to `app/web/admin/__init__.py`:
```python
from app.web.admin.{module} import router as {module}_router
admin_router.include_router({module}_router)
```

### 4. Create template
Create `templates/admin/{module}/index.html`:

```html
{% extends "layouts/admin.html" %}
{% from "components/ui/macros.html" import page_header, data_table, table_head,
    table_row, row_actions, row_action, empty_state, pagination, status_badge,
    action_button, filter_bar, search_input, filter_select %}

{% block title %}Page Title - Admin{% endblock %}

{% block content %}
<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
    {{ page_header("Page Title", "Description", icon="icon-name",
        color="amber", color2="orange",
        action_url="/admin/{module}/new", action_label="New Item") }}

    {{ filter_bar() }}
        {{ search_input(placeholder="Search...") }}
        {{ filter_select("status", "Status", [
            ("", "All"),
            ("draft", "Draft"),
            ("active", "Active"),
        ]) }}
    {{ filter_bar(end=true) }}

    {% if items %}
    {{ data_table() }}
        <thead>
            <tr>
                {{ table_head("Name") }}
                {{ table_head("Status") }}
                {{ table_head("Created") }}
                {{ table_head("", align="right") }}
            </tr>
        </thead>
        <tbody>
            {% for item in items %}
            {{ table_row() }}
                <td class="px-6 py-4">{{ item.name }}</td>
                <td class="px-6 py-4">{{ status_badge(item.status.value) }}</td>
                <td class="px-6 py-4">{{ item.created_at.strftime('%d %b %Y') }}</td>
                <td class="px-6 py-4 text-right">
                    {{ row_actions() }}
                        {{ row_action("View", "/admin/{module}/" ~ item.id) }}
                        {{ row_action("Edit", "/admin/{module}/" ~ item.id ~ "/edit") }}
                    {{ row_actions(end=true) }}
                </td>
            {{ table_row(end=true) }}
            {% endfor %}
        </tbody>
    {{ data_table(end=true) }}
    {% else %}
    {{ empty_state("No items found", "Create your first item to get started.",
        action_url="/admin/{module}/new", action_label="New Item") }}
    {% endif %}
</div>
{% endblock %}
```

### 5. Add sidebar link
Edit `templates/components/navigation/admin_sidebar.html`:
- Find the correct section (Customers, Network, Operations, etc.)
- Add a nav link matching existing patterns
- Include permission check: `{% if can_{module} %}`

### 6. UI/UX checklist
- [ ] Uses macros from `components/ui/macros.html`
- [ ] Correct domain colors
- [ ] Dark mode variants on all elements
- [ ] `animate-fade-in-up` on cards
- [ ] `status_badge()` for status columns
- [ ] `danger_button()` with confirm modal for deletes
- [ ] Responsive: `overflow-x-auto` on tables
- [ ] `aria-label` on icon-only buttons

### 7. Verify
```bash
python -c "from app.web.admin.{module} import router"
docker compose restart app
# Get fresh JWT and test
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/admin/{module}
```
