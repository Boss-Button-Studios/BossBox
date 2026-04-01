"""
Backup Manager — BossBox Atomic Step 13
========================================
Provides timestamped, immutable backups of files before any destructive
pipeline operation (spec Section 10.5).

Design invariants
-----------------
- Writes only inside the work area (``~/.bossbox/workspace/``).
- Backups land in ``~/.bossbox/workspace/backups/``.
- The backup directory is **never deleted from** by this module.
- Each backup call produces a unique copy even when called in rapid succession
  (timestamp includes microseconds; a counter suffix breaks ties).
- Paths outside the work area raise ``OutsideWorkAreaError`` immediately.

Public API
----------
BackupManager(work_area=None)
    work_area defaults to ``~/.bossbox/workspace``.

BackupManager.backup(source_path) -> Path
    Copy *source_path* into the backup directory and return the backup path.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from bossbox.pipeline.exceptions import OutsideWorkAreaError

# Default work area location (can be overridden in tests / config)
_DEFAULT_WORK_AREA = Path.home() / ".bossbox" / "workspace"


class BackupManager:
    """
    Creates timestamped backups of files before destructive operations.

    Parameters
    ----------
    work_area:
        Root of the sandboxed work area.  Defaults to ``~/.bossbox/workspace``.
        All source paths must be inside this directory.
    """

    def __init__(self, work_area: Path | str | None = None) -> None:
        self.work_area: Path = (
            Path(work_area).expanduser().resolve()
            if work_area is not None
            else _DEFAULT_WORK_AREA.expanduser().resolve()
        )
        self.backup_dir: Path = self.work_area / "backups"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def backup(self, source_path: Path | str) -> Path:
        """
        Copy *source_path* into the backup directory.

        Parameters
        ----------
        source_path:
            Path to the file to back up.  Must exist and must be inside
            ``work_area``.

        Returns
        -------
        Path
            Absolute path to the new backup file.

        Raises
        ------
        OutsideWorkAreaError
            *source_path* resolves to a location outside ``work_area``.
        FileNotFoundError
            *source_path* does not exist.
        """
        source = Path(source_path).expanduser().resolve()
        self._assert_inside_work_area(source)

        if not source.exists():
            raise FileNotFoundError(f"Backup source not found: {source}")

        self.backup_dir.mkdir(parents=True, exist_ok=True)

        dest = self._unique_backup_path(source)
        shutil.copy2(source, dest)
        return dest

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_inside_work_area(self, path: Path) -> None:
        """Raise OutsideWorkAreaError if *path* is not under work_area."""
        try:
            path.relative_to(self.work_area)
        except ValueError:
            raise OutsideWorkAreaError(
                f"Path '{path}' is outside the work area '{self.work_area}'. "
                "BossBox may only operate on files within the work area."
            )

    def _unique_backup_path(self, source: Path) -> Path:
        """
        Build a backup destination path that is guaranteed unique.

        Format: ``<backup_dir>/<stem>_<timestamp>_<counter><suffix>``

        The timestamp uses microseconds.  A counter suffix (``_1``, ``_2``, …)
        is appended only when a collision still occurs despite the microsecond
        precision — this handles extremely rapid successive calls.
        """
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
        stem = source.stem
        suffix = source.suffix
        candidate = self.backup_dir / f"{stem}_{ts}{suffix}"

        counter = 0
        while candidate.exists():
            counter += 1
            candidate = self.backup_dir / f"{stem}_{ts}_{counter}{suffix}"

        return candidate
