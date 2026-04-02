"""
Notification Service Tests (unittest) — BossBox Atomic Step 17
===============================================================
Stdlib unittest mirror of test_notifier.py.
Runnable with: python -m unittest tests.notify.test_notifier_unittest -v
"""
from __future__ import annotations

import asyncio
import smtplib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
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
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


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


def _mock_smtp():
    mock_instance = MagicMock()
    mock_cls = MagicMock()
    mock_cls.return_value.__enter__.return_value = mock_instance
    mock_cls.return_value.__exit__.return_value = False
    return mock_cls, mock_instance


async def _send(notifier: Notifier, event: NotifyEvent) -> None:
    tasks = await notifier.send(event)
    if tasks:
        await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Base test case with tmp audit logger
# ---------------------------------------------------------------------------


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.audit = AuditLogger(log_path=Path(self._tmpdir.name) / "audit.log")

    def tearDown(self):
        self._tmpdir.cleanup()


# ---------------------------------------------------------------------------
# Queue behaviour
# ---------------------------------------------------------------------------


class TestQueue(_Base):
    def test_queue_appended_on_send(self):
        notifier = _make_notifier(audit=self.audit)
        event = _event()
        _run(_send(notifier, event))
        self.assertEqual(len(notifier.queue()), 1)
        self.assertIs(notifier.queue()[0], event)

    def test_queue_returns_copy(self):
        notifier = _make_notifier(audit=self.audit)
        _run(_send(notifier, _event()))
        self.assertIsNot(notifier.queue(), notifier.queue())

    def test_queue_accumulates(self):
        notifier = _make_notifier(audit=self.audit)
        _run(_send(notifier, _event(EventType.TASK_COMPLETE)))
        _run(_send(notifier, _event(EventType.TASK_FAILED)))
        self.assertEqual(len(notifier.queue()), 2)

    def test_queue_updated_with_no_channels(self):
        notifier = _make_notifier(audit=self.audit, os_native=False)
        _run(_send(notifier, _event()))
        self.assertEqual(len(notifier.queue()), 1)


# ---------------------------------------------------------------------------
# OS native channel
# ---------------------------------------------------------------------------


class TestOsNative(_Base):
    def test_os_native_calls_plyer(self):
        notifier = _make_notifier(audit=self.audit, os_native=True)
        mock_plyer = MagicMock()
        with patch.dict(sys.modules, {"plyer": mock_plyer}):
            _run(_send(notifier, _event()))
        mock_plyer.notification.notify.assert_called_once()

    def test_os_native_disabled_no_plyer(self):
        notifier = _make_notifier(audit=self.audit, os_native=False)
        mock_plyer = MagicMock()
        with patch.dict(sys.modules, {"plyer": mock_plyer}):
            _run(_send(notifier, _event()))
        mock_plyer.notification.notify.assert_not_called()

    def test_os_native_import_error_no_raise(self):
        notifier = _make_notifier(audit=self.audit, os_native=True)
        with patch.dict(sys.modules, {"plyer": None}):
            _run(_send(notifier, _event()))  # must not raise

    def test_os_native_notify_error_no_raise(self):
        notifier = _make_notifier(audit=self.audit, os_native=True)
        mock_plyer = MagicMock()
        mock_plyer.notification.notify.side_effect = RuntimeError("no display")
        with patch.dict(sys.modules, {"plyer": mock_plyer}):
            _run(_send(notifier, _event()))  # must not raise


# ---------------------------------------------------------------------------
# ntfy priority
# ---------------------------------------------------------------------------


class TestNtfyPriority(unittest.TestCase):
    def test_injection_block_is_urgent(self):
        self.assertEqual(_ntfy_priority(EventType.INJECTION_BLOCK), "urgent")

    def test_privilege_escalation_is_urgent(self):
        self.assertEqual(_ntfy_priority(EventType.PRIVILEGE_ESCALATION), "urgent")

    def test_checkpoint_is_high(self):
        self.assertEqual(_ntfy_priority(EventType.HUMAN_CHECKPOINT), "high")

    def test_task_complete_is_default(self):
        self.assertEqual(_ntfy_priority(EventType.TASK_COMPLETE), "default")


# ---------------------------------------------------------------------------
# ntfy.sh channel
# ---------------------------------------------------------------------------


class TestNtfy(_Base):
    def test_ntfy_posts_correct_url(self):
        notifier = _make_notifier(audit=self.audit, ntfy_cfg=_ntfy_cfg(topic="mybox"))
        with respx.mock:
            route = respx.post("https://ntfy.sh/mybox").mock(
                return_value=httpx.Response(200)
            )
            _run(_send(notifier, _event()))
        self.assertTrue(route.called)

    def test_ntfy_custom_base_url(self):
        notifier = _make_notifier(
            audit=self.audit,
            ntfy_cfg=_ntfy_cfg(base_url="https://push.internal", topic="bb"),
        )
        with respx.mock:
            route = respx.post("https://push.internal/bb").mock(
                return_value=httpx.Response(200)
            )
            _run(_send(notifier, _event()))
        self.assertTrue(route.called)

    def test_ntfy_disabled_no_request(self):
        notifier = _make_notifier(audit=self.audit, ntfy_cfg=_ntfy_cfg(enabled=False))
        with respx.mock:
            route = respx.post("https://ntfy.sh/test-topic").mock(
                return_value=httpx.Response(200)
            )
            _run(_send(notifier, _event()))
        self.assertFalse(route.called)

    def test_ntfy_failure_does_not_raise(self):
        notifier = _make_notifier(audit=self.audit, ntfy_cfg=_ntfy_cfg())
        with respx.mock:
            respx.post("https://ntfy.sh/test-topic").mock(
                side_effect=httpx.ConnectError("refused")
            )
            _run(_send(notifier, _event()))  # must not raise


