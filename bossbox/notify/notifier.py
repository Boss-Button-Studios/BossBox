"""
Notification Service — BossBox Atomic Step 17
=============================================
Dispatches user-facing notifications through up to three channels:
  - OS native desktop notification (plyer, always enabled by default)
  - ntfy.sh push notification (optional, recommended for background tasks)
  - SMTP email (optional, strictly templated content only)

All channels are fire-and-forget: send() schedules async tasks and returns
immediately.  The internal queue is always updated synchronously before any
network call so callers can inspect what was sent regardless of channel outcome.

Credentials (SMTP password, ntfy topic) are never written to the audit trail
or application logs.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum

import httpx

from bossbox.audit.logger import AuditLogger
from bossbox.config.loader import NotifyConfig, NtfyNotifyConfig, SmtpNotifyConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    """Notification event types drawn from the spec §12.2 event matrix."""
    TASK_COMPLETE = "task_complete"
    TASK_FAILED = "task_failed"
    HUMAN_CHECKPOINT = "human_checkpoint"
    INJECTION_WARN = "injection_warn"
    INJECTION_BLOCK = "injection_block"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    MODEL_UPDATE = "model_update"
    ANOMALY_DETECTED = "anomaly_detected"


@dataclass
class NotifyEvent:
    """A single notification payload ready for dispatch."""
    event_type: EventType
    title: str
    body: str
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------


class Notifier:
    """
    Dispatches notifications across configured channels.

    Parameters
    ----------
    config:
        NotifyConfig from the loader.  Missing optional channels (ntfy, smtp)
        are silently skipped.
    audit_logger:
        Append-only audit trail.  Event metadata is logged; credentials are not.
    """

    def __init__(self, config: NotifyConfig, audit_logger: AuditLogger) -> None:
        self._config = config
        self._audit = audit_logger
        self._queue: list[NotifyEvent] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def queue(self) -> list[NotifyEvent]:
        """Return a snapshot of all dispatched events."""
        return list(self._queue)

    async def send(self, event: NotifyEvent) -> list[asyncio.Task]:
        """
        Dispatch *event* to all enabled channels.

        The internal queue is updated synchronously before any network calls.
        Each channel dispatch is an independent asyncio task — the pipeline
        never waits on notification delivery.

        Returns the list of created tasks.  Production callers typically discard
        this; tests await the tasks to verify channel behaviour.
        """
        self._queue.append(event)
        # Log metadata only — never log credentials or message body.
        self._audit.log(
            "notify_event",
            {"event_type": event.event_type.value, "title": event.title},
        )

        tasks: list[asyncio.Task] = []

        if self._config.os_native.enabled:
            tasks.append(asyncio.create_task(self._send_os(event)))

        if (
            self._config.ntfy is not None
            and self._config.ntfy.enabled
            and self._config.ntfy.topic
        ):
            tasks.append(asyncio.create_task(self._send_ntfy(event)))

        if (
            self._config.smtp is not None
            and self._config.smtp.enabled
            and self._should_send_email(event.event_type)
        ):
            tasks.append(asyncio.create_task(self._send_smtp(event)))

        return tasks

    # ------------------------------------------------------------------
    # Channel implementations
    # ------------------------------------------------------------------

    async def _send_os(self, event: NotifyEvent) -> None:
        """OS native desktop notification via plyer.

        Degrades gracefully when plyer is unavailable or no display is present
        (headless server, CI).  A warning is logged but no exception propagates.
        """
        try:
            import plyer  # noqa: PLC0415 — lazy import; allows headless installs
            plyer.notification.notify(
                title=event.title,
                message=event.body,
                app_name="BossBox",
            )
        except Exception as exc:
            log.warning("OS notification unavailable: %s", exc)

    async def _send_ntfy(self, event: NotifyEvent) -> None:
        """Publish to ntfy.sh or a self-hosted ntfy instance via HTTP POST."""
        cfg: NtfyNotifyConfig = self._config.ntfy  # type: ignore[assignment]
        url = f"{cfg.base_url.rstrip('/')}/{cfg.topic}"
        headers = {
            "Title": event.title,
            "Priority": _ntfy_priority(event.event_type),
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, content=event.body, headers=headers)
        except Exception as exc:
            log.warning("ntfy notification failed: %s", exc)

    async def _send_smtp(self, event: NotifyEvent) -> None:
        """Send a strictly templated email via SMTP in a thread executor."""
        cfg: SmtpNotifyConfig = self._config.smtp  # type: ignore[assignment]
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, _smtp_send_sync, event, cfg)
        except Exception as exc:
            log.warning("SMTP notification failed: %s", exc)

    # ------------------------------------------------------------------
    # Routing helpers
    # ------------------------------------------------------------------

    def _should_send_email(self, event_type: EventType) -> bool:
        """Return True if *event_type* warrants an email per spec §12.2."""
        cfg: SmtpNotifyConfig = self._config.smtp  # type: ignore[assignment]
        email_events = {EventType.TASK_COMPLETE, EventType.TASK_FAILED}
        if cfg.email_on_checkpoint:
            email_events.add(EventType.HUMAN_CHECKPOINT)
        return event_type in email_events


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


_URGENT_PRIORITY = {EventType.INJECTION_BLOCK, EventType.PRIVILEGE_ESCALATION}
_HIGH_PRIORITY = {
    EventType.HUMAN_CHECKPOINT,
    EventType.INJECTION_WARN,
    EventType.ANOMALY_DETECTED,
}


def _ntfy_priority(event_type: EventType) -> str:
    """Map an event type to an ntfy priority string."""
    if event_type in _URGENT_PRIORITY:
        return "urgent"
    if event_type in _HIGH_PRIORITY:
        return "high"
    return "default"


def _smtp_send_sync(event: NotifyEvent, cfg: SmtpNotifyConfig) -> None:
    """
    Synchronous SMTP send — runs in a thread executor.

    Email content is strictly templated per spec §12.1: event type, task title,
    outcome summary, and timestamp only.  No pipeline output, document content,
    or model reasoning is included.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[BossBox] {event.title}"
    msg["From"] = cfg.from_address
    msg["To"] = cfg.to_address
    msg.attach(MIMEText(_render_email_body(event), "plain"))

    if cfg.use_tls:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(cfg.host, cfg.port) as smtp:
            smtp.starttls(context=ctx)
            smtp.login(cfg.username, cfg.password)
            smtp.send_message(msg)
    else:
        # No STARTTLS: used when relaying through a local proxy such as
        # Proton Mail Bridge, which handles encryption upstream.
        with smtplib.SMTP(cfg.host, cfg.port) as smtp:
            smtp.login(cfg.username, cfg.password)
            smtp.send_message(msg)


def _render_email_body(event: NotifyEvent) -> str:
    """Return a strictly templated plain-text email body."""
    return (
        "BossBox Notification\n"
        "====================\n"
        f"Event:     {event.event_type.value}\n"
        f"Title:     {event.title}\n"
        f"Summary:   {event.body}\n"
        f"Timestamp: {event.timestamp.isoformat()}\n"
        "\n"
        "This is an automated summary from BossBox.\n"
        "No pipeline output or model reasoning is included in this message.\n"
    )
