"""
Append-only JSONL audit logger.

Writes one JSON object per line to ~/.bossbox/audit/audit.log.
File is created with 600 permissions on Unix. Never truncates.
Thread-safe for concurrent pipeline use.
"""

import json
import os
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_AUDIT_PATH = Path.home() / ".bossbox" / "audit" / "audit.log"

# Note (Step 21): Hypervisor audit entries will carry a salted hash of
# reasoning rather than plaintext — the hypervisor hashes before passing
# anything to the logger, so no logger changes are needed at that point.
# However, a read_all_decrypted(secrets_manager) method will be needed
# alongside read_all() to support reconstruction of hashed reasoning fields
# for the Security Center's full-detail view. Add it in Step 21 once
# SecretsManager exists. See spec §10.3.10.

# Module-level lock covers the default logger instance.
# Each AuditLogger instance carries its own lock so tests with
# isolated tmp paths don't contend with each other.


class AuditLogger:
    """
    Append-only JSONL logger. One JSON record per line, UTC timestamps.

    Parameters
    ----------
    log_path:
        Path to the audit log file. Defaults to ~/.bossbox/audit/audit.log.
        Tests should inject a tmp_path to stay isolated.
    """

    def __init__(self, log_path: Path = DEFAULT_AUDIT_PATH) -> None:
        self._path = Path(log_path)
        self._lock = threading.Lock()
        self._ensure_log_file()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> None:
        """
        Append one record to the audit log.

        Parameters
        ----------
        event_type:
            Short identifier for the event class, e.g. "stage_transition",
            "hypervisor_block", "privilege_escalation_request".
        data:
            Arbitrary key/value payload. Defaults to empty dict.
            Values that are not natively JSON-serialisable are coerced to
            strings via ``default=str`` so the logger never raises on
            unusual types.
        task_id:
            The TaskEnvelope task_id this event belongs to, if any.
        """
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "task_id": task_id,
            "data": data if data is not None else {},
        }
        line = json.dumps(entry, default=str)

        with self._lock:
            # Open in append mode on every call — this is the simplest way
            # to guarantee never-truncate across process restarts.
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        """
        Return every entry in the log as a list of dicts.

        Intended for the Security Center event log and for tests.
        Returns an empty list if the file does not exist or is empty.
        """
        if not self._path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with open(self._path, encoding="utf-8") as fh:
            for raw_line in fh:
                stripped = raw_line.strip()
                if stripped:
                    entries.append(json.loads(stripped))
        return entries

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_log_file(self) -> None:
        """Create the log file and parent directories if they don't exist.

        Sets 600 permissions on Unix. On Windows the open() call is
        sufficient; ACL-based restriction is left to the installer.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.touch()
            _set_600(self._path)


def _set_600(path: Path) -> None:
    """Apply owner-read/write-only permissions on platforms that support it."""
    if os.name != "nt":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