# ---------------------------------------------------------------------------
# SMTP channel
# ---------------------------------------------------------------------------


class TestSmtp(_Base):
    def test_smtp_task_complete(self):
        notifier = _make_notifier(audit=self.audit, smtp_cfg=_smtp_cfg())
        mock_cls, mock_instance = _mock_smtp()
        with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
            _run(_send(notifier, _event(EventType.TASK_COMPLETE)))
        self.assertTrue(mock_instance.send_message.called)

    def test_smtp_task_failed(self):
        notifier = _make_notifier(audit=self.audit, smtp_cfg=_smtp_cfg())
        mock_cls, mock_instance = _mock_smtp()
        with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
            _run(_send(notifier, _event(EventType.TASK_FAILED)))
        self.assertTrue(mock_instance.send_message.called)

    def test_smtp_not_called_for_injection_warn(self):
        notifier = _make_notifier(audit=self.audit, smtp_cfg=_smtp_cfg())
        mock_cls, _ = _mock_smtp()
        with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
            _run(_send(notifier, _event(EventType.INJECTION_WARN)))
        mock_cls.assert_not_called()

    def test_smtp_checkpoint_off_by_default(self):
        notifier = _make_notifier(
            audit=self.audit, smtp_cfg=_smtp_cfg(email_on_checkpoint=False)
        )
        mock_cls, _ = _mock_smtp()
        with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
            _run(_send(notifier, _event(EventType.HUMAN_CHECKPOINT)))
        mock_cls.assert_not_called()

    def test_smtp_checkpoint_when_enabled(self):
        notifier = _make_notifier(
            audit=self.audit, smtp_cfg=_smtp_cfg(email_on_checkpoint=True)
        )
        mock_cls, mock_instance = _mock_smtp()
        with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
            _run(_send(notifier, _event(EventType.HUMAN_CHECKPOINT)))
        self.assertTrue(mock_instance.send_message.called)

    def test_smtp_subject_prefix(self):
        notifier = _make_notifier(audit=self.audit, smtp_cfg=_smtp_cfg())
        mock_cls, mock_instance = _mock_smtp()
        with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
            _run(_send(notifier, _event(title="Done")))
        sent = mock_instance.send_message.call_args.args[0]
        self.assertTrue(sent["Subject"].startswith("[BossBox]"))

    def test_smtp_no_tls_skips_starttls(self):
        notifier = _make_notifier(audit=self.audit, smtp_cfg=_smtp_cfg(use_tls=False))
        mock_cls, mock_instance = _mock_smtp()
        with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
            _run(_send(notifier, _event(EventType.TASK_COMPLETE)))
        mock_instance.starttls.assert_not_called()
        self.assertTrue(mock_instance.send_message.called)

    def test_smtp_failure_does_not_raise(self):
        notifier = _make_notifier(audit=self.audit, smtp_cfg=_smtp_cfg())
        mock_cls, mock_instance = _mock_smtp()
        mock_instance.send_message.side_effect = smtplib.SMTPException("err")
        with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
            _run(_send(notifier, _event()))  # must not raise

    def test_smtp_disabled_no_call(self):
        notifier = _make_notifier(audit=self.audit, smtp_cfg=_smtp_cfg(enabled=False))
        mock_cls, _ = _mock_smtp()
        with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
            _run(_send(notifier, _event(EventType.TASK_COMPLETE)))
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class TestAuditLog(_Base):
    def test_audit_contains_event_type(self):
        notifier = _make_notifier(audit=self.audit)
        _run(_send(notifier, _event(EventType.TASK_FAILED)))
        entries = self.audit.read_all()
        self.assertTrue(
            any(
                e["event_type"] == "notify_event"
                and e["data"]["event_type"] == "task_failed"
                for e in entries
            )
        )

    def test_audit_never_contains_smtp_password(self):
        password = "topsecretpassword"
        notifier = _make_notifier(audit=self.audit, smtp_cfg=_smtp_cfg(password=password))
        mock_cls, mock_instance = _mock_smtp()
        with patch("bossbox.notify.notifier.smtplib.SMTP", mock_cls):
            _run(_send(notifier, _event(EventType.TASK_COMPLETE)))
        raw = self.audit._path.read_text()
        self.assertNotIn(password, raw)


# ---------------------------------------------------------------------------
# EventType enum
# ---------------------------------------------------------------------------


class TestEventType(unittest.TestCase):
    def test_event_type_count(self):
        self.assertEqual(len(EventType), 8)

    def test_event_type_string_values(self):
        self.assertEqual(EventType.TASK_COMPLETE, "task_complete")
        self.assertEqual(EventType.TASK_FAILED, "task_failed")
        self.assertEqual(EventType.HUMAN_CHECKPOINT, "human_checkpoint")
        self.assertEqual(EventType.INJECTION_WARN, "injection_warn")
        self.assertEqual(EventType.INJECTION_BLOCK, "injection_block")
        self.assertEqual(EventType.PRIVILEGE_ESCALATION, "privilege_escalation")
        self.assertEqual(EventType.MODEL_UPDATE, "model_update")
        self.assertEqual(EventType.ANOMALY_DETECTED, "anomaly_detected")


if __name__ == "__main__":
    unittest.main()
