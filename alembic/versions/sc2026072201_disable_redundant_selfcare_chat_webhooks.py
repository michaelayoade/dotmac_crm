"""disable redundant generic selfcare chat webhooks

Agent replies already push to dotmac_sub via ``selfcare.notify_chat_message`` /
``notify_field_chat_message``. The same wakeup URLs were also registered as
generic ``message_outbound`` webhook subscriptions, which sign a different event
envelope and therefore fail sub's dedicated receiver authentication. Deactivate
those subscriptions/endpoints and close out their stuck pending deliveries.

Revision ID: sc2026072201
Revises: rp2026072101
Create Date: 2026-07-22 00:00:00.000000
"""

from alembic import op

revision = "sc2026072201"
down_revision = "rp2026072101"
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
