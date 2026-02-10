# AGENTS.md - Codex Guidance for DotMac CRM

This file provides short, high-signal guidance for Codex when working in this repo. For detailed conventions, see `CLAUDE.md`.

## Scope
- This file applies only to this repository.
- It does not affect global Codex behavior or other projects.

## Quick Start
- Check `CLAUDE.md` for architecture, UI system, and service-layer rules.
- Business logic belongs in `app/services/`; routes are thin wrappers.
- Use shared Jinja2 macros from `templates/components/ui/macros.html`.

## Safety Guardrails
- Do not reintroduce removed legacy domains (billing/catalog/nas/radius/usage/collections).
- Avoid N+1 queries; batch or eager-load.
- Use POST-Redirect-GET with `status_code=303`.
- Add dark mode variants for any new UI classes.

## Validation
- Python lint/format: `ruff check app/ tests/ --fix && ruff format app/ tests/`
- Type check: `mypy app/`
- Security scan: `bandit -r app -q`
- Tests: `pytest` (or targeted module)

## Where to Look
- Architecture and patterns: `CLAUDE.md`
- Key service files: `app/services/`
- UI system: `templates/components/ui/macros.html` and `docs/design-guide.html`
