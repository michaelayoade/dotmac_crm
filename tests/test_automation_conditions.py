from app.services.automation_conditions import evaluate_conditions


def test_evaluate_conditions_trims_field_path() -> None:
    context = {"payload": {"channel_target_id": "abc-123"}}
    conditions = [
        {
            "field": " payload.channel_target_id ",
            "op": "eq",
            "value": " abc-123 ",
        }
    ]

    assert evaluate_conditions(conditions, context) is True


def test_evaluate_conditions_in_with_whitespace_field_path() -> None:
    context = {"payload": {"channel_type": "whatsapp"}}
    conditions = [
        {
            "field": " payload.channel_type",
            "op": "in",
            "value": [" email ", " whatsapp "],
        }
    ]

    assert evaluate_conditions(conditions, context) is True
