from app.web.public import crm_webhooks


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


def test_meta_webhook_accepts_whatsapp_secret_fallback(monkeypatch):
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

    response = __import__("asyncio").run(crm_webhooks.meta_webhook(_Request(), db=None))

    assert response["status"] == "ok"
    assert captured["channel"] == "meta"
    assert captured["payload"]["object"] == "whatsapp_business_account"
    assert verify_calls == ["meta-secret", "wa-secret"]
