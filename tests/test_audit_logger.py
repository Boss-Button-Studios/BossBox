"""
Regression tests for bossbox.audit.logger.AuditLogger.

All tests use pytest's tmp_path fixture to keep log files isolated from
~/.bossbox and from each other.  No test touches the default log path.
"""

import json
import os
import stat
import sys
import threading
from pathlib import Path

import pytest

from bossbox.audit.logger import AuditLogger


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    """A fresh, isolated log file path for each test."""
    return tmp_path / "audit.log"


@pytest.fixture
def logger(log_path: Path) -> AuditLogger:
    """A ready-to-use AuditLogger backed by the isolated tmp log path."""
    return AuditLogger(log_path=log_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_lines(path: Path) -> list[str]:
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _read_entries(path: Path) -> list[dict]:
    return [json.loads(l) for l in _read_lines(path)]


# ---------------------------------------------------------------------------
# Append behaviour — the core acceptance criteria
# ---------------------------------------------------------------------------


class TestAppendBehaviour:
    def test_ten_calls_produce_ten_lines(self, logger: AuditLogger, log_path: Path):
        for i in range(10):
            logger.log("test_event", {"index": i})
        assert len(_read_lines(log_path)) == 10

    def test_each_line_is_valid_json(self, logger: AuditLogger, log_path: Path):
        for i in range(5):
            logger.log("test_event", {"index": i})
        for line in _read_lines(log_path):
            json.loads(line)  # raises on malformed JSON — that's the assertion

    def test_restart_appends_not_overwrites(self, log_path: Path):
        """Simulates a process restart by constructing two independent logger
        instances pointing at the same path."""
        first = AuditLogger(log_path=log_path)
        for i in range(5):
            first.log("first_session", {"i": i})

        second = AuditLogger(log_path=log_path)
        for i in range(5):
            second.log("second_session", {"i": i})

        entries = _read_entries(log_path)
        assert len(entries) == 10

        first_batch = entries[:5]
        second_batch = entries[5:]
        assert all(e["event_type"] == "first_session" for e in first_batch)
        assert all(e["event_type"] == "second_session" for e in second_batch)

    def test_file_never_truncated_between_instances(self, log_path: Path):
        """Belt-and-suspenders: open/write/close cycle must not truncate."""
        logger_a = AuditLogger(log_path=log_path)
        logger_a.log("alpha")
        size_after_one = log_path.stat().st_size

        logger_b = AuditLogger(log_path=log_path)
        logger_b.log("beta")
        size_after_two = log_path.stat().st_size

        assert size_after_two > size_after_one


# ---------------------------------------------------------------------------
# Record structure
# ---------------------------------------------------------------------------


class TestRecordStructure:
    def test_required_fields_present(self, logger: AuditLogger, log_path: Path):
        logger.log("stage_transition")
        entry = _read_entries(log_path)[0]
        assert "timestamp" in entry
        assert "event_type" in entry
        assert "task_id" in entry
        assert "data" in entry

    def test_event_type_stored_verbatim(self, logger: AuditLogger, log_path: Path):
        logger.log("hypervisor_block")
        entry = _read_entries(log_path)[0]
        assert entry["event_type"] == "hypervisor_block"

    def test_task_id_stored_when_provided(self, logger: AuditLogger, log_path: Path):
        logger.log("stage_transition", task_id="task-abc-123")
        entry = _read_entries(log_path)[0]
        assert entry["task_id"] == "task-abc-123"

    def test_task_id_is_none_when_omitted(self, logger: AuditLogger, log_path: Path):
        logger.log("bare_event")
        entry = _read_entries(log_path)[0]
        assert entry["task_id"] is None

    def test_data_payload_round_trips(self, logger: AuditLogger, log_path: Path):
        payload = {"stage": "ingest", "model": "smollm-360m", "duration_ms": 42}
        logger.log("model_invocation", data=payload)
        entry = _read_entries(log_path)[0]
        assert entry["data"] == payload

    def test_empty_data_defaults_to_empty_dict(self, logger: AuditLogger, log_path: Path):
        logger.log("bare_event")
        entry = _read_entries(log_path)[0]
        assert entry["data"] == {}

    def test_timestamp_is_utc_iso8601(self, logger: AuditLogger, log_path: Path):
        logger.log("ts_test")
        entry = _read_entries(log_path)[0]
        ts = entry["timestamp"]
        # UTC offset indicator must be present — either +00:00 or Z
        assert ts.endswith("+00:00") or ts.endswith("Z"), (
            f"Expected UTC ISO-8601 timestamp, got: {ts!r}"
        )

    def test_non_serialisable_values_coerced_to_string(
        self, logger: AuditLogger, log_path: Path
    ):
        """Logger must not raise when data contains non-JSON-native types."""
        from pathlib import PurePosixPath
        logger.log("coerce_test", data={"path": PurePosixPath("/tmp/x")})
        entry = _read_entries(log_path)[0]
        assert isinstance(entry["data"]["path"], str)


# ---------------------------------------------------------------------------
# File-system and permissions
# ---------------------------------------------------------------------------


class TestFileSystem:
    def test_parent_directories_created_automatically(self, tmp_path: Path):
        deep_path = tmp_path / "a" / "b" / "c" / "audit.log"
        logger = AuditLogger(log_path=deep_path)
        logger.log("deep_dir_test")
        assert deep_path.exists()

    def test_log_file_created_on_init(self, log_path: Path):
        AuditLogger(log_path=log_path)
        assert log_path.exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix file permissions only")
    def test_new_file_has_600_permissions(self, log_path: Path):
        AuditLogger(log_path=log_path)
        mode = stat.S_IMODE(os.stat(log_path).st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix file permissions only")
    def test_permissions_stable_across_reopens(self, log_path: Path):
        """A second AuditLogger instance on an existing file must not
        widen permissions."""
        AuditLogger(log_path=log_path).log("first")
        AuditLogger(log_path=log_path).log("second")
        mode = stat.S_IMODE(os.stat(log_path).st_mode)
        assert mode == 0o600


# ---------------------------------------------------------------------------
# read_all()
# ---------------------------------------------------------------------------


class TestReadAll:
    def test_read_all_returns_all_entries(self, logger: AuditLogger, log_path: Path):
        for i in range(7):
            logger.log("ev", {"i": i})
        assert len(logger.read_all()) == 7

    def test_read_all_preserves_order(self, logger: AuditLogger, log_path: Path):
        for i in range(5):
            logger.log("ev", {"i": i})
        indices = [e["data"]["i"] for e in logger.read_all()]
        assert indices == list(range(5))

    def test_read_all_returns_empty_list_when_file_missing(self, tmp_path: Path):
        logger = AuditLogger(log_path=tmp_path / "ghost.log")
        # Remove the file that __init__ created so we can test the absent case.
        (tmp_path / "ghost.log").unlink()
        assert logger.read_all() == []

    def test_read_all_returns_empty_list_on_empty_file(self, log_path: Path):
        logger = AuditLogger(log_path=log_path)
        # Don't log anything — file exists but is empty.
        assert logger.read_all() == []


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_writes_all_recorded(self, logger: AuditLogger, log_path: Path):
        n = 100
        threads = [
            threading.Thread(target=logger.log, args=(f"event_{i}", {"i": i}))
            for i in range(n)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(_read_lines(log_path)) == n

    def test_concurrent_writes_all_valid_json(self, logger: AuditLogger, log_path: Path):
        threads = [
            threading.Thread(target=logger.log, args=(f"ev_{i}", {"i": i}))
            for i in range(100)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for line in _read_lines(log_path):
            json.loads(line)  # must not raise

    def test_concurrent_writes_no_interleaved_lines(
        self, logger: AuditLogger, log_path: Path
    ):
        """Each line must be complete and independently parseable — no
        partial writes interleaved across threads."""
        threads = [
            threading.Thread(
                target=logger.log,
                args=("concurrent", {"payload": "x" * 500, "idx": i}),
            )
            for i in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # If any line is corrupt this will raise
        entries = _read_entries(log_path)
        assert len(entries) == 50
