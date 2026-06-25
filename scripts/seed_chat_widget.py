"""Seed the live-chat ChatWidgetConfig + the message.outbound webhook to the sub.

Idempotent. Run INSIDE the CRM app container AFTER deploying the branch that
adds WebhookEventType.message_outbound (the enum must exist in the DB image):

    docker exec -it dotmac_omni_app python scripts/seed_chat_widget.py

Config via env (sensible defaults shown):
    CHAT_WIDGET_NAME          "DotMac Self-care"
    CHAT_ALLOWED_DOMAINS      comma list, e.g. "selfcare.dotmac.io,app.dotmac.io"
    SUB_CHAT_WEBHOOK_URL      "https://selfcare.dotmac.io/webhooks/crm/chat"
    SUB_CHAT_WEBHOOK_SECRET   must equal the sub's CRM_CHAT_WEBHOOK_SECRET

Prints the ChatWidgetConfig id to use as the sub's CRM_CHAT_CONFIG_ID.
"""

import os

from app.db import SessionLocal
from app.models.crm.chat_widget import ChatWidgetConfig
from app.models.webhook import WebhookEndpoint, WebhookEventType, WebhookSubscription


def main() -> None:
    name = os.getenv("CHAT_WIDGET_NAME", "DotMac Self-care")
    domains = [
        d.strip()
        for d in os.getenv(
            "CHAT_ALLOWED_DOMAINS", "selfcare.dotmac.io,app.dotmac.io"
        ).split(",")
        if d.strip()
    ]
    hook_url = os.getenv(
        "SUB_CHAT_WEBHOOK_URL", "https://selfcare.dotmac.io/webhooks/crm/chat"
    )
    hook_secret = os.getenv("SUB_CHAT_WEBHOOK_SECRET", "")

    db = SessionLocal()
    try:
        # 1) ChatWidgetConfig (general pool; customer + reseller share it).
        config = (
            db.query(ChatWidgetConfig).filter(ChatWidgetConfig.name == name).first()
        )
        if config is None:
            config = ChatWidgetConfig(
                name=name,
                allowed_domains=domains,
                widget_title="Chat with us",
                is_active=True,
            )
            db.add(config)
            db.flush()
            print(f"created ChatWidgetConfig {config.id}")
        else:
            config.allowed_domains = domains
            config.is_active = True
            print(f"updated ChatWidgetConfig {config.id}")

        # 2) Webhook endpoint -> the sub chat receiver.
        endpoint = (
            db.query(WebhookEndpoint).filter(WebhookEndpoint.url == hook_url).first()
        )
        if endpoint is None:
            endpoint = WebhookEndpoint(
                name="DotMac Sub — chat push",
                url=hook_url,
                secret=hook_secret or None,
                is_active=True,
            )
            db.add(endpoint)
            db.flush()
            print(f"created WebhookEndpoint {endpoint.id}")
        else:
            if hook_secret:
                endpoint.secret = hook_secret
            endpoint.is_active = True
            print(f"updated WebhookEndpoint {endpoint.id}")

        # 3) Subscription for message.outbound.
        sub = (
            db.query(WebhookSubscription)
            .filter(WebhookSubscription.endpoint_id == endpoint.id)
            .filter(WebhookSubscription.event_type == WebhookEventType.message_outbound)
            .first()
        )
        if sub is None:
            sub = WebhookSubscription(
                endpoint_id=endpoint.id,
                event_type=WebhookEventType.message_outbound,
                is_active=True,
            )
            db.add(sub)
            print("created WebhookSubscription message.outbound")
        else:
            sub.is_active = True
            print("subscription message.outbound already present")

        db.commit()
        if not hook_secret:
            print("WARNING: SUB_CHAT_WEBHOOK_SECRET empty — deliveries unsigned.")
        print("\nSet on the sub:  CRM_CHAT_CONFIG_ID=" + str(config.id))
    finally:
        db.close()


if __name__ == "__main__":
    main()
