from dataclasses import dataclass

from sqlalchemy.orm import Session


@dataclass
class ValidationIssue:
    index: int
    detail: str


def _get(payload, key):
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key)


# Note: validate_subscribers, validate_subscriptions, validate_cpe_devices,
# validate_ip_assignments, validate_service_orders removed as they depended
# on deleted validator modules (catalog, network, provisioning, subscriber)
