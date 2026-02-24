---
name: visual-qa
description: Audit a page's visual design against the Industrial Modern design system using Playwright
arguments:
  - name: page_url
    description: "URL to audit (e.g. '/admin/support/tickets', '/admin/crm/inbox')"
---

# Visual QA

Audit a web page against the DotMac Industrial Modern design system using
Playwright MCP for browser interaction and inspection.

## Audit Workflow

### Step 1: Navigate and Capture

1. Navigate to the target URL (prefix with `http://localhost:8000` if relative)
2. Login first if needed using environment-provided test credentials (never hardcode credentials)
3. Take an accessibility snapshot of the page
4. Take a screenshot in light mode
5. Toggle dark mode (click the dark mode toggle or execute JS: `document.documentElement.classList.toggle('dark')`)
6. Take a screenshot in dark mode

### Step 2: Design System Checklist

Inspect the accessibility snapshot and screenshots against these rules:

**Typography:**
- [ ] Page heading uses Outfit font (h1-h3 are auto-styled)
- [ ] Body text uses Plus Jakarta Sans (default)
- [ ] Metric values use `font-display text-3xl font-bold`
- [ ] Labels use `text-xs font-semibold uppercase tracking-wide`

**Border Radius:**
- [ ] Cards use `rounded-2xl` (16px)
- [ ] Buttons use `rounded-xl` (12px)
- [ ] Badges use `rounded-lg` (8px)
- [ ] Avatars use `rounded-full`

**Colors (Domain-Specific):**
| Domain | Expected Colors |
|--------|----------------|
| Customers | amber + orange |
| Network | cyan + blue |
| Fiber | violet + purple |
| Tickets | rose + pink |
| Projects | emerald + teal |
| System | indigo + violet |

**Component Macros:**
- [ ] Page header uses `page_header()` macro
- [ ] Status badges use `status_badge()` macro
- [ ] Tables use `data_table()` + `table_head()` + `table_row()`
- [ ] Empty states use `empty_state()` macro
- [ ] Buttons use `action_button()` / `submit_button()` / `danger_button()`

**Dark Mode:**
- [ ] Every background has a `dark:` variant
- [ ] Text colors have `dark:` counterparts
- [ ] Borders use opacity variants (`border-slate-200/60 dark:border-slate-700/60`)
- [ ] No hardcoded hex colors in inline styles
- [ ] Cards: `bg-white dark:bg-slate-800`
- [ ] Page: `bg-slate-100 dark:bg-slate-900`

**Animations:**
- [ ] Cards have `animate-fade-in-up` entrance
- [ ] Buttons have hover effects (translateY, shadow)
- [ ] Cards have hover effects (translateY, deeper shadow)

**Accessibility:**
- [ ] Icon-only buttons have `aria-label`
- [ ] Dropdowns have `aria-expanded` + `aria-controls`
- [ ] Status badges include icon + color (not color alone)
- [ ] Form inputs have visible labels
- [ ] Decorative SVGs use `aria-hidden="true"`
- [ ] Proper heading hierarchy (h1 > h2 > h3)
- [ ] Focus rings visible on interactive elements

**Responsive:**
- [ ] Tables wrapped in `overflow-x-auto`
- [ ] Grids collapse: `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3`
- [ ] Touch targets minimum 40px
- [ ] Page header responsive: `flex-col gap-4 sm:flex-row`

**HTMX & Alpine.js:**
- [ ] HTMX partials have loading indicators
- [ ] `x-cloak` on initially hidden Alpine elements
- [ ] No FOUC (flash of unstyled content)

### Step 3: Check Console Errors

Use Playwright's `browser_console_messages` tool:
- Filter for `error` and `warning` levels
- Report any JavaScript errors

### Step 4: Report

```markdown
## Visual QA Report: [Page Name]
*URL: [url] | Date: [date]*

### Overall: PASS / WARN / FAIL

### Design System Compliance
| Category | Status | Issues |
|----------|--------|--------|
| Typography | PASS | |
| Border Radius | PASS | |
| Domain Colors | WARN | Using wrong accent color |
| Component Macros | PASS | |
| Dark Mode | FAIL | Card missing dark:bg variant |
| Animations | PASS | |
| Accessibility | WARN | 2 icon buttons missing aria-label |
| Responsive | PASS | |

### Detailed Issues
1. **[P1]** `dark:bg-slate-800` missing on `.card-wrapper` at line ~45
2. **[P2]** Icon button for "delete" at row 3 missing `aria-label`

### Screenshots
- Light mode: [saved/referenced]
- Dark mode: [saved/referenced]

### Console Errors
- [None / list any errors]
```
