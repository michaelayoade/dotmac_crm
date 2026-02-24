# Template Rules (Jinja2 + HTMX + Alpine.js)

## Template Structure

Templates extend portal-specific layouts:
```html
{% extends "layouts/admin.html" %}
{% block title %}Page Title - Admin{% endblock %}
{% block content %}
    <nav class="flex items-center gap-2 text-sm text-slate-500">...</nav>
    <!-- Page content -->
{% endblock %}
```

## HTMX Patterns

- `hx-get` for data loading, `hx-post` for mutations
- `hx-trigger="load"` for lazy-loading partials
- `hx-swap="innerHTML"` for content replacement
- Partials prefixed with `_` (e.g., `_campaign_recipients_table.html`)
- Always include loading indicators (`.htmx-indicator`)

## Alpine.js Rules

- **CRITICAL**: Use single quotes for `x-data` with `tojson`:
  ```html
  <div x-data='{{ data | tojson }}'>  <!-- Correct -->
  <div x-data="{{ data | tojson }}">  <!-- WRONG - breaks on quotes -->
  ```
- Use `x-cloak` on initially hidden elements (prevents FOUC)
- Sidebar collapse state persisted in `localStorage`

## Enum Display

```html
{{ item.status.value | replace('_', ' ') | title }}
```

## None Handling

```html
{{ var if var else '' }}
{{ count | default(0) }}
```

## Dynamic Tailwind Classes

Use dict lookup, not string interpolation:
```html
{% set status_colors = {
    'active': 'bg-green-100 text-green-800',
    'inactive': 'bg-red-100 text-red-800',
} %}
<span class="{{ status_colors.get(item.status.value, 'bg-gray-100') }}">
```

## Status Badges

Always use the `status_badge()` macro — never write raw badge HTML:
```html
{{ status_badge(item.status.value) }}
```

## Confirmation Modals

For destructive actions:
```html
{{ danger_button("Delete", url="/admin/items/" ~ item.id ~ "/delete",
    confirm_title="Delete Item?",
    confirm_message="This action cannot be undone.") }}
```
