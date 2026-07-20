from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.weekly_reporting import delivery, engine
from app.services.weekly_reporting.configuration import WeeklyReportingConfig
from app.services.weekly_reporting.period import previous_complete_week
from app.tasks import reports as report_tasks


def _config(*, enabled: bool = True, recipients: tuple[str, ...] = ()) -> WeeklyReportingConfig:
    return WeeklyReportingConfig(
        enabled=enabled,
        recipients=recipients,
        schedule_day="monday",
        schedule_time="08:00",
        timezone="Africa/Lagos",
    )


def _generators(*, support_inbound: int = 759, fail_support: bool = False):
    calls = {"sales": 0, "support": 0}

    def previous_complete_week(_now):
        return (
            datetime(2026, 7, 13, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 13, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 0, 0, tzinfo=UTC),
        )

    def sales_generate(output_dir: Path, *, now):
        del now
        calls["sales"] += 1
        (output_dir / engine.SALES_MARKDOWN_NAME).write_text("sales markdown", encoding="utf-8")
        (output_dir / delivery.SALES_PDF_NAME).write_bytes(b"%PDF-sales")
        return {
            "reporting_period": "13 July 2026 - 19 July 2026",
            "total_conversations_reviewed": 759,
            "total_sales_conversations": 45,
            "active_inboxes_reviewed": 6,
            "warnings": ["Sales warning"],
            "validation": {
                "reviewed": 759,
                "sales": 45,
                "intent_total": 45,
                "outcome_total": 45,
                "sentiment_total": 45,
                "agent_total": 45,
                "active_inboxes": 6,
            },
        }

    def support_generate(output_dir: Path, *, now):
        del now
        calls["support"] += 1
        if fail_support:
            raise ValueError("Support validation failed")
        (output_dir / engine.SUPPORT_MARKDOWN_NAME).write_text("support markdown", encoding="utf-8")
        (output_dir / delivery.SUPPORT_PDF_NAME).write_bytes(b"%PDF-support")
        return {
            "reporting_period": "13 July 2026 - 19 July 2026",
            "total_inbound_conversations_reviewed": support_inbound,
            "total_support_conversations_reviewed": 580,
            "active_inboxes_reviewed": 6,
            "warnings": ["Support warning"],
            "validation": {
                "inbound_reviewed": support_inbound,
                "support_reviewed": 580,
                "complaint_total": 580,
                "sentiment_total": 580,
                "resolution_total": 580,
                "agent_total": 580,
                "happiness_total": 580,
                "active_inboxes": 6,
            },
        }

    sales = SimpleNamespace(previous_complete_week=previous_complete_week, generate=sales_generate)
    support = SimpleNamespace(generate=support_generate)
    return sales, support, calls


def test_disabled_weekly_reporting_logs_without_generating(tmp_path, monkeypatch):
    generators = _generators()
    monkeypatch.setattr(engine, "_load_validated_generators", lambda: generators[:2])
    monkeypatch.setattr(engine, "_load_config_read_only", lambda: _config(enabled=False))

    result = engine.run_weekly_reporting(
        now_utc=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
        reports_root=tmp_path / "reports",
    )

    assert result["status"] == "skipped"
    assert result["email_delivery_status"] == "skipped_disabled"
    assert generators[2] == {"sales": 0, "support": 0}
    assert Path(result["execution_log"]).is_file()


def test_no_recipients_generates_validated_archive_and_log(tmp_path, monkeypatch):
    generators = _generators()
    monkeypatch.setattr(engine, "_load_validated_generators", lambda: generators[:2])
    monkeypatch.setattr(engine, "_load_config_read_only", lambda: _config())

    result = engine.run_weekly_reporting(
        now_utc=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
        reports_root=tmp_path / "reports",
    )

    archive = tmp_path / "reports/weekly/2026-07-13_to_2026-07-19"
    assert result["status"] == "completed_with_warnings"
    assert result["email_delivery_status"] == "skipped_no_recipients"
    assert result["conversations_analysed"] == 759
    assert result["sales_conversations_identified"] == 45
    assert result["support_conversations_identified"] == 580
    assert (archive / delivery.SALES_PDF_NAME).read_bytes() == b"%PDF-sales"
    assert (archive / delivery.SUPPORT_PDF_NAME).read_bytes() == b"%PDF-support"
    log = json.loads(Path(result["execution_log"]).read_text(encoding="utf-8"))
    assert log["recipient_count"] == 0
    assert log["email_delivery_status"] == "skipped_no_recipients"


