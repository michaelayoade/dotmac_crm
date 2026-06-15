from __future__ import annotations

from datetime import UTC, datetime

from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.scheduler import ScheduledTask
from app.services import ncc_report_email


def test_ncc_report_email_settings_default_disabled(db_session):
    snapshot = ncc_report_email.get_settings_snapshot(db_session)

    assert snapshot["enabled"] is False
    assert snapshot["recipient_email"] == ""
    assert snapshot["from_name"] == ""
    assert snapshot["local_time"] == "08:00"
    assert snapshot["timezone"] == "Africa/Lagos"
    assert snapshot["send_day"] == "monday"
    assert snapshot["lookback_days"] == 7
    assert "{lookback_days}" in snapshot["body_template"]


def test_save_ncc_report_email_settings_syncs_scheduled_task(db_session):
    snapshot = ncc_report_email.save_email_settings(
        db_session,
        enabled=True,
        recipient_email="regulatory@example.com",
        cc="ops@example.com",
        bcc="audit@example.com",
        from_name="Aisha Ibrahim",
        subject="NCC Weekly",
        body_template="Attached NCC report for {report_date}. Rows: {row_count}.",
        local_time="09:30",
        timezone="Africa/Lagos",
        send_day="friday",
        lookback_days=14,
    )

    assert snapshot["enabled"] is True
    assert snapshot["recipient_email"] == "regulatory@example.com"
    assert snapshot["from_name"] == "Aisha Ibrahim"
    assert snapshot["body_template"] == "Attached NCC report for {report_date}. Rows: {row_count}."
    assert snapshot["send_day"] == "friday"
    task = (
        db_session.query(ScheduledTask)
        .filter(ScheduledTask.task_name == ncc_report_email.NCC_REPORT_EMAIL_TASK_NAME)
        .one()
    )
    assert task.enabled is True
    assert task.interval_seconds == 300

    ncc_report_email.save_email_settings(
        db_session,
        enabled=False,
        recipient_email="regulatory@example.com",
        cc="",
        bcc="",
        from_name="Aisha Ibrahim",
        subject="NCC Weekly",
        body_template="Attached NCC report for {report_date}. Rows: {row_count}.",
        local_time="09:30",
        timezone="Africa/Lagos",
        send_day="friday",
        lookback_days=14,
    )
    db_session.refresh(task)
    assert task.enabled is False


def test_scheduled_ncc_report_skips_before_send_time(db_session):
    ncc_report_email.save_email_settings(
        db_session,
        enabled=True,
        recipient_email="regulatory@example.com",
        cc="",
        bcc="",
        from_name="Aisha Ibrahim",
        subject="NCC Weekly",
        body_template="Attached NCC report for {report_date}. Rows: {row_count}.",
        local_time="08:00",
        timezone="Africa/Lagos",
        send_day="monday",
        lookback_days=7,
    )

    result = ncc_report_email.run_scheduled_ncc_report_email(
        db_session,
        now_utc=datetime(2026, 6, 15, 6, 59, tzinfo=UTC),
    )

    assert result == {"status": "skipped", "reason": "before_scheduled_time"}


def test_scheduled_ncc_report_skips_on_non_selected_weekday(db_session):
    ncc_report_email.save_email_settings(
        db_session,
        enabled=True,
        recipient_email="regulatory@example.com",
        cc="",
        bcc="",
        from_name="Aisha Ibrahim",
        subject="NCC Weekly",
        body_template="Attached NCC report for {report_date}. Rows: {row_count}.",
        local_time="08:00",
        timezone="Africa/Lagos",
        send_day="friday",
        lookback_days=7,
    )

    result = ncc_report_email.run_scheduled_ncc_report_email(
        db_session,
        now_utc=datetime(2026, 6, 15, 8, 0, tzinfo=UTC),
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "not_scheduled_day"
    assert result["send_day"] == "friday"


def test_scheduled_ncc_report_sends_xlsx_and_marks_sent(db_session, monkeypatch):
    ncc_report_email.save_email_settings(
        db_session,
        enabled=True,
        recipient_email="regulatory@example.com",
        cc="ops@example.com",
        bcc="",
        from_name="Aisha Ibrahim",
        subject="NCC Weekly",
        body_template="Hello,\nAttached NCC report for {report_date}. Rows: {row_count}.",
        local_time="08:00",
        timezone="Africa/Lagos",
        send_day="monday",
        lookback_days=7,
    )

    from app.web.admin import reports as ncc_reports

    monkeypatch.setattr(
        ncc_reports,
        "_build_ncc_records",
        lambda _db, _start_dt, _end_dt: [{"First Name": "Ada", "_ticket_url": "/ticket"}],
    )
    monkeypatch.setattr(ncc_reports, "_build_ncc_workbook", lambda _records, _columns: b"xlsx-content")
    sent: dict[str, object] = {}

    def fake_send_email(**kwargs):
        sent.update(kwargs)
        return True, None

    monkeypatch.setattr(ncc_report_email.email_service, "send_email", fake_send_email)

    result = ncc_report_email.run_scheduled_ncc_report_email(
        db_session,
        now_utc=datetime(2026, 6, 15, 7, 0, tzinfo=UTC),
    )

    assert result["status"] == "sent"
    assert result["rows"] == 1
    assert sent["to_email"] == "regulatory@example.com"
    assert sent["from_name"] == "Aisha Ibrahim"
    assert sent["cc_emails"] == ["ops@example.com"]
    assert sent["body_text"] == "Hello,\nAttached NCC report for 2026-06-15. Rows: 1."
    assert sent["body_html"] == "<p>Hello,<br>Attached NCC report for 2026-06-15. Rows: 1.</p>"
    attachment = sent["attachments"][0]
    assert attachment["file_name"] == "NCC REPORTS (DOTMAC).xlsx"
    assert attachment["mime_type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    last_sent = (
        db_session.query(DomainSetting)
        .filter(DomainSetting.domain == SettingDomain.notification)
        .filter(DomainSetting.key == ncc_report_email.NCC_REPORT_EMAIL_LAST_SENT_KEY)
        .one()
    )
    assert last_sent.value_text == "2026-06-15"
