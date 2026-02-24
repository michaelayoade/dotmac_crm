---
name: add-page-template
description: Scaffold a complete admin page template using the Industrial Modern design system macros
arguments:
  - name: page_info
    description: "Page type and domain (e.g. 'detail page for fiber closures with map sidebar')"
---

# Add Page Template

Scaffold a complete Jinja2 admin page template for DotMac Omni CRM using the Industrial Modern design system.

## Steps

### 1. Determine page type and domain
Parse `$ARGUMENTS` to determine:
- **Page type**: list, detail, form, dashboard/hub, settings, inbox/3-panel
- **Domain**: determines color scheme (see table below)
- **Features needed**: filter bar, stats cards, tabs, map, HTMX partials

**Domain color scheme:**
| Domain | Colors | Tailwind |
|--------|--------|----------|
| Customers / Subscribers | amber + orange | `color="amber" color2="orange"` |
| Network / IP Management | cyan + blue | `color="cyan" color2="blue"` |
| Fiber / OLTs | violet + purple | `color="violet" color2="purple"` |
| Tickets / Support | rose + pink | `color="rose" color2="pink"` |
| Projects | emerald + teal | `color="emerald" color2="teal"` |
| System / Settings | indigo + violet | `color="indigo" color2="violet"` |

### 2. Study the macros
Read `templates/components/ui/macros.html` for available macros:

**Page structure macros:**
- `page_header(title, subtitle, icon, color, color2, actions, breadcrumbs)`
- `ambient_background(color1, color2)`
- `card()` / `card(end=true)` — card wrapper
- `tabs(items, active)` — tab navigation

**Data display macros:**
- `data_table()` / `data_table(end=true)` — table wrapper with overflow-x-auto
- `table_head(label, align, sortable)` — column header
- `table_row()` / `table_row(end=true)` — table row
- `row_actions()` / `row_action(label, url, icon)` — action dropdown
- `empty_state(title, message, action_url, action_label)`
- `pagination(page, total_pages, base_url)`
- `stats_card(title, value, subtitle, color, color2, icon, trend)`
- `status_badge(variant)` — semantic status badge

**Detail page macros:**
- `detail_header(title, subtitle, status, actions)` — entity header
- `info_row(label, value)` — key-value info row
- `icon_badge(icon, color)` — colored icon badge
- `avatar(name, size, url)` — user avatar
- `type_badge(label, color)` — colored type label

**Form macros:**
- `validated_input(name, label, type, value, required, error)` — form input with validation
- `typeahead_input(name, label, url, display_key)` — autocomplete input
- `submit_button(label)` / `loading_button(label)` — form submit
- `danger_button(label, url, confirm_title, confirm_message)` — destructive action

**Filter macros:**
- `filter_bar()` / `filter_bar(end=true)` — filter container
- `search_input(placeholder, name)` — search field
- `filter_select(name, label, options)` — dropdown filter

**Feedback macros:**
- `info_banner(title, message, variant)` — inline alert
- `spinner(size)` — loading spinner

### 3. Create the template

