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
    assert verify_calls == ["meta-secret", "wa-secret"]


def test_meta_webhook_invalid_signature_blocks_before_payload_parse(monkeypatch):
    body = b'{"object":"instagram","entry":[{"id":"ig_123","time":1712200000,"messaging":[]}]}'

    class _RequestState:
        raw_body = body

    class _Request:
        def __init__(self):
            self.state = _RequestState()
            self.headers = {"X-Hub-Signature-256": "sha256=bad"}

        async def body(self):
            return body

    monkeypatch.setattr(
        crm_webhooks.meta_oauth,
        "get_meta_settings",
        lambda _db: {"meta_app_secret": "meta-secret", "whatsapp_app_secret": "wa-secret"},
    )
    monkeypatch.setattr(crm_webhooks.meta_webhooks, "verify_webhook_signature", lambda *_args, **_kwargs: False)

    validated = {"called": False}

    def _should_not_validate(_payload):
        validated["called"] = True
        raise AssertionError("payload parse should not run on invalid signature")

    monkeypatch.setattr(crm_webhooks.MetaWebhookPayload, "model_validate_json", _should_not_validate)

    response = _run_async(crm_webhooks.meta_webhook(_Request(), db=None))

    assert response.status_code == 401
    assert validated["called"] is False


def test_meta_webhook_debug_logs_signature_context_without_bypassing_validation(monkeypatch):
    body = b'{"object":"instagram","entry":[{"id":"ig_123","time":1712200000,"messaging":[]}]}'

    class _RequestState:
        raw_body = body

    class _Request:
        def __init__(self):
            self.state = _RequestState()
            self.headers = {"X-Hub-Signature-256": "sha256=test"}

        async def body(self):
            return body

    monkeypatch.setattr(
        crm_webhooks.meta_oauth,
        "get_meta_settings",
        lambda _db: {"meta_app_secret": "meta-secret", "whatsapp_app_secret": None},
    )
    monkeypatch.setattr(crm_webhooks, "_meta_signature_debug_enabled", lambda: True)
    monkeypatch.setattr(crm_webhooks.meta_webhooks, "verify_webhook_signature", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        crm_webhooks.meta_webhooks, "compute_webhook_signature", lambda *_args, **_kwargs: "abc123456789ffff"
    )

    captured_messages: list[str] = []

    def _capture_info(message, *args, **kwargs):
        rendered = message % args if args else message
        captured_messages.append(rendered)

    monkeypatch.setattr(crm_webhooks.logger, "info", _capture_info)
    monkeypatch.setattr(crm_webhooks, "_enqueue_webhook_task", lambda *args, **kwargs: True)

    response = _run_async(crm_webhooks.meta_webhook(_Request(), db=None))

    assert response["status"] == "ok"
    assert any("meta_webhook_signature_debug" in message for message in captured_messages)


def test_meta_webhook_signature_compare_debug_logs_header_hash_and_proxy_context(monkeypatch):
    body = b'{"object":"instagram","entry":[{"id":"ig_123","time":1712200000,"messaging":[]}]}'
    computed = crm_webhooks.meta_webhooks.compute_webhook_signature(body, "meta-secret")

    class _RequestState:
        raw_body = body

    class _Request:
        def __init__(self):
            self.state = _RequestState()
            self.headers = {
                "X-Hub-Signature-256": f"sha256={computed}",
                "content-length": str(len(body)),
                "CF-Connecting-IP": "203.0.113.10",
                "X-Forwarded-For": "203.0.113.10, 10.0.0.1",
            }

        async def body(self):
            return body

    monkeypatch.setattr(
        crm_webhooks.meta_oauth,
        "get_meta_settings",
        lambda _db: {"meta_app_secret": "meta-secret", "whatsapp_app_secret": None},
    )
    monkeypatch.setattr(crm_webhooks, "_meta_signature_compare_debug_enabled", lambda: True)

    captured_messages: list[str] = []

    def _capture_info(message, *args, **kwargs):
        rendered = message % args if args else message
        captured_messages.append(rendered)

    monkeypatch.setattr(crm_webhooks.logger, "info", _capture_info)
    monkeypatch.setattr(crm_webhooks, "_enqueue_webhook_task", lambda *args, **kwargs: True)

    response = _run_async(crm_webhooks.meta_webhook(_Request(), db=None))

    assert response["status"] == "ok"
    compare_logs = [message for message in captured_messages if "meta_webhook_signature_compare" in message]
    assert compare_logs
    compare_log = compare_logs[0]
    assert "signature_match=True" in compare_log
    assert f"signature_header_prefix=sha256={computed[:12]}" in compare_log
    assert f"computed_signature_prefix=sha256={computed[:12]}" in compare_log
    assert f"sha256={computed}" not in compare_log
    assert f"body_hash_prefix={crm_webhooks._raw_body_sha256(body)[:20]}" in compare_log
    assert f"content_length={len(body)}" in compare_log
    assert "cf_connecting_ip_present=True" in compare_log
    assert "x_forwarded_for_present=True" in compare_log


def test_meta_webhook_invalid_signature_logs_safe_secret_fingerprints_and_payload_identifiers(monkeypatch):
    body = (
        b'{"object":"instagram","entry":[{"id":"17841400000000000","time":1712200000,'
        b'"messaging":[{"sender":{"id":"user-1"},"recipient":{"id":"17841411111111111"},"message":{"mid":"m_1"}}],'
        b'"app_id":"1234567890"}]}'
    )

    class _RequestState:
        raw_body = body

    class _Request:
        def __init__(self):
            self.state = _RequestState()
            self.headers = {
                "X-Hub-Signature-256": "sha256=bad",
                "content-length": str(len(body)),
                "X-App-Id": "header-app-id",
            }

        async def body(self):
            return body

    monkeypatch.setattr(
        crm_webhooks.meta_oauth,
        "get_meta_settings",
        lambda _db: {"meta_app_secret": "meta-secret", "whatsapp_app_secret": "wa-secret"},
    )
    monkeypatch.setattr(crm_webhooks, "_meta_signature_compare_debug_enabled", lambda: True)
    monkeypatch.setattr(crm_webhooks.meta_webhooks, "verify_webhook_signature", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        crm_webhooks.meta_webhooks, "compute_webhook_signature", lambda *_args, **_kwargs: "abc123456789ffff"
    )

    captured_messages: list[str] = []

    def _capture_info(message, *args, **kwargs):
        rendered = message % args if args else message
        captured_messages.append(rendered)

    monkeypatch.setattr(crm_webhooks.logger, "info", _capture_info)

    response = _run_async(crm_webhooks.meta_webhook(_Request(), db=None))

    assert response.status_code == 401
    invalid_logs = [message for message in captured_messages if "meta_webhook_signature_invalid" in message]
    assert invalid_logs
    invalid_log = invalid_logs[0]
    assert "object=instagram" in invalid_log
    assert "entry_id=17841400000000000" in invalid_log
    assert "app_id=1234567890" in invalid_log
    assert "page_id=17841400000000000" in invalid_log
    assert "instagram_account_id=17841411111111111" in invalid_log
    assert f"primary_secret_fingerprint={crm_webhooks._fingerprint_secret('meta-secret')}" in invalid_log
    assert f"secondary_secret_fingerprint={crm_webhooks._fingerprint_secret('wa-secret')}" in invalid_log
    assert "meta-secret" not in invalid_log
    assert "wa-secret" not in invalid_log


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
