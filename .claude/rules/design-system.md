# Design System Rules — "Industrial Modern"

## Color System

**Primary**: Teal/Cyan `#06b6d4` | **Accent**: Warm Orange `#f97316`

**Domain colors (must be consistent):**
| Domain | Colors | Tailwind |
|--------|--------|----------|
| Customers / Subscribers | amber + orange | `color="amber" color2="orange"` |
| Network / IP Management | cyan + blue | `color="cyan" color2="blue"` |
| Fiber / OLTs | violet + purple | `color="violet" color2="purple"` |
| Tickets / Support | rose + pink | `color="rose" color2="pink"` |
| Projects | emerald + teal | `color="emerald" color2="teal"` |
| System / Settings | indigo + violet | `color="indigo" color2="violet"` |

**CRM channel colors** (CSS custom properties in `main.css`):
Email=violet, WhatsApp=green, SMS=orange, Telegram=sky, Webchat=amber, Facebook=blue, Instagram=pink

## Typography

| Usage | Font | Class |
|-------|------|-------|
| Display headings (h1-h3) | Outfit | `.font-display` (auto via CSS) |
| Body text | Plus Jakarta Sans | `.font-body` (default) |
| Metric values | Outfit | `.font-display text-3xl font-bold` |
| Labels/captions | Plus Jakarta Sans | `text-xs font-semibold uppercase tracking-wide` |

Fonts self-hosted in `static/fonts/`.

## Border Radius

- `rounded-lg` (8px): Badges, table header icons
- `rounded-xl` (12px): **Buttons**, **inputs**, tabs
- `rounded-2xl` (16px): **Cards**, table wrappers, filter bars
- `rounded-full`: Avatars, notification dots

## Component Macros

**Always import from `components/ui/macros.html`:**
```html
{% from "components/ui/macros.html" import page_header, stats_card, data_table,
    table_head, table_row, row_actions, row_action, empty_state, pagination,
    status_badge, action_button, card, filter_bar, search_input, filter_select,
    submit_button, danger_button, warning_button, loading_button, spinner %}
```

Never duplicate these patterns in raw HTML.

## Dark Mode

Every element needs light + dark variants:

| Element | Light | Dark |
|---------|-------|------|
| Page bg | `bg-slate-100` | `dark:bg-slate-900` |
| Card | `bg-white` | `dark:bg-slate-800` |
| Card border | `border-slate-200/60` | `dark:border-slate-700/60` |
| Primary text | `text-slate-900` | `dark:text-white` |
| Secondary text | `text-slate-500` | `dark:text-slate-400` |
| Input bg | `bg-slate-50/50` | `dark:bg-slate-700/50` |
| Input border | `border-slate-200` | `dark:border-slate-600` |

**Never** use inline `style="color: #xxx"` without a dark mode equivalent.

## Animations

- `animate-fade-in-up`: Card entrance (0.5s)
- `.btn-hover`: translateY(-1px) + shadow
- `.card-hover`: translateY(-2px) + deeper shadow
- All respect `prefers-reduced-motion: reduce`

## Accessibility (WCAG 2.1 AA)

- Status badges: icon + color (not color alone)
- Icon-only buttons: `aria-label` required
- Dropdowns: `aria-expanded` + `aria-controls`
- Form inputs: `aria-invalid` + `aria-describedby` for errors
- Decorative SVGs: `aria-hidden="true"`
- Focus rings: `focus:ring-2 focus:ring-{color}-500/20`
- Touch targets: minimum 40px

## Responsive

- Sidebar: fixed desktop (`lg:`), overlay mobile
- Content: `max-w-7xl`
- Grids: `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3`
- Tables: always `overflow-x-auto`