def test_generation_or_cross_validation_failure_never_archives_or_emails(tmp_path, monkeypatch):
    for generators in (_generators(fail_support=True), _generators(support_inbound=758)):
        root = tmp_path / f"reports-{generators[2]['sales']}-{id(generators)}"
        deliveries: list[object] = []
        monkeypatch.setattr(engine, "_load_validated_generators", lambda generators=generators: generators[:2])
        monkeypatch.setattr(engine, "_load_config_read_only", lambda: _config(recipients=("reports@example.com",)))

        def capture_delivery(*, captured=deliveries, **kwargs):
            captured.append(kwargs)

        monkeypatch.setattr(engine, "_deliver_read_only", capture_delivery)

        result = engine.run_weekly_reporting(
            now_utc=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
            reports_root=root,
        )

        assert result["status"] == "failed"
        assert result["email_delivery_status"] == "not_sent_due_to_failure"
        assert deliveries == []
        assert not (root / "weekly/2026-07-13_to_2026-07-19").exists()


def test_archive_is_never_overwritten_and_delivery_is_idempotent(tmp_path, monkeypatch):
    generators = _generators()
    root = tmp_path / "reports"
    monkeypatch.setattr(engine, "_load_validated_generators", lambda: generators[:2])
    monkeypatch.setattr(engine, "_load_config_read_only", lambda: _config())
    first = engine.run_weekly_reporting(
        now_utc=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
        reports_root=root,
    )
    archive = root / "weekly/2026-07-13_to_2026-07-19"
    sales_pdf = archive / delivery.SALES_PDF_NAME
    original_mtime = sales_pdf.stat().st_mtime_ns

    monkeypatch.setattr(
        engine,
        "_load_config_read_only",
        lambda: _config(recipients=("reports@example.com", "ops@example.com")),
    )
    deliveries: list[dict] = []

    def fake_delivery(**kwargs):
        deliveries.append(kwargs)
        return {
            "status": "sent",
            "recipient_count": 2,
            "subject": "DotMac Weekly Inbound Experience Reports | 13 July 2026 - 19 July 2026",
        }

    monkeypatch.setattr(engine, "_deliver_read_only", fake_delivery)
    second = engine.run_weekly_reporting(
        now_utc=datetime(2026, 7, 20, 7, 5, tzinfo=UTC),
        reports_root=root,
    )
    third = engine.run_weekly_reporting(
        now_utc=datetime(2026, 7, 20, 7, 10, tzinfo=UTC),
        reports_root=root,
    )

    assert first["status"] == "completed_with_warnings"
    assert second["email_delivery_status"] == "sent"
    assert third["email_delivery_status"] == "already_delivered"
    assert len(deliveries) == 1
    assert generators[2] == {"sales": 1, "support": 1}
    assert sales_pdf.stat().st_mtime_ns == original_mtime
    assert (archive / engine.DELIVERY_MARKER_NAME).is_file()


def test_professional_email_contains_period_timestamp_and_both_attachments(tmp_path, monkeypatch):
    (tmp_path / delivery.SALES_PDF_NAME).write_bytes(b"sales")
    (tmp_path / delivery.SUPPORT_PDF_NAME).write_bytes(b"support")
    sent: dict = {}

    def fake_send_email(**kwargs):
        sent.update(kwargs)
        return True, None

    monkeypatch.setattr(delivery.email_service, "send_email", fake_send_email)
    result = delivery.deliver_reports(
        object(),
        config=_config(recipients=("reports@example.com", "ops@example.com")),
        archive_dir=tmp_path,
        reporting_period="13 July 2026 - 19 July 2026",
        generated_at=datetime(2026, 7, 20, 7, 5, tzinfo=UTC),
        summary={
            "conversations_analysed": 759,
            "sales_conversations": 45,
            "support_conversations": 580,
            "active_inboxes": 6,
        },
    )

    assert result["status"] == "sent"
    assert "13 July 2026 - 19 July 2026" in sent["subject"]
    assert "20 July 2026 at 08:05 WAT" in sent["body_text"]
    assert "Weekly Sales Inbound Experience Report" in sent["body_text"]
    assert "Weekly Support Inbound Experience Report" in sent["body_text"]
    assert sent["to_email"] == "reports@example.com"
    assert sent["bcc_emails"] == ["ops@example.com"]
    assert {item["file_name"] for item in sent["attachments"]} == {
        delivery.SALES_PDF_NAME,
        delivery.SUPPORT_PDF_NAME,
    }
    assert sent["track"] is False