**List page template** — `templates/admin/{domain}/index.html`:
```html
{% extends "layouts/admin.html" %}
{% from "components/ui/macros.html" import page_header, stats_card, data_table,
    table_head, table_row, row_actions, row_action, empty_state, pagination,
    status_badge, action_button, filter_bar, search_input, filter_select %}

{% block title %}{Page Title} - Admin{% endblock %}

{% block content %}
<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
    {# Page header with domain colors #}
    {% call page_header("{Page Title}", "{Description}",
        icon='<svg class="h-7 w-7 text-white" ...>...</svg>',
        color="{color}", color2="{color2}") %}
        <a href="/admin/{route}/new"
           class="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-{color}-500 to-{color2}-600 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-{color}-500/25 btn-hover">
            <svg class="h-4 w-4" ...>...</svg>
            New Item
        </a>
    {% endcall %}

    {# Stats cards (optional) #}
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {{ stats_card("Total", total_count | default(0), color="{color}", color2="{color2}",
            icon='<svg class="h-5 w-5" ...>...</svg>') }}
        {{ stats_card("Active", active_count | default(0), color="green", color2="emerald",
            icon='<svg class="h-5 w-5" ...>...</svg>') }}
    </div>

    {# Filter bar #}
    {% call filter_bar() %}
        {{ search_input(placeholder="Search...", value=request.query_params.get('search', '')) }}
        {{ filter_select("status", "Status", [
            ("", "All Statuses"),
            ("active", "Active"),
            ("draft", "Draft"),
            ("archived", "Archived"),
        ], selected=request.query_params.get('status', '')) }}
    {% endcall %}

    {# Data table #}
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
                <td class="px-6 py-4">
                    <a href="/admin/{route}/{{ item.id }}"
                       class="font-medium text-slate-900 dark:text-white hover:text-{color}-600 dark:hover:text-{color}-400">
                        {{ item.name }}
                    </a>
                </td>
                <td class="px-6 py-4">{{ status_badge(item.status.value) }}</td>
                <td class="px-6 py-4 text-sm text-slate-500 dark:text-slate-400">
                    {{ item.created_at.strftime('%d %b %Y') }}
                </td>
                <td class="px-6 py-4 text-right">
                    {{ row_actions() }}
                        {{ row_action("View", "/admin/{route}/" ~ item.id) }}
                        {{ row_action("Edit", "/admin/{route}/" ~ item.id ~ "/edit") }}
                    {{ row_actions(end=true) }}
                </td>
            {{ table_row(end=true) }}
            {% endfor %}
        </tbody>
    {{ data_table(end=true) }}
    {% else %}
    {{ empty_state("No items found", "Create your first item to get started.",
        action_url="/admin/{route}/new", action_label="New Item") }}
    {% endif %}
</div>
{% endblock %}
```

**Detail page template** — `templates/admin/{domain}/detail.html`:
```html
{% extends "layouts/admin.html" %}
{% from "components/ui/macros.html" import page_header, card, info_row, status_badge,
    icon_badge, danger_button, action_button, tabs %}

{% block title %}{{ item.name }} - Admin{% endblock %}

{% block content %}
<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
    {# Page header with breadcrumbs #}
    {% call page_header(item.name, "",
        icon='<svg class="h-7 w-7 text-white" ...>...</svg>',
        color="{color}", color2="{color2}",
        breadcrumbs=[
            {"label": "{Domain}", "href": "/admin/{route}"},
            {"label": item.name}
        ]) %}
        <div class="flex items-center gap-3">
            <a href="/admin/{route}/{{ item.id }}/edit"
               class="inline-flex items-center gap-2 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 px-4 py-2 text-sm font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700">
                Edit
            </a>
            {{ danger_button("Delete", url="/admin/{route}/" ~ item.id ~ "/delete",
                confirm_title="Delete Item?",
                confirm_message="This action cannot be undone.") }}
        </div>
    {% endcall %}

    {# Two-column layout: 2/3 main + 1/3 sidebar #}
    <div class="grid gap-6 lg:grid-cols-3">
        {# Main content (2/3) #}
        <div class="lg:col-span-2 space-y-6">
            {{ card() }}
                <div class="px-6 py-4 border-b border-slate-200/60 dark:border-slate-700/60">
                    <h2 class="text-lg font-semibold text-slate-900 dark:text-white">Details</h2>
                </div>
                <div class="p-6 space-y-4">
                    {{ info_row("Status", status_badge(item.status.value)) }}
                    {{ info_row("Created", item.created_at.strftime('%d %b %Y at %H:%M')) }}
                    {{ info_row("Description", item.description or "No description") }}
                </div>
            {{ card(end=true) }}
        </div>

        {# Sidebar (1/3) #}
        <div class="space-y-6">
            {{ card() }}
                <div class="px-6 py-4 border-b border-slate-200/60 dark:border-slate-700/60">
                    <h2 class="text-lg font-semibold text-slate-900 dark:text-white">Info</h2>
                </div>
                <div class="p-6 space-y-3">
                    {# Sidebar content #}
                </div>
            {{ card(end=true) }}
        </div>
    </div>
</div>
{% endblock %}
```

