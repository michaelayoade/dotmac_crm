# Pivot Table & Report Macro Suite

## Problem

~594 lines of duplicated/consolidatable table markup across 9 report templates. Existing `table_shell`/`table_head` macros cover ~40% of table patterns but leave report-specific patterns (leaderboards, score cells, progress bars, cross-tabs) unabstracted.

## Audit Summary

| Template | Lines | Table Markup | Pattern |
|----------|-------|-------------|---------|
| `performance/_leaderboard_table.html` | 112 | 112 | Flat ranked list with mini progress bars |
| `reports/technician.html` | 202 | ~80 | Flat ranked list with rating stars |
| `reports/network.html` | 216 | ~78 | Summary breakdowns + progress bars |
| `network/fiber/reports.html` | 339 | ~113 | Asset inventory + cable breakdowns |
| `data_quality/_entity_table.html` | 60 | 60 | Quality scores with field chips |
| `data_quality/domain_detail.html` | 122 | ~50 | Quality breakdown with progress bars |
| `reports/crm_performance.html` | 55 | ~30 | Agent/team conversation metrics |
| `data_quality/index.html` | 105 | ~40 | Data quality dashboard |
| `performance/agent_detail.html` | 147 | ~31 | Individual agent scoring detail |
| **Total** | **1,358** | **~594** | |

### Duplication Hotspots

- **Rank badge logic** (gold/silver/bronze) — 4 places, 5 lines each
- **Progress bar + percentage** — 4+ places, 7-11 lines each
- **Score color thresholds** (`>=70` green, `>=40` amber, `<40` red) — repeated everywhere
- **Leaderboard row structure** (avatar + name + N metric cells) — 35 lines per variant
- **Empty state fallbacks** — 12 lines x 9 templates

## Proposed Macros

All macros go in `templates/components/ui/macros.html` alongside existing macros.

### 1. `pivot_table(rows, cols, values, totals)`

Server-rendered cross-tab macro. No JS dependency, prints cleanly, works with existing stack.

**Parameters:**
- `rows` — list of dicts, each with a `label` key and data values
- `cols` — list of column header strings
- `values` — 2D data keyed by `row_label -> col_label -> value`
- `totals` — dict with `row_totals`, `col_totals`, `grand_total` (all optional)
- `title` — table title (optional)
- `value_format` — `"number"`, `"currency"`, `"percent"` (default: `"number"`)
- `color` — Tailwind color for header gradient (default: `"slate"`)
- `empty_message` — shown when no data

**Enables:** Revenue by Month x Department, Tickets by Status x Priority, Conversations by Channel x Agent, etc.

**Example usage:**
```html
{% from "components/ui/macros.html" import pivot_table %}

{{ pivot_table(
    rows=[{"label": "Sales"}, {"label": "Support"}, {"label": "Field"}],
    cols=["Jan", "Feb", "Mar", "Q1"],
    values={
        "Sales":   {"Jan": 12, "Feb": 15, "Mar": 18, "Q1": 45},
        "Support": {"Jan": 8,  "Feb": 10, "Mar": 12, "Q1": 30},
        "Field":   {"Jan": 5,  "Feb": 7,  "Mar": 9,  "Q1": 21},
    },
    totals={
        "col_totals": {"Jan": 25, "Feb": 32, "Mar": 39, "Q1": 96},
        "grand_total": 96
    },
    title="Conversations by Department",
    value_format="number",
    color="cyan"
) }}
```

### 2. `leaderboard_table(data, columns, rank)`

Ranked list with optional position badges, avatars, and multi-metric columns.

**Parameters:**
- `data` — list of row dicts
- `columns` — list of `{"key": "...", "label": "...", "format": "number|percent|rating|progress"}`
- `rank` — bool, show rank badges (default: `true`)
- `avatar_key` — dict key for avatar initials (optional)
- `name_key` — dict key for display name (default: `"name"`)
- `color` — Tailwind color theme

**Consolidates:** `_leaderboard_table.html` (112 lines) and `technician.html` table section (~80 lines).

