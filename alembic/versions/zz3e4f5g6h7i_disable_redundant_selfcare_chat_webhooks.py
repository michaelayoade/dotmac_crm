"""disable redundant generic selfcare chat webhooks

Revision ID: zz3e4f5g6h7i
Revises: zy2d3e4f5g6h
Create Date: 2026-07-11 12:15:00.000000
"""

from alembic import op


revision = "zz3e4f5g6h7i"
down_revision = "zy2d3e4f5g6h"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE webhook_subscriptions AS s
        SET is_active = false,
            updated_at = now()
        FROM webhook_endpoints AS e
        WHERE s.endpoint_id = e.id
          AND s.event_type = 'message_outbound'
          AND e.url = ANY(ARRAY[
              'https://selfcare.dotmac.io/api/v1/webhooks/crm/chat',
              'https://selfcare.dotmac.io/api/v1/webhooks/crm/field-chat'
          ])
        """
    )
    op.execute(
        """
        UPDATE webhook_endpoints
        SET is_active = false,
            updated_at = now()
        WHERE url = ANY(ARRAY[
              'https://selfcare.dotmac.io/api/v1/webhooks/crm/chat',
              'https://selfcare.dotmac.io/api/v1/webhooks/crm/field-chat'
          ])
        """
    )
    op.execute(
        """
        UPDATE webhook_deliveries
        SET status = 'failed',
            error = 'disabled redundant generic selfcare chat webhook endpoint; dedicated selfcare chat push remains active',
            last_attempt_at = COALESCE(last_attempt_at, now())
        WHERE endpoint_id IN (
            SELECT id
            FROM webhook_endpoints
            WHERE url = ANY(ARRAY[
                'https://selfcare.dotmac.io/api/v1/webhooks/crm/chat',
                'https://selfcare.dotmac.io/api/v1/webhooks/crm/field-chat'
            ])
        )
          AND event_type = 'message_outbound'
          AND status = 'pending'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE webhook_endpoints
        SET is_active = true,
            updated_at = now()
        WHERE url = ANY(ARRAY[
              'https://selfcare.dotmac.io/api/v1/webhooks/crm/chat',
              'https://selfcare.dotmac.io/api/v1/webhooks/crm/field-chat'
          ])
        """
    )
    op.execute(
        """
        UPDATE webhook_subscriptions AS s
        SET is_active = true,
            updated_at = now()
        FROM webhook_endpoints AS e
        WHERE s.endpoint_id = e.id
          AND s.event_type = 'message_outbound'
          AND e.url = ANY(ARRAY[
              'https://selfcare.dotmac.io/api/v1/webhooks/crm/chat',
              'https://selfcare.dotmac.io/api/v1/webhooks/crm/field-chat'
          ])
        """
    )