def test_generator_import_failure_is_logged(tmp_path, monkeypatch):
    def fail_import():
        raise ImportError("validated generators unavailable")

    monkeypatch.setattr(engine, "_load_validated_generators", fail_import)

    result = engine.run_weekly_reporting(
        now_utc=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
        reports_root=tmp_path / "reports",
    )

    assert result["status"] == "failed"
    assert result["reporting_period"] == "13 July 2026 - 19 July 2026"
    assert "validated generators unavailable" in result["errors"]
    assert Path(result["execution_log"]).is_file()


def test_incomplete_delivery_state_suppresses_automatic_resend(tmp_path, monkeypatch):
    generators = _generators()
    root = tmp_path / "reports"
    monkeypatch.setattr(engine, "_load_validated_generators", lambda: generators[:2])
    monkeypatch.setattr(engine, "_load_config_read_only", lambda: _config())
    first = engine.run_weekly_reporting(
        now_utc=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
        reports_root=root,
    )
    archive = root / "weekly/2026-07-13_to_2026-07-19"
    recipients = ("reports@example.com",)
    engine._reserve_delivery(
        archive,
        attempted_at=datetime(2026, 7, 20, 7, 5, tzinfo=UTC),
        recipient_count=1,
        recipients=recipients,
    )
    deliveries: list[dict] = []
    monkeypatch.setattr(engine, "_load_config_read_only", lambda: _config(recipients=recipients))
    monkeypatch.setattr(engine, "_deliver_read_only", lambda **kwargs: deliveries.append(kwargs))

    second = engine.run_weekly_reporting(
        now_utc=datetime(2026, 7, 20, 7, 10, tzinfo=UTC),
        reports_root=root,
    )

    assert first["email_delivery_status"] == "skipped_no_recipients"
    assert second["status"] == "completed_with_warnings"
    assert second["email_delivery_status"] == "previous_delivery_state_unknown"
    assert deliveries == []


def test_failed_delivery_is_recorded_and_not_retried_automatically(tmp_path, monkeypatch):
    generators = _generators()
    root = tmp_path / "reports"
    recipients = ("reports@example.com",)
    monkeypatch.setattr(engine, "_load_validated_generators", lambda: generators[:2])
    monkeypatch.setattr(engine, "_load_config_read_only", lambda: _config(recipients=recipients))
    deliveries = 0

    def fail_delivery(**kwargs):
        nonlocal deliveries
        del kwargs
        deliveries += 1
        return {"status": "failed", "error": "SMTP authentication failed", "recipient_count": 1}

    monkeypatch.setattr(engine, "_deliver_read_only", fail_delivery)
    first = engine.run_weekly_reporting(
        now_utc=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
        reports_root=root,
    )
    second = engine.run_weekly_reporting(
        now_utc=datetime(2026, 7, 20, 7, 5, tzinfo=UTC),
        reports_root=root,
    )
    marker = json.loads(
        (root / "weekly/2026-07-13_to_2026-07-19" / engine.DELIVERY_MARKER_NAME).read_text(encoding="utf-8")
    )

    assert first["status"] == "failed"
    assert second["email_delivery_status"] == "previous_delivery_state_unknown"
    assert marker["status"] == "failed"
    assert deliveries == 1


def test_celery_task_marks_engine_failure_as_failure(monkeypatch):
    monkeypatch.setattr(
        engine,
        "run_weekly_reporting",
        lambda: {"status": "failed", "errors": ["validation failed"], "execution_log": "reports/logs/run.json"},
    )

    with pytest.raises(RuntimeError, match="Weekly Reporting execution failed"):
        report_tasks.run_weekly_inbound_reporting.run()


def test_orchestration_period_matches_validated_generator():
    sales, _ = engine._load_validated_generators()
    for instant in (
        datetime(2026, 7, 19, 22, 59, tzinfo=UTC),
        datetime(2026, 7, 19, 23, 0, tzinfo=UTC),
        datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
    ):
        assert previous_complete_week(instant) == sales.previous_complete_week(instant)
