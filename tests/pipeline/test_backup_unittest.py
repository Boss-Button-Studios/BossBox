"""
Backup Manager Tests (unittest) — BossBox Atomic Step 13
=========================================================
Stdlib unittest mirror of test_backup.py.
Runnable with: python -m unittest tests.pipeline.test_backup_unittest -v
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from bossbox.pipeline.backup import BackupManager
from bossbox.pipeline.exceptions import OutsideWorkAreaError


class _WorkAreaMixin:
    """Sets up a temporary work area and tears it down."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)
        self.work_area = self.tmp_path / "workspace"
        self.work_area.mkdir()
        self.manager = BackupManager(work_area=self.work_area)
        self.source_file = self.work_area / "document.txt"
        self.source_file.write_text("original content")

    def tearDown(self):
        self._tmpdir.cleanup()


class TestBackupCreationUnittest(_WorkAreaMixin, unittest.TestCase):

    def test_backup_creates_a_file(self):
        dest = self.manager.backup(self.source_file)
        self.assertTrue(dest.exists())

    def test_backup_inside_backup_dir(self):
        dest = self.manager.backup(self.source_file)
        self.assertEqual(dest.parent, self.work_area / "backups")

    def test_backup_preserves_content(self):
        dest = self.manager.backup(self.source_file)
        self.assertEqual(dest.read_text(), "original content")

    def test_backup_preserves_suffix(self):
        dest = self.manager.backup(self.source_file)
        self.assertEqual(dest.suffix, ".txt")

    def test_backup_filename_contains_stem(self):
        dest = self.manager.backup(self.source_file)
        self.assertIn("document", dest.name)

    def test_backup_dir_created_automatically(self):
        self.assertFalse((self.work_area / "backups").exists())
        self.manager.backup(self.source_file)
        self.assertTrue((self.work_area / "backups").is_dir())

    def test_backup_accepts_string_path(self):
        dest = self.manager.backup(str(self.source_file))
        self.assertTrue(dest.exists())


class TestDistinctCopiesUnittest(_WorkAreaMixin, unittest.TestCase):

    def test_two_calls_produce_two_files(self):
        dest1 = self.manager.backup(self.source_file)
        dest2 = self.manager.backup(self.source_file)
        self.assertNotEqual(dest1, dest2)

    def test_two_files_both_exist(self):
        dest1 = self.manager.backup(self.source_file)
        dest2 = self.manager.backup(self.source_file)
        self.assertTrue(dest1.exists())
        self.assertTrue(dest2.exists())

    def test_rapid_succession_unique(self):
        destinations = [self.manager.backup(self.source_file) for _ in range(5)]
        self.assertEqual(len(set(destinations)), 5)

    def test_filenames_include_timestamp(self):
        dest = self.manager.backup(self.source_file)
        self.assertRegex(dest.name, r"\d{8}T\d{6}_\d{6}")


class TestWorkAreaEnforcementUnittest(_WorkAreaMixin, unittest.TestCase):

    def test_path_outside_raises(self):
        outside = self.tmp_path / "outside.txt"
        outside.write_text("bad")
        with self.assertRaises(OutsideWorkAreaError):
            self.manager.backup(outside)

    def test_absolute_outside_raises(self):
        with self.assertRaises(OutsideWorkAreaError):
            self.manager.backup(Path("/etc/passwd"))

    def test_inside_does_not_raise(self):
        dest = self.manager.backup(self.source_file)
        self.assertTrue(dest.exists())

    def test_error_message_includes_path(self):
        outside = self.tmp_path / "bad.txt"
        outside.write_text("x")
        with self.assertRaises(OutsideWorkAreaError) as ctx:
            self.manager.backup(outside)
        self.assertIn("bad.txt", str(ctx.exception))

    def test_symlink_traversal_blocked(self):
        target = self.tmp_path / "secret.txt"
        target.write_text("secret")
        link = self.work_area / "link.txt"
        link.symlink_to(target)
        with self.assertRaises(OutsideWorkAreaError):
            self.manager.backup(link)


class TestErrorCasesUnittest(_WorkAreaMixin, unittest.TestCase):

    def test_missing_source_raises_file_not_found(self):
        missing = self.work_area / "nonexistent.txt"
        with self.assertRaises(FileNotFoundError):
            self.manager.backup(missing)

    def test_backup_dir_never_deleted(self):
        self.manager.backup(self.source_file)
        backup_dir = self.work_area / "backups"
        self.assertTrue(backup_dir.exists())
        self.manager.backup(self.source_file)
        self.assertTrue(backup_dir.exists())


class TestBackupManagerConstructionUnittest(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)
        self.work_area = self.tmp_path / "workspace"
        self.work_area.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_work_area_stored(self):
        bm = BackupManager(work_area=self.work_area)
        self.assertEqual(bm.work_area, self.work_area.resolve())

    def test_backup_dir_is_backups_subdir(self):
        bm = BackupManager(work_area=self.work_area)
        self.assertEqual(bm.backup_dir, self.work_area.resolve() / "backups")

    def test_default_work_area_is_home_bossbox(self):
        bm = BackupManager()
        self.assertIn(".bossbox", str(bm.work_area))
        self.assertIn("workspace", str(bm.work_area))

    def test_string_work_area_accepted(self):
        bm = BackupManager(work_area=str(self.work_area))
        self.assertEqual(bm.work_area, self.work_area.resolve())


if __name__ == "__main__":
    unittest.main()
