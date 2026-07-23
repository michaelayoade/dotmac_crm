"""Shared external-system identifiers used by subscriber integrations."""

SELFCARE_EXTERNAL_SYSTEM = "selfcare"
SPLYNX_EXTERNAL_SYSTEM = "splynx"
EXTERNAL_SUBSCRIBER_SYSTEMS = {SELFCARE_EXTERNAL_SYSTEM, SPLYNX_EXTERNAL_SYSTEM}


def selfcare_subscriber_number_for_splynx_id(splynx_id: object) -> str | None:
    """Return the canonical dotmac_sub number for a migrated Splynx customer."""
    raw = str(splynx_id or "").strip()
    if not raw.isdigit():
        return None
    return "100" + raw.zfill(6)


def splynx_id_from_selfcare_subscriber_number(subscriber_number: object) -> str | None:
    """Recover a legacy Splynx id only from the canonical migrated-number shape."""
    number = str(subscriber_number or "").strip()
    if not number.startswith("100") or not number[3:].isdigit():
        return None
    legacy_id = str(int(number[3:]))
    if selfcare_subscriber_number_for_splynx_id(legacy_id) != number:
        return None
    return legacy_id