**Form template** — `templates/admin/{domain}/form.html`:
```html
{% extends "layouts/admin.html" %}
{% from "components/ui/macros.html" import page_header, card, validated_input,
    submit_button, filter_select %}

{% block title %}{% if item %}Edit{% else %}New{% endif %} Item - Admin{% endblock %}

{% block content %}
<div class="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
    {{ page_header("{% if item %}Edit{% else %}New{% endif %} Item", "",
        color="{color}", color2="{color2}") }}

    <form method="POST"
          action="/admin/{route}{% if item %}/{{ item.id }}{% endif %}">
        <input type="hidden" name="_csrf_token" value="{{ request.state.csrf_token }}">

        {{ card() }}
            <div class="p-6 space-y-5">
                {{ validated_input("name", "Name", type="text",
                    value=item.name if item else "",
                    required=true, error=errors.name if errors is defined else "") }}

                {{ validated_input("description", "Description", type="textarea",
                    value=item.description if item else "",
                    error=errors.description if errors is defined else "") }}
            </div>
            <div class="px-6 py-4 bg-slate-50 dark:bg-slate-800/50 border-t border-slate-200/60 dark:border-slate-700/60 flex justify-end gap-3">
                <a href="/admin/{route}"
                   class="rounded-xl px-4 py-2 text-sm font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700">
                    Cancel
                </a>
                {{ submit_button("Save") }}
            </div>
        {{ card(end=true) }}
    </form>
</div>
{% endblock %}
```

### 4. Create HTMX partial (if needed)
For lazy-loaded or dynamically updated sections, create `templates/admin/{domain}/_{partial}.html`:

```html
{# No layout extension — this is a partial #}
{% from "components/ui/macros.html" import status_badge, spinner %}

{% if items %}
<table class="min-w-full">
    {# Table content #}
</table>
{% else %}
<p class="text-center text-sm text-slate-500 dark:text-slate-400 py-8">No items found</p>
{% endif %}
```

Wire it up in the parent template:
```html
<div hx-get="/admin/{route}/partials/{partial}"
     hx-trigger="load"
     hx-swap="innerHTML"
     class="relative min-h-[200px]">
    <div class="htmx-indicator absolute inset-0 flex items-center justify-center">
        {{ spinner("md") }}
    </div>
</div>
```

### 5. Dark mode verification
Every element must have both light and dark variants:

| Element | Light | Dark |
|---------|-------|------|
| Page bg | `bg-slate-100` | `dark:bg-slate-900` |
| Card | `bg-white` | `dark:bg-slate-800` |
| Card border | `border-slate-200/60` | `dark:border-slate-700/60` |
| Primary text | `text-slate-900` | `dark:text-white` |
| Secondary text | `text-slate-500` | `dark:text-slate-400` |
| Input bg | `bg-slate-50/50` | `dark:bg-slate-700/50` |
| Links | `text-{color}-600` | `dark:text-{color}-400` |
| Hover states | `hover:bg-slate-50` | `dark:hover:bg-slate-700` |

### 6. UI/UX checklist
- [ ] Extends `layouts/admin.html`
- [ ] Imports macros from `components/ui/macros.html`
- [ ] Correct domain color scheme (`color` + `color2`)
- [ ] `page_header()` with icon, breadcrumbs (detail pages)
- [ ] `status_badge()` for all status displays (never raw HTML badges)
- [ ] `danger_button()` with confirmation modal for destructive actions
- [ ] `animate-fade-in-up` on card entrances
- [ ] All dark mode variants present
- [ ] Responsive: `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3` grids
- [ ] Tables wrapped in `overflow-x-auto` (via `data_table()` macro)
- [ ] Touch targets minimum 40px on mobile
- [ ] `aria-label` on icon-only buttons
- [ ] `aria-hidden="true"` on decorative SVGs
- [ ] Form inputs have CSRF token
- [ ] HTMX partials prefixed with `_`
- [ ] No hardcoded hex colors (use Tailwind classes)
- [ ] `x-cloak` on Alpine.js initially hidden elements
