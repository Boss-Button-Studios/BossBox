"""
Notification Service Tests — BossBox Atomic Step 17
====================================================
All channel calls are mocked — no live SMTP, ntfy, or display required.
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from bossbox.audit.logger import AuditLogger
from bossbox.config.loader import (
    NotifyConfig,
    NtfyNotifyConfig,
    OsNativeNotifyConfig,
    SmtpNotifyConfig,
)
from bossbox.notify.notifier import EventType, NotifyEvent, Notifier, _ntfy_priority


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def audit(tmp_path):
    return AuditLogger(log_path=tmp_path / "audit.log")


def _make_notifier(
    *,
    audit: AuditLogger,
    os_native: bool = False,
    ntfy_cfg: NtfyNotifyConfig | None = None,
    smtp_cfg: SmtpNotifyConfig | None = None,
) -> Notifier:
    config = NotifyConfig(
        os_native=OsNativeNotifyConfig(enabled=os_native),
        ntfy=ntfy_cfg,
        smtp=smtp_cfg,
    )
    return Notifier(config, audit)


def _ntfy_cfg(
    *,
    enabled: bool = True,
    base_url: str = "https://ntfy.sh",
    topic: str = "test-topic",
) -> NtfyNotifyConfig:
    return NtfyNotifyConfig(enabled=enabled, base_url=base_url, topic=topic)


def _smtp_cfg(
    *,
    enabled: bool = True,
    use_tls: bool = True,
    email_on_checkpoint: bool = False,
    password: str = "s3cr3t",
) -> SmtpNotifyConfig:
    return SmtpNotifyConfig(
        enabled=enabled,
        host="127.0.0.1",
        port=587,
        username="user@example.com",
        password=password,
        from_address="user@example.com",
        to_address="dest@example.com",
        use_tls=use_tls,
        email_on_checkpoint=email_on_checkpoint,
    )


def _event(
    event_type: EventType = EventType.TASK_COMPLETE,
    title: str = "Test title",
    body: str = "Test body",
) -> NotifyEvent:
    return NotifyEvent(event_type=event_type, title=title, body=body)


async def _run(notifier: Notifier, event: NotifyEvent) -> None:
    """Send and await all dispatched tasks."""
    tasks = await notifier.send(event)
    if tasks:
        await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Queue behaviour
# ---------------------------------------------------------------------------


async def test_queue_appended_on_send(audit):
    notifier = _make_notifier(audit=audit)
    event = _event()
    await notifier.send(event)
    assert len(notifier.queue()) == 1
    assert notifier.queue()[0] is event


async def test_queue_returns_copy(audit):
    notifier = _make_notifier(audit=audit)
    await notifier.send(_event())
    assert notifier.queue() is not notifier.queue()


async def test_queue_accumulates_multiple_events(audit):
    notifier = _make_notifier(audit=audit)
    await notifier.send(_event(EventType.TASK_COMPLETE))
    await notifier.send(_event(EventType.TASK_FAILED))
    assert len(notifier.queue()) == 2


async def test_queue_updated_even_when_all_channels_disabled(audit):
    notifier = _make_notifier(audit=audit, os_native=False)
    await notifier.send(_event())
    assert len(notifier.queue()) == 1


# ---------------------------------------------------------------------------
# OS native channel
# ---------------------------------------------------------------------------


async def test_os_native_calls_plyer(audit):
    notifier = _make_notifier(audit=audit, os_native=True)
    mock_plyer = MagicMock()
    with patch.dict(sys.modules, {"plyer": mock_plyer}):
        await _run(notifier, _event(title="My title"))
    mock_plyer.notification.notify.assert_called_once()
    _, kwargs = mock_plyer.notification.notify.call_args
    assert kwargs.get("title") == "My title" or mock_plyer.notification.notify.call_args.args[0] == "My title"


async def test_os_native_disabled_does_not_call_plyer(audit):
    notifier = _make_notifier(audit=audit, os_native=False)
    mock_plyer = MagicMock()
    with patch.dict(sys.modules, {"plyer": mock_plyer}):
        await _run(notifier, _event())
    mock_plyer.notification.notify.assert_not_called()


async def test_os_native_import_error_does_not_raise(audit):
    notifier = _make_notifier(audit=audit, os_native=True)
    with patch.dict(sys.modules, {"plyer": None}):
        await _run(notifier, _event())  # must not raise


async def test_os_native_notify_error_does_not_raise(audit):
    notifier = _make_notifier(audit=audit, os_native=True)
    mock_plyer = MagicMock()
    mock_plyer.notification.notify.side_effect = RuntimeError("no display")
    with patch.dict(sys.modules, {"plyer": mock_plyer}):
        await _run(notifier, _event())  # must not raise


# ---------------------------------------------------------------------------
# ntfy.sh channel
# ---------------------------------------------------------------------------


async def test_ntfy_posts_to_correct_url(audit):
    notifier = _make_notifier(audit=audit, ntfy_cfg=_ntfy_cfg(topic="my-topic"))
    with respx.mock:
        route = respx.post("https://ntfy.sh/my-topic").mock(
            return_value=httpx.Response(200)
        )
        await _run(notifier, _event())
    assert route.called


async def test_ntfy_uses_custom_base_url(audit):
    notifier = _make_notifier(
        audit=audit,
        ntfy_cfg=_ntfy_cfg(base_url="https://ntfy.myserver.com", topic="bossbox"),
    )
    with respx.mock:
        route = respx.post("https://ntfy.myserver.com/bossbox").mock(
            return_value=httpx.Response(200)
        )
        await _run(notifier, _event())
    assert route.called


async def test_ntfy_sends_title_header(audit):
    notifier = _make_notifier(audit=audit, ntfy_cfg=_ntfy_cfg())
    with respx.mock:
        route = respx.post("https://ntfy.sh/test-topic").mock(
            return_value=httpx.Response(200)
        )
        await _run(notifier, _event(title="Task complete"))
    assert route.called
    request = route.calls.last.request
    assert request.headers.get("title") == "Task complete"


async def test_ntfy_disabled_no_request(audit):
    notifier = _make_notifier(audit=audit, ntfy_cfg=_ntfy_cfg(enabled=False))
    with respx.mock:
        route = respx.post("https://ntfy.sh/test-topic").mock(
            return_value=httpx.Response(200)
        )
        await _run(notifier, _event())
    assert not route.called


async def test_ntfy_http_error_does_not_raise(audit):
    notifier = _make_notifier(audit=audit, ntfy_cfg=_ntfy_cfg())
    with respx.mock:
        respx.post("https://ntfy.sh/test-topic").mock(
            side_effect=httpx.ConnectError("refused")
        )
        await _run(notifier, _event())  # must not raise


async def test_ntfy_no_topic_no_request(audit):
    cfg = NtfyNotifyConfig(enabled=True, base_url="https://ntfy.sh", topic=None)
    notifier = _make_notifier(audit=audit, ntfy_cfg=cfg)
    with respx.mock:
        route = respx.post("https://ntfy.sh/").mock(return_value=httpx.Response(200))
        await _run(notifier, _event())
    assert not route.called


# ---------------------------------------------------------------------------
# ntfy priority mapping
# ---------------------------------------------------------------------------


def test_ntfy_priority_urgent_for_injection_block():
    assert _ntfy_priority(EventType.INJECTION_BLOCK) == "urgent"


def test_ntfy_priority_urgent_for_privilege_escalation():
    assert _ntfy_priority(EventType.PRIVILEGE_ESCALATION) == "urgent"


def test_ntfy_priority_high_for_checkpoint():
    assert _ntfy_priority(EventType.HUMAN_CHECKPOINT) == "high"


def test_ntfy_priority_default_for_task_complete():
    assert _ntfy_priority(EventType.TASK_COMPLETE) == "default"


# ---------------------------------------------------------------------------
# SMTP channel
# ---------------------------------------------------------------------------


def _mock_smtp():
    """Return a context-manager-compatible SMTP mock."""
    mock_instance = MagicMock()
    mock_cls = MagicMock()
    mock_cls.return_value.__enter__.return_value = mock_instance
    mock_cls.return_value.__exit__.return_value = False
    return mock_cls, mock_instance


async def test_smtp_called_for_task_complete(audit):
    notifier = _make_notifier(audit=audit, smtp_cfg=_smtp_cfg())
    mock_cls, mock_instance = _mock_smtp()
    with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
        await _run(notifier, _event(EventType.TASK_COMPLETE))
    assert mock_instance.send_message.called


async def test_smtp_called_for_task_failed(audit):
    notifier = _make_notifier(audit=audit, smtp_cfg=_smtp_cfg())
    mock_cls, mock_instance = _mock_smtp()
    with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
        await _run(notifier, _event(EventType.TASK_FAILED))
    assert mock_instance.send_message.called


async def test_smtp_not_called_for_injection_warn(audit):
    notifier = _make_notifier(audit=audit, smtp_cfg=_smtp_cfg())
    mock_cls, _ = _mock_smtp()
    with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
        await _run(notifier, _event(EventType.INJECTION_WARN))
    mock_cls.assert_not_called()


async def test_smtp_not_called_for_anomaly(audit):
    notifier = _make_notifier(audit=audit, smtp_cfg=_smtp_cfg())
    mock_cls, _ = _mock_smtp()
    with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
        await _run(notifier, _event(EventType.ANOMALY_DETECTED))
    mock_cls.assert_not_called()


async def test_smtp_checkpoint_off_by_default(audit):
    notifier = _make_notifier(audit=audit, smtp_cfg=_smtp_cfg(email_on_checkpoint=False))
    mock_cls, _ = _mock_smtp()
    with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
        await _run(notifier, _event(EventType.HUMAN_CHECKPOINT))
    mock_cls.assert_not_called()


async def test_smtp_checkpoint_when_enabled(audit):
    notifier = _make_notifier(audit=audit, smtp_cfg=_smtp_cfg(email_on_checkpoint=True))
    mock_cls, mock_instance = _mock_smtp()
    with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
        await _run(notifier, _event(EventType.HUMAN_CHECKPOINT))
    assert mock_instance.send_message.called


async def test_smtp_subject_contains_bossbox_prefix(audit):
    notifier = _make_notifier(audit=audit, smtp_cfg=_smtp_cfg())
    mock_cls, mock_instance = _mock_smtp()
    with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
        await _run(notifier, _event(title="Task done"))
    sent_msg = mock_instance.send_message.call_args.args[0]
    assert sent_msg["Subject"].startswith("[BossBox]")
    assert "Task done" in sent_msg["Subject"]


async def test_smtp_body_contains_event_type_not_credentials(audit):
    notifier = _make_notifier(audit=audit, smtp_cfg=_smtp_cfg(password="supersecret"))
    mock_cls, mock_instance = _mock_smtp()
    with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
        await _run(notifier, _event(EventType.TASK_COMPLETE, title="Done", body="summary"))
    sent_msg = mock_instance.send_message.call_args.args[0]
    payload = sent_msg.get_payload(decode=False)
    if isinstance(payload, list):
        body_text = payload[0].get_payload(decode=False)
    else:
        body_text = str(payload)
    assert "task_complete" in body_text
    assert "supersecret" not in body_text


async def test_smtp_no_tls_path(audit):
    notifier = _make_notifier(audit=audit, smtp_cfg=_smtp_cfg(use_tls=False))
    mock_cls, mock_instance = _mock_smtp()
    with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
        await _run(notifier, _event(EventType.TASK_COMPLETE))
    # starttls must NOT be called on the no-TLS path
    mock_instance.starttls.assert_not_called()
    assert mock_instance.send_message.called


async def test_smtp_failure_does_not_raise(audit):
    notifier = _make_notifier(audit=audit, smtp_cfg=_smtp_cfg())
    mock_cls, mock_instance = _mock_smtp()
    mock_instance.send_message.side_effect = smtplib.SMTPException("conn refused")
    with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
        await _run(notifier, _event())  # must not raise


async def test_smtp_disabled_no_call(audit):
    notifier = _make_notifier(audit=audit, smtp_cfg=_smtp_cfg(enabled=False))
    mock_cls, _ = _mock_smtp()
    with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
        await _run(notifier, _event(EventType.TASK_COMPLETE))
    mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


async def test_audit_log_contains_event_type(audit):
    notifier = _make_notifier(audit=audit)
    await _run(notifier, _event(EventType.TASK_FAILED, title="oops"))
    entries = audit.read_all()
    assert any(
        e["event_type"] == "notify_event"
        and e["data"]["event_type"] == "task_failed"
        for e in entries
    )


async def test_audit_log_never_contains_smtp_password(audit):
    password = "topsecretpassword"
    notifier = _make_notifier(audit=audit, smtp_cfg=_smtp_cfg(password=password))
    mock_cls, mock_instance = _mock_smtp()
    with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
        await _run(notifier, _event(EventType.TASK_COMPLETE))
    import json
    raw = (audit._path).read_text()
    assert password not in raw


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def test_notify_config_loads_from_yaml(tmp_path):
    from bossbox.config.loader import load_notify

    yaml_text = (
        "notify:\n"
        "  os_native:\n"
        "    enabled: false\n"
        "  ntfy:\n"
        "    enabled: true\n"
        "    base_url: https://ntfy.sh\n"
        "    topic: mybox\n"
    )
    p = tmp_path / "notify.yaml"
    p.write_text(yaml_text)
    cfg = load_notify(p)
    assert cfg.os_native.enabled is False
    assert cfg.ntfy is not None
    assert cfg.ntfy.enabled is True
    assert cfg.ntfy.topic == "mybox"
    assert cfg.smtp is None


def test_notify_config_defaults_when_file_absent(tmp_path):
    from bossbox.config.loader import load_config

    cfg = load_config(tmp_path)
    assert cfg.notify.os_native.enabled is True
    assert cfg.notify.ntfy is None
    assert cfg.notify.smtp is None


def test_notify_config_smtp_section_loads(tmp_path):
    from bossbox.config.loader import load_notify

    yaml_text = (
        "notify:\n"
        "  smtp:\n"
        "    enabled: true\n"
        "    host: smtp.example.com\n"
        "    port: 465\n"
        "    username: user\n"
        "    password: pass\n"
        "    from_address: from@example.com\n"
        "    to_address: to@example.com\n"
        "    use_tls: true\n"
        "    email_on_checkpoint: true\n"
    )
    p = tmp_path / "notify.yaml"
    p.write_text(yaml_text)
    cfg = load_notify(p)
    assert cfg.smtp is not None
    assert cfg.smtp.host == "smtp.example.com"
    assert cfg.smtp.port == 465
    assert cfg.smtp.email_on_checkpoint is True


def test_load_config_includes_notify(tmp_path):
    from bossbox.config.loader import load_config

    (tmp_path / "notify.yaml").write_text(
        "notify:\n  os_native:\n    enabled: false\n"
    )
    cfg = load_config(tmp_path)
    assert cfg.notify.os_native.enabled is False


# ---------------------------------------------------------------------------
# EventType enum
# ---------------------------------------------------------------------------


def test_event_type_values():
    assert EventType.TASK_COMPLETE == "task_complete"
    assert EventType.TASK_FAILED == "task_failed"
    assert EventType.HUMAN_CHECKPOINT == "human_checkpoint"
    assert EventType.INJECTION_WARN == "injection_warn"
    assert EventType.INJECTION_BLOCK == "injection_block"
    assert EventType.PRIVILEGE_ESCALATION == "privilege_escalation"
    assert EventType.MODEL_UPDATE == "model_update"
    assert EventType.ANOMALY_DETECTED == "anomaly_detected"


def test_all_event_types_have_eight_members():
    assert len(EventType) == 8


# need smtplib for the failure test above
import smtplib  # noqa: E402