**Example usage:**
```html
{{ leaderboard_table(
    data=technicians,
    columns=[
        {"key": "total_jobs", "label": "Jobs", "format": "number"},
        {"key": "completion_rate", "label": "Completion", "format": "percent"},
        {"key": "avg_hours", "label": "Avg Hours", "format": "number"},
        {"key": "rating", "label": "Rating", "format": "rating"},
    ],
    name_key="technician_name",
    avatar_key="technician_name",
    color="cyan"
) }}
```

### 3. `score_cell(value, thresholds)`

Colored score display with consistent threshold logic.

**Parameters:**
- `value` — numeric score (0-100)
- `thresholds` — optional dict override, default: `{"good": 70, "warn": 40}`
- `show_bar` — bool, show mini progress bar below value (default: `false`)
- `size` — `"sm"`, `"md"`, `"lg"` (default: `"md"`)

**Color logic (default thresholds):**
- `>= 70` — emerald (good)
- `>= 40` — amber (warning)
- `< 40` — rose (danger)

**Consolidates:** Score color logic duplicated across 6+ templates (~80 lines total).

### 4. `progress_bar(pct, color, size)`

Reusable progress bar with percentage label.

**Parameters:**
- `pct` — percentage value (0-100)
- `color` — Tailwind color (default: auto from thresholds)
- `size` — `"xs"`, `"sm"`, `"md"` (bar height, default: `"sm"`)
- `show_label` — bool, show percentage text (default: `true`)
- `label_position` — `"right"`, `"inside"` (default: `"right"`)

**Consolidates:** 4+ duplicated progress bar patterns (~40 lines total).

### 5. `rank_badge(position)`

Medal-style rank indicator for leaderboard positions.

**Parameters:**
- `position` — integer rank (1, 2, 3, or higher)

**Renders:**
- 1st: gold badge
- 2nd: silver badge
- 3rd: bronze badge
- 4th+: plain number

**Consolidates:** Rank badge logic in 4 places (~20 lines total).

## Estimated Impact

| Macro | Existing Lines Replaced | New Views Enabled |
|-------|------------------------|-------------------|
| `pivot_table()` | ~0 (no existing cross-tabs) | Revenue x Month, Tickets x Status x Priority, Channel x Agent |
| `leaderboard_table()` | ~150 lines | Any new ranked list |
| `score_cell()` | ~80 lines | Consistent scoring everywhere |
| `progress_bar()` | ~40 lines | Reusable across all reports |
| `rank_badge()` | ~20 lines | Any ranked display |
| **Total** | **~290 lines reduced** | **Trivial to add new cross-tab and leaderboard views** |

## Implementation Order

1. **`progress_bar()`** + **`score_cell()`** — smallest, most reused, zero-risk refactor
2. **`rank_badge()`** — tiny, used by leaderboard
3. **`leaderboard_table()`** — consolidates 2 existing templates
4. **`pivot_table()`** — new capability, no existing templates to refactor

## Design Rules

- All macros follow Industrial Modern design system (rounded-2xl cards, dark mode, Outfit headings)
- All macros support dark mode (`dark:` variants on every color)
- `pivot_table()` totals row/column use `font-semibold bg-slate-50 dark:bg-slate-700/50`
- Print-friendly: no JS dependencies, semantic `<table>` elements, visible borders in print
- Accessible: `<th scope="col|row">`, proper `<caption>`, `aria-label` on interactive elements

## Data Preparation (Service Layer)

The `pivot_table()` macro expects pre-pivoted data from the service layer. Add a generic helper:

```python
# app/services/reports.py

def pivot_data(
    rows: list[dict],
    row_key: str,
    col_key: str,
    value_key: str,
    agg: str = "sum",  # "sum", "count", "avg"
) -> dict:
    """Transform flat query results into pivot_table() macro format.

    Returns {"rows": [...], "cols": [...], "values": {...}, "totals": {...}}
    """
```

This keeps the macro pure (rendering only) and the service layer responsible for aggregation.
