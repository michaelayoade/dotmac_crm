"""Field-app services: technician/vendor-facing orchestration layer.

All business logic for the field mobile app lives here; routes in
``app/api/field/`` are thin wrappers. These services orchestrate and scope
existing domain services — they do not duplicate them.
"""

from app.services.field.attachments import field_attachments  # noqa: F401
