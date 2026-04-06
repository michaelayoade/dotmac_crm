from app import monitoring


def test_parse_traces_sample_rate_defaults_on_invalid(caplog):
    with caplog.at_level("WARNING", logger="app.monitoring"):
        value = monitoring._parse_traces_sample_rate("not-a-number")

    assert value == 0.1
    assert "Invalid SENTRY_TRACES_SAMPLE_RATE" in caplog.text


def test_parse_traces_sample_rate_defaults_on_out_of_range(caplog):
    with caplog.at_level("WARNING", logger="app.monitoring"):
        value = monitoring._parse_traces_sample_rate("1.5")

    assert value == 0.1
    assert "Out-of-range SENTRY_TRACES_SAMPLE_RATE" in caplog.text
