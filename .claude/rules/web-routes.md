# Web Route Rules

## Route Structure

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

## POST Redirects

Always use `status_code=303` for POST redirects:
```python
return RedirectResponse(url=f"/admin/items/{item.id}", status_code=303)
```

## Context Helper Pattern

```python
def _base_ctx(request: Request, db: Session, **kwargs) -> dict:
    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)
    return {"request": request, "current_user": current_user,
            "sidebar_stats": sidebar_stats, "active_page": "page_name", **kwargs}
```

## Prohibited in Routes

Routes must NEVER contain:
- `db.query()` or `db.add()` calls
- Complex business logic (if/else chains)
- Direct model manipulation
- Email/notification sending
- Calculations or data transformations

All of these belong in the service layer.

## HTMX Partials

- Prefix partial templates with `_` (e.g., `_ticket_table.html`)
- HTMX endpoints need full context (use `build_admin_context()`)
- Include CSRF token (automatic via `base.html` config)
- POST mutations use `status_code=303` redirect (PRG pattern)

## Toast Notifications

```python
headers = {"HX-Trigger": json.dumps({"showToast": {"message": "Saved!", "type": "success"}})}
return RedirectResponse(url=url, status_code=303, headers=headers)
```
