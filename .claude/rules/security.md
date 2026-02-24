# Security Rules

## Authentication

- Admin: `/auth/login` with RBAC
- Customer: `/portal/auth/login`
- Vendor: `/vendor/auth/login`
- Reseller: `/reseller/auth/login`

All routes must have permission checks via `Depends(require_permission("domain:resource:action"))`.

## CSRF Protection

- Double-submit cookie pattern: `csrf_token` cookie must match form field/header
- All POST/PUT/DELETE web routes must validate CSRF
- HTMX requests: automatic via `base.html` config

## Input Validation

- Use Pydantic schemas for all request validation
- Use `validate_enum()` for enum values — never trust string inputs
- Use `coerce_uuid()` for ID parameters

## File Uploads

- Validate size BEFORE write
- Use UUID-based storage names (not user-supplied filenames)
- Validate MIME types
- Use `resolve_safe_path()` for path traversal prevention

## Query Safety

- Never use f-strings or `.format()` in SQL queries
- Use SQLAlchemy ORM or parameterized queries exclusively
- Use `with_for_update(skip_locked=True)` for concurrent safety

## Secrets

- All secrets in environment variables or `.env`
- Never hardcode credentials in source files
- JWT secrets minimum 24 bytes (192 bits)
- Use OpenBao for production secret management

## Error Handling

- Never use bare `except:` — always catch specific exceptions
- Template output: `| safe` only for CSRF tokens, `tojson`, admin CSS
- Log sensitive errors but don't expose details to users
