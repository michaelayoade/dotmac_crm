"""Guard CRM's documented role as sales-retirement evidence, not authority."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETIREMENT = ROOT / "docs" / "sales-authority-retirement.md"

PINS = (
    "57e112f0757edcee6b9ad625ee3e13ebff5c7d71",
    "f64946fc451ba94a1d4c8f0a61b7831367d5b598",
    "7828697ef11fb1ae765a5397dfa7dc221ae6207a",
    "2749ec5396cbbd7a1132b394e85855a1d133a7cd",
)


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _authority_errors(text: str) -> tuple[str, ...]:
    normalized = _normalized(text)
    required = {
        "sub current owner": "**Current sales system of record:** Sub",
        "crm retirement only": ("CRM's local sales implementation is parity, migration and writer-retirement evidence"),
        "accepted boundary": ("The reusable boundary stops at an accepted, immutable Quote"),
        "orders excluded": "It does not include SalesOrder rows",
        "campaign unverified": "CRM's campaign rows remain unverified",
        "retention unresolved": ("Retention guidance conflicts and needs an explicit owner decision"),
        "not complete": "this retirement is **not complete**",
    }
    return tuple(name for name, phrase in required.items() if phrase not in normalized)


def test_sales_retirement_inventory_is_pinned_and_explicit() -> None:
    text = RETIREMENT.read_text(encoding="utf-8")

    assert _authority_errors(text) == ()
    for revision in PINS:
        assert revision in text


def test_sales_authority_guard_is_sensitive() -> None:
    text = _normalized(RETIREMENT.read_text(encoding="utf-8"))
    required = (
        "**Current sales system of record:** Sub",
        "CRM's local sales implementation is parity, migration and writer-retirement evidence",
        "The reusable boundary stops at an accepted, immutable Quote",
        "It does not include SalesOrder rows",
        "CRM's campaign rows remain unverified",
        "Retention guidance conflicts and needs an explicit owner decision",
        "this retirement is **not complete**",
    )

    for phrase in required:
        violated = text.replace(phrase, "removed by sensitivity canary", 1)
        assert _authority_errors(violated), phrase


def test_no_checked_in_guide_still_calls_crm_the_quote_owner() -> None:
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    portal_rfc = (ROOT / "docs" / "integration" / "portal-api-and-event-bus.md").read_text(encoding="utf-8")

    assert "system-of-record** for work orders / projects / quotes" not in claude
    assert "CRM is **not** the system of record for customer-sales" in claude
    assert "not the\nsales source of truth" in architecture
    assert "Sales authority correction — 2026-08-17" in portal_rfc
