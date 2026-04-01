"""
Backup Manager Tests — BossBox Atomic Step 13
=============================================
All tests use tmp_path so nothing touches ~/.bossbox.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from bossbox.pipeline.backup import BackupManager
from bossbox.pipeline.exceptions import OutsideWorkAreaError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def work_area(tmp_path: Path) -> Path:
    wa = tmp_path / "workspace"
    wa.mkdir()
    return wa


@pytest.fixture
def manager(work_area: Path) -> BackupManager:
    return BackupManager(work_area=work_area)


@pytest.fixture
def source_file(work_area: Path) -> Path:
    f = work_area / "document.txt"
    f.write_text("original content")
    return f


# ---------------------------------------------------------------------------
# Basic backup creation
# ---------------------------------------------------------------------------

class TestBackupCreation:

    def test_backup_creates_a_file(self, manager, source_file):
        dest = manager.backup(source_file)
        assert dest.exists()

    def test_backup_returns_path_inside_backup_dir(self, manager, source_file, work_area):
        dest = manager.backup(source_file)
        assert dest.parent == work_area / "backups"

    def test_backup_preserves_content(self, manager, source_file):
        dest = manager.backup(source_file)
        assert dest.read_text() == "original content"

    def test_backup_preserves_suffix(self, manager, source_file):
        dest = manager.backup(source_file)
        assert dest.suffix == ".txt"

    def test_backup_filename_contains_stem(self, manager, source_file):
        dest = manager.backup(source_file)
        assert "document" in dest.name

    def test_backup_dir_created_automatically(self, manager, source_file, work_area):
        assert not (work_area / "backups").exists()
        manager.backup(source_file)
        assert (work_area / "backups").is_dir()

    def test_backup_accepts_string_path(self, manager, source_file):
        dest = manager.backup(str(source_file))
        assert dest.exists()


# ---------------------------------------------------------------------------
# Acceptance: two calls produce two distinct timestamped copies
# ---------------------------------------------------------------------------

class TestDistinctCopies:

    def test_two_calls_produce_two_files(self, manager, source_file):
        dest1 = manager.backup(source_file)
        dest2 = manager.backup(source_file)
        assert dest1 != dest2

    def test_two_files_both_exist(self, manager, source_file):
        dest1 = manager.backup(source_file)
        dest2 = manager.backup(source_file)
        assert dest1.exists()
        assert dest2.exists()

    def test_rapid_succession_still_unique(self, manager, source_file):
        """Even if called in tight loop, all destinations are unique."""
        destinations = [manager.backup(source_file) for _ in range(5)]
        assert len(set(destinations)) == 5

    def test_backup_filenames_include_timestamp(self, manager, source_file):
        dest = manager.backup(source_file)
        # Timestamp format: YYYYMMDDTHHMMSS_ffffff
        import re
        assert re.search(r"\d{8}T\d{6}_\d{6}", dest.name)


# ---------------------------------------------------------------------------
# Acceptance: path outside work area raises OutsideWorkAreaError
# ---------------------------------------------------------------------------

class TestWorkAreaEnforcement:

    def test_path_outside_work_area_raises(self, manager, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("bad")
        with pytest.raises(OutsideWorkAreaError):
            manager.backup(outside)

    def test_absolute_path_outside_raises(self, manager):
        with pytest.raises(OutsideWorkAreaError):
            manager.backup(Path("/etc/passwd"))

    def test_path_inside_work_area_does_not_raise(self, manager, source_file):
        dest = manager.backup(source_file)
        assert dest.exists()

    def test_error_message_includes_path(self, manager, tmp_path):
        outside = tmp_path / "bad.txt"
        outside.write_text("x")
        with pytest.raises(OutsideWorkAreaError, match="bad.txt"):
            manager.backup(outside)

    def test_error_message_includes_work_area(self, manager, tmp_path, work_area):
        outside = tmp_path / "bad.txt"
        outside.write_text("x")
        with pytest.raises(OutsideWorkAreaError, match="workspace"):
            manager.backup(outside)

    def test_symlink_traversal_blocked(self, manager, work_area, tmp_path):
        """Symlink pointing outside work_area is caught after resolve()."""
        target = tmp_path / "secret.txt"
        target.write_text("secret")
        link = work_area / "link.txt"
        link.symlink_to(target)
        with pytest.raises(OutsideWorkAreaError):
            manager.backup(link)


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

class TestErrorCases:

    def test_missing_source_raises_file_not_found(self, manager, work_area):
        missing = work_area / "nonexistent.txt"
        with pytest.raises(FileNotFoundError):
            manager.backup(missing)

    def test_backup_dir_is_never_deleted(self, manager, source_file, work_area):
        """Sanity: the backup dir must never be cleaned up by the module."""
        manager.backup(source_file)
        backup_dir = work_area / "backups"
        assert backup_dir.exists()
        # Simulate a second round — dir still present
        manager.backup(source_file)
        assert backup_dir.exists()


# ---------------------------------------------------------------------------
# BackupManager construction
# ---------------------------------------------------------------------------

class TestBackupManagerConstruction:

    def test_work_area_stored(self, work_area):
        bm = BackupManager(work_area=work_area)
        assert bm.work_area == work_area.resolve()

    def test_backup_dir_is_backups_subdir(self, work_area):
        bm = BackupManager(work_area=work_area)
        assert bm.backup_dir == work_area.resolve() / "backups"

    def test_default_work_area_is_home_bossbox(self):
        bm = BackupManager()
        assert ".bossbox" in str(bm.work_area)
        assert "workspace" in str(bm.work_area)

    def test_string_work_area_accepted(self, work_area):
        bm = BackupManager(work_area=str(work_area))
        assert bm.work_area == work_area.resolve()
