---
name: run-audit
description: Run a comprehensive code quality and security audit on a module
arguments:
  - name: module
    description: "Module to audit (e.g. 'crm', 'tickets', 'network', 'services/crm', or 'all')"
---

# Run Code Audit

Perform a comprehensive audit of DotMac Omni CRM code quality and security.

## Steps

### 0. Resolve target paths first
Choose paths that exist for the requested module:

- For `all`:
  - Type check: `mypy app/`
  - Lint: `ruff check app/ --statistics`
- For module names that are files (e.g. `tickets`):
  - Type check: `mypy app/services/$ARGUMENTS.py app/models/$ARGUMENTS.py app/web/admin/$ARGUMENTS.py 2>&1`
  - Lint: `ruff check app/services/$ARGUMENTS.py app/models/$ARGUMENTS.py app/web/admin/$ARGUMENTS.py --statistics 2>&1`
- For module names that are directories (e.g. `crm`, `network`):
  - Type check: `mypy app/services/$ARGUMENTS app/models/$ARGUMENTS app/web/admin/$ARGUMENTS.py 2>&1`
  - Lint: `ruff check app/services/$ARGUMENTS app/models/$ARGUMENTS app/web/admin/$ARGUMENTS.py --statistics 2>&1`

If a path does not exist, skip it instead of failing the whole audit.

### 1. Type safety audit
```bash
mypy <resolved_paths> 2>&1
```
Report all type errors. For each error, explain the fix.

### 2. Lint audit
```bash
ruff check <resolved_paths> --statistics 2>&1
```
Report rule violation counts and patterns.

### 3. Security audit
Check for:

**Path traversal:**
- File operations without `.resolve()` + `.relative_to()` validation
- User-supplied filenames used directly in `open()` or `Path()`

**SQL injection:**
- String formatting in queries instead of parameterized
- f-strings or `.format()` in `.filter()` / `.execute()` calls
- Raw SQL without bind parameters

**CSRF:**
- POST/PUT/DELETE routes missing CSRF token validation
- HTMX POST requests without `hx-headers` CSRF

**Auth bypass:**
- Routes missing `Depends(require_permission(...))` or equivalent
- Web routes without `get_current_user()` check

**Secrets in code:**
- API keys, passwords, tokens hardcoded instead of in env vars
- DSN strings with credentials in source files

**File upload:**
- Size validated BEFORE write, not after
- MIME type validation present
- UUID-based storage names (not user-supplied filenames)

### 4. Service layer violations
Check for business logic in wrong places:

**Routes should NOT contain:**
- `db.query()` or `db.add()` calls
- Complex if/else business logic
- Direct model manipulation
- Notification/email sending

**Tasks should NOT contain:**
- Direct DB queries (only service delegation)
- Business logic (only orchestration)

**All complex logic should be in `app/services/`.**

### 5. N+1 query detection
Look for:
- Queries inside `for` loops
- Relationship access in template loops without eager loading
- Missing `joinedload()` or `selectinload()` on accessed relationships

### 6. Template audit
Check templates in the module for:
- Missing `dark:` variants on color classes
- Hardcoded hex colors in inline styles
- Missing `aria-label` on icon-only buttons
- Status indicators using only color (no icon differentiation)
- Missing `x-cloak` on Alpine.js elements
- HTMX requests without loading indicators

### 7. Test coverage gaps
```bash
pytest tests/ -k "$ARGUMENTS" --cov=app/services --cov-report=term-missing 2>&1
```
Identify untested service methods.

### 8. Generate report
Output a markdown report with:
- **P0 (Critical)**: Security vulnerabilities, auth bypass, SQL injection
- **P1 (High)**: Type errors, missing auth, service layer violations, N+1 queries
- **P2 (Medium)**: Missing tests, lint warnings, template accessibility issues
- **P3 (Low)**: Style inconsistencies, dead code, missing dark mode variants

Format:
```markdown
## Audit Report: {module}
*Generated: {date}*

### P0 - Critical ({count})
| # | File | Line | Issue | Fix |
|---|------|------|-------|-----|

### P1 - High ({count})
...

### Summary
- Type errors: X
- Lint violations: X
- Security issues: X
- Service layer violations: X
- Test coverage: X%
```
