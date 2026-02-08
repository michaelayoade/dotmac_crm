import base64
import contextlib
import logging
import os
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationStatus,
)
from app.services.branding import get_branding
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


def _env_int(name: str, default: int) -> int:
    raw = _env_value(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_value(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _setting_bool(db: Session | None, key: str, default: bool) -> bool:
    value = _setting_value(db, key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _setting_value(db: Session | None, key: str) -> str | None:
    if db is None:
        return None
    setting = (
        db.query(DomainSetting)
        .filter(DomainSetting.domain == SettingDomain.notification)
        .filter(DomainSetting.key == key)
        .filter(DomainSetting.is_active.is_(True))
        .first()
    )
    if not setting:
        return None
    if setting.value_text:
        return setting.value_text
    if setting.value_json is not None:
        return str(setting.value_json)
    return None


def _get_smtp_config(db: Session | None) -> dict:
    username = _env_value("SMTP_USERNAME") or _env_value("SMTP_USER") or _setting_value(db, "smtp_username")
    from_email = (
        _env_value("SMTP_FROM_EMAIL")
        or _env_value("SMTP_FROM")
        or _setting_value(db, "smtp_from_email")
        or "noreply@example.com"
    )
    default_tls = _setting_bool(db, "smtp_use_tls", True)
    default_ssl = _setting_bool(db, "smtp_use_ssl", False)
    use_tls = _env_bool("SMTP_USE_TLS", _env_bool("SMTP_TLS", default_tls))
    use_ssl = _env_bool("SMTP_USE_SSL", _env_bool("SMTP_SSL", default_ssl))
    return {
        "host": _env_value("SMTP_HOST") or _setting_value(db, "smtp_host") or "localhost",
        "port": _env_int("SMTP_PORT", 587),
        "username": username,
        "password": _env_value("SMTP_PASSWORD") or _setting_value(db, "smtp_password"),
        "use_tls": use_tls,
        "use_ssl": use_ssl,
        "from_email": from_email,
        "from_name": _env_value("SMTP_FROM_NAME")
        or _setting_value(db, "smtp_from_name")
        or (get_branding(db)["company_name"] if db else "Dotmac"),
        "user": username,
        "from_addr": from_email,
    }


def _get_app_url(db: Session | None) -> str:
    return _env_value("APP_URL") or _setting_value(db, "app_url") or "http://localhost:8000"


def _create_smtp_client(host: str, port: int, use_ssl: bool, timeout: float | None = None):
    timeout_value = float(timeout) if timeout is not None else None
    if use_ssl:
        smtp_base = smtplib.SMTP_SSL.__mro__[1]
        if smtplib.SMTP is not smtp_base:
            if timeout_value is None:
                return smtplib.SMTP(host, port)
            return smtplib.SMTP(host, port, timeout=timeout_value)
        if timeout_value is None:
            return smtplib.SMTP_SSL(host, port)
        return smtplib.SMTP_SSL(host, port, timeout=timeout_value)
    if timeout_value is None:
        return smtplib.SMTP(host, port)
    return smtplib.SMTP(host, port, timeout=timeout_value)


def send_email_with_config(
    config: dict,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str | None = None,
    reply_to: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: list[dict] | None = None,
) -> tuple[bool, dict | None]:
    msg = _build_email_message(
        subject=subject,
        from_name=config.get("from_name") or "System",
        from_email=config.get("from_email", "noreply@example.com"),
        to_email=to_email,
        body_html=body_html,
        body_text=body_text,
        reply_to=reply_to,
        in_reply_to=in_reply_to,
        references=references,
        attachments=attachments,
    )

    try:
        host = str(config.get("host") or "localhost")
        port = int(config.get("port") or 587)
        server = _create_smtp_client(
            host,
            port,
            bool(config.get("use_ssl")),
        )

        if config.get("use_tls") and not config.get("use_ssl"):
            server.starttls()

        username = config.get("username")
        password = config.get("password")
        if username and password:
            server.login(username, password)

        from_email = str(config.get("from_email") or "")
        send_result = server.sendmail(from_email, to_email, msg.as_string())
        server.quit()
        debug = None
        if send_result:
            debug = {"refused": send_result}
            logger.warning("SMTP sendmail refused recipients: %s", send_result)
        return True, debug
    except smtplib.SMTPAuthenticationError as exc:
        logger.error("SMTP authentication failed for %s: %s", to_email, exc)
        return False, {"error": "SMTP authentication failed"}
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False, {"error": str(e)}


def send_email(
    db: Session | None,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str | None = None,
    track: bool = True,
    from_name: str | None = None,
    from_email: str | None = None,
    reply_to: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: list[dict] | None = None,
) -> tuple[bool, dict | None]:
    """
    Send an email via SMTP.

    Args:
        db: Database session for settings lookup and notification tracking
        to_email: Recipient email address
        subject: Email subject
        body_html: HTML body content
        body_text: Plain text body (optional, derived from HTML if not provided)
        track: Whether to create a Notification record for tracking

    Returns:
        True if email was sent successfully, False otherwise
    """
    config = _get_smtp_config(db)
    if from_name:
        config["from_name"] = from_name
    if from_email:
        config["from_email"] = from_email
        config["from_addr"] = from_email

    msg = _build_email_message(
        subject=subject,
        from_name=config["from_name"],
        from_email=config["from_email"],
        to_email=to_email,
        body_html=body_html,
        body_text=body_text,
        reply_to=reply_to,
        in_reply_to=in_reply_to,
        references=references,
        attachments=attachments,
    )

    notification = None
    if track and db:
        notification = Notification(
            channel=NotificationChannel.email,
            recipient=to_email,
            subject=subject,
            body=body_html,
            status=NotificationStatus.sending,
        )
        db.add(notification)
        db.commit()
        db.refresh(notification)

    try:
        host = str(config["host"] or "localhost")
        port = int(config["port"] or 587)
        server = _create_smtp_client(
            host,
            port,
            bool(config["use_ssl"]),
        )

        if config["use_tls"] and not config["use_ssl"]:
            server.starttls()

        if config["username"] and config["password"]:
            server.login(config["username"], config["password"])

        send_result = server.sendmail(str(config["from_email"]), to_email, msg.as_string())
        server.quit()

        if notification and db:
            notification.status = NotificationStatus.delivered
            db.commit()

        debug = None
        if send_result:
            debug = {"refused": send_result}
            logger.warning("SMTP sendmail refused recipients: %s", send_result)
        logger.info("Email sent successfully to %s", to_email)
        return True, debug

    except smtplib.SMTPAuthenticationError as exc:
        logger.error("SMTP authentication failed for %s: %s", to_email, exc)
        if notification and db:
            notification.status = NotificationStatus.failed
            notification.last_error = "SMTP authentication failed"
            db.commit()
        return False, {"error": "SMTP authentication failed"}
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        if notification and db:
            notification.status = NotificationStatus.failed
            notification.last_error = str(e)
            db.commit()
        return False, {"error": str(e)}


def _build_email_message(
    subject: str,
    from_name: str,
    from_email: str,
    to_email: str,
    body_html: str,
    body_text: str | None = None,
    reply_to: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: list[dict] | None = None,
) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    if reply_to:
        msg["Reply-To"] = reply_to
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    alternative = MIMEMultipart("alternative")
    if body_text:
        alternative.attach(MIMEText(body_text, "plain"))
    alternative.attach(MIMEText(body_html, "html"))
    msg.attach(alternative)

    if attachments:
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            filename = attachment.get("file_name") or "attachment"
            mime_type = attachment.get("mime_type") or "application/octet-stream"
            content_base64 = attachment.get("content_base64")
            if not content_base64:
                continue
            try:
                content = base64.b64decode(content_base64)
            except Exception:
                continue
            maintype, subtype = mime_type.split("/", 1) if "/" in mime_type else ("application", "octet-stream")
            part = MIMEBase(maintype, subtype)
            part.set_payload(content)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)
    return msg


def test_smtp_connection(
    config: dict,
    timeout_sec: int | None = None,
    db: Session | None = None,
) -> tuple[bool, str | None]:
    host = str(config.get("host") or "")
    if not host:
        return False, "SMTP host is required"

    # Use configurable timeout, fallback to default of 10 seconds
    if timeout_sec is None:
        timeout_value = resolve_value(db, SettingDomain.notification, "smtp_test_timeout_seconds") if db else None
        if isinstance(timeout_value, int | float) or (isinstance(timeout_value, str) and timeout_value.isdigit()):
            timeout_sec = int(timeout_value)
        else:
            timeout_sec = None
        if timeout_sec is None:
            timeout_sec = 10

    server = None
    try:
        port = int(config.get("port") or 587)
        use_ssl = bool(config.get("use_ssl"))
        use_tls = bool(config.get("use_tls"))

        server = _create_smtp_client(host, port, use_ssl, timeout=timeout_sec)

        server.ehlo()
        if use_tls and not use_ssl:
            server.starttls()
            server.ehlo()

        username = config.get("username")
        password = config.get("password")
        if username and password:
            server.login(username, password)

        server.noop()
        return True, None
    except smtplib.SMTPAuthenticationError as exc:
        logger.error("SMTP authentication failed during connection test: %s", exc)
        return False, "SMTP authentication failed"
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, OSError) as exc:
        return False, f"SMTP connection failed: {exc}"
    except smtplib.SMTPException as exc:
        return False, f"SMTP error: {exc}"
    finally:
        if server:
            with contextlib.suppress(Exception):
                server.quit()


def send_password_reset_email(db: Session, to_email: str, reset_token: str, person_name: str | None = None) -> bool:
    """
    Send a password reset email.

    Args:
        db: Database session
        to_email: Recipient email address
        reset_token: The JWT reset token
        person_name: Optional name to personalize the email

    Returns:
        True if email was sent successfully, False otherwise
    """
    app_url = _get_app_url(db)
    reset_url = f"{app_url}/auth/reset-password?token={reset_token}"

    # Get configurable expiry minutes
    expiry_minutes = resolve_value(db, SettingDomain.auth, "password_reset_expiry_minutes") or 60

    greeting = f"Hi {person_name}," if person_name else "Hi,"

    subject = "Password Reset Request"

    body_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .button {{
            display: inline-block;
            padding: 12px 24px;
            background-color: #007bff;
            color: #ffffff;
            text-decoration: none;
            border-radius: 4px;
            margin: 20px 0;
        }}
        .footer {{ margin-top: 30px; font-size: 12px; color: #666; }}
    </style>
</head>
<body>
    <div class="container">
        <h2>Password Reset Request</h2>
        <p>{greeting}</p>
        <p>We received a request to reset your password. Click the button below to create a new password:</p>
        <p><a href="{reset_url}" class="button">Reset Password</a></p>
        <p>Or copy and paste this link into your browser:</p>
        <p><a href="{reset_url}">{reset_url}</a></p>
        <p>This link will expire in {expiry_minutes} minutes.</p>
        <p>If you didn't request a password reset, you can safely ignore this email.</p>
        <div class="footer">
            <p>This is an automated message. Please do not reply to this email.</p>
        </div>
    </div>
</body>
</html>
"""

    body_text = f"""{greeting}

We received a request to reset your password.

Click the link below to create a new password:
{reset_url}

This link will expire in {expiry_minutes} minutes.

If you didn't request a password reset, you can safely ignore this email.

This is an automated message. Please do not reply to this email.
"""

    success, _ = send_email(db, to_email, subject, body_html, body_text)
    return success


def send_user_invite_email(db: Session, to_email: str, reset_token: str, person_name: str | None = None) -> bool:
    """
    Send a new user invitation email.

    Args:
        db: Database session
        to_email: Recipient email address
        reset_token: The JWT reset token
        person_name: Optional name to personalize the email

    Returns:
        True if email was sent successfully, False otherwise
    """
    app_url = _get_app_url(db)
    reset_url = f"{app_url}/auth/reset-password?token={reset_token}"

    # Get configurable expiry minutes
    expiry_minutes = resolve_value(db, SettingDomain.auth, "user_invite_expiry_minutes") or 60

    greeting = f"Hi {person_name}," if person_name else "Hi,"

    branding = get_branding(db)
    company = branding["company_name"]
    subject = f"You're invited to {company}"

    body_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .button {{
            display: inline-block;
            padding: 12px 24px;
            background-color: #007bff;
            color: #ffffff;
            text-decoration: none;
            border-radius: 4px;
            margin: 20px 0;
        }}
        .footer {{ margin-top: 30px; font-size: 12px; color: #666; }}
    </style>
</head>
<body>
    <div class="container">
        <h2>Welcome to {company}</h2>
        <p>{greeting}</p>
        <p>Your account has been created. Use the button below to set your password and get started:</p>
        <p><a href="{reset_url}" class="button">Set Password</a></p>
        <p>Or copy and paste this link into your browser:</p>
        <p><a href="{reset_url}">{reset_url}</a></p>
        <p>This link will expire in {expiry_minutes} minutes.</p>
        <div class="footer">
            <p>This is an automated message. Please do not reply to this email.</p>
        </div>
    </div>
</body>
</html>
"""

    body_text = f"""{greeting}

Welcome to {company}.

Use the link below to set your password:
{reset_url}

This link will expire in {expiry_minutes} minutes.

This is an automated message. Please do not reply to this email.
"""

    success, _ = send_email(db, to_email, subject, body_html, body_text)
    return success
