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
