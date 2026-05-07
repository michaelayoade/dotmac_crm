import asyncio
import concurrent.futures

from app.web.public import crm_webhooks


def _run_async(coro):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


def test_parse_meta_whatsapp_status_payload_detects_native_status_callbacks():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba_123",
                "time": 1712200000,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.status.1",
                                    "status": "delivered",
                                    "timestamp": "1712200000",
                                    "recipient_id": "15551234567",
                                }
                            ]
                        },
                    }
                ],
            }
        ],
    }

    meta_payload, status_count = crm_webhooks._parse_meta_whatsapp_status_payload(payload)

    assert meta_payload is not None
    assert meta_payload.object == "whatsapp_business_account"
    assert status_count == 1


def test_meta_webhook_prefers_whatsapp_secret_for_whatsapp_payload(monkeypatch):
    body = b'{"object":"whatsapp_business_account","entry":[{"id":"waba_123","time":1712200000,"changes":[]}]}'

    class _RequestState:
        raw_body = body

    class _Request:
        def __init__(self):
            self.state = _RequestState()
            self.headers = {"X-Hub-Signature-256": "sha256=test"}

        async def body(self):
            return body

    verify_calls: list[str] = []

    def _verify(payload_body, signature_header, secret):
        verify_calls.append(secret)
        return secret == "wa-secret"

    monkeypatch.setattr(
        crm_webhooks.meta_oauth,
        "get_meta_settings",
        lambda _db: {"meta_app_secret": "meta-secret", "whatsapp_app_secret": "wa-secret"},
    )
    monkeypatch.setattr(crm_webhooks.meta_webhooks, "verify_webhook_signature", _verify)

    captured: dict = {}

    def _enqueue(delay_fn, *, channel, payload, trace_id, message_id=None):
        captured["channel"] = channel
        captured["payload"] = payload
        captured["trace_id"] = trace_id
        return True

    monkeypatch.setattr(crm_webhooks, "_enqueue_webhook_task", _enqueue)

    response = _run_async(crm_webhooks.meta_webhook(_Request(), db=None))

    assert response["status"] == "ok"
    assert captured["channel"] == "meta"
    assert captured["payload"]["object"] == "whatsapp_business_account"
    assert verify_calls == ["wa-secret"]


def test_extract_meta_whatsapp_messages_preserves_bounded_ctwa_attribution():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba_123",
                "time": 1712200000,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {
                                "phone_number_id": "pnid-1",
                                "display_phone_number": "15551234567",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Ned"},
                                    "wa_id": "15550001111",
                                }
                            ],
                            "messages": [
                                {
                                    "from": "15550001111",
                                    "id": "wamid.1",
                                    "timestamp": "1712200000",
                                    "type": "text",
                                    "text": {"body": "Hi"},
                                    "referral": {
                                        "source": "ADS",
                                        "ctwa_clid": "clid-123",
                                        "ad_id": "ad-1",
                                        "campaign_id": "camp-1",
                                        "source_url": "https://m.me/example",
                                        "referral_data": {"promo_code": "FIBER"},
                                        "headline": "Fiber promo",
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }

    messages = crm_webhooks._extract_meta_whatsapp_messages(payload)

    assert len(messages) == 1
    attribution = messages[0].metadata["attribution"]
    assert attribution["source"] == "ADS"
    assert attribution["ctwa_clid"] == "clid-123"
    assert attribution["ad_id"] == "ad-1"
    assert attribution["campaign_id"] == "camp-1"
    assert attribution["source_url"] == "https://m.me/example"
    assert attribution["referral_data"] == {"promo_code": "FIBER"}
    assert attribution["referral"] == {
        "source": "ADS",
        "ctwa_clid": "clid-123",
        "ad_id": "ad-1",
        "campaign_id": "camp-1",
        "source_url": "https://m.me/example",
        "referral_data": {"promo_code": "FIBER"},
        "headline": "Fiber promo",
    }


def test_extract_meta_whatsapp_messages_leaves_plain_whatsapp_metadata_unchanged():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba_123",
                "time": 1712200000,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {
                                "phone_number_id": "pnid-1",
                                "display_phone_number": "15551234567",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Ned"},
                                    "wa_id": "15550001111",
                                }
                            ],
                            "messages": [
                                {
                                    "from": "15550001111",
                                    "id": "wamid.1",
                                    "timestamp": "1712200000",
                                    "type": "text",
                                    "text": {"body": "Hi"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }

    messages = crm_webhooks._extract_meta_whatsapp_messages(payload)

    assert len(messages) == 1
    metadata = messages[0].metadata
    assert metadata["phone_number_id"] == "pnid-1"
    assert metadata["display_phone_number"] == "15551234567"
    assert "attribution" not in metadata
