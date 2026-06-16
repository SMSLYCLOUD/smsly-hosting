# pylint: disable=invalid-name
"""Regression test: ``_safe_tar_extractall`` must refuse tarballs that
contain a symlink (or hardlink) whose target resolves outside the extract
directory. This is the primary defence against tar-slip / symlink-escape
attacks via crafted backups.
"""
import os
import sys
import tarfile
import tempfile
import unittest

from apps.deployments.services.backup_service import _safe_tar_extractall


def _make_tar_with_symlink(tmpdir, link_name='evil', link_target='/etc/passwd'):
    """Build a tarball containing a single symlink member pointing outside."""
    archive_path = os.path.join(tmpdir, 'evil.tar.gz')
    with tarfile.open(archive_path, 'w:gz') as tar:
        info = tarfile.TarInfo(name=link_name)
        info.type = tarfile.SYMTYPE
        info.linkname = link_target
        tar.addfile(info)
    return archive_path


def _make_tar_with_relative_symlink_escape(tmpdir):
    """Build a tarball with a symlink whose target escapes via '..'."""
    archive_path = os.path.join(tmpdir, 'evil2.tar.gz')
    with tarfile.open(archive_path, 'w:gz') as tar:
        info = tarfile.TarInfo(name='inner/escape')
        info.type = tarfile.SYMTYPE
        info.linkname = '../../../etc/passwd'
        tar.addfile(info)
    return archive_path


def _make_tar_with_hardlink(tmpdir):
    """Build a tarball with a hardlink to /etc/passwd."""
    archive_path = os.path.join(tmpdir, 'evil3.tar.gz')
    with tarfile.open(archive_path, 'w:gz') as tar:
        info = tarfile.TarInfo(name='hard')
        info.type = tarfile.LNKTYPE
        info.linkname = '/etc/passwd'
        tar.addfile(info)
    return archive_path


def _make_tar_with_safe_symlink(tmpdir):
    """Build a tarball with an INTERNAL symlink (legitimate use)."""
    archive_path = os.path.join(tmpdir, 'safe.tar.gz')
    with tarfile.open(archive_path, 'w:gz') as tar:
        # A regular file so the link target has something to resolve to.
        real = tarfile.TarInfo(name='data/real.txt')
        data = b'hello'
        real.size = len(data)
        import io as _io
        tar.addfile(real, _io.BytesIO(data))
        # A symlink inside the archive.
        info = tarfile.TarInfo(name='data/link.txt')
        info.type = tarfile.SYMTYPE
        info.linkname = 'real.txt'
        tar.addfile(info)
    return archive_path


class SafeTarExtractAllTests(unittest.TestCase):
    """Verify _safe_tar_extractall blocks symlink/hardlink escapes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='tar-symlink-')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_absolute_symlink_target_is_rejected(self):
        archive = _make_tar_with_symlink(self.tmp)
        dest = os.path.join(self.tmp, 'extract')
        os.makedirs(dest, exist_ok=True)
        with tarfile.open(archive, 'r:gz') as tar:
            with self.assertRaises(ValueError):
                _safe_tar_extractall(tar, dest)

    def test_relative_symlink_escape_is_rejected(self):
        archive = _make_tar_with_relative_symlink_escape(self.tmp)
        dest = os.path.join(self.tmp, 'extract2')
        os.makedirs(dest, exist_ok=True)
        with tarfile.open(archive, 'r:gz') as tar:
            with self.assertRaises(ValueError):
                _safe_tar_extractall(tar, dest)

    def test_absolute_hardlink_is_rejected(self):
        archive = _make_tar_with_hardlink(self.tmp)
        dest = os.path.join(self.tmp, 'extract3')
        os.makedirs(dest, exist_ok=True)
        with tarfile.open(archive, 'r:gz') as tar:
            with self.assertRaises(ValueError):
                _safe_tar_extractall(tar, dest)

    def test_internal_symlink_is_allowed(self):
        archive = _make_tar_with_safe_symlink(self.tmp)
        dest = os.path.join(self.tmp, 'extract4')
        os.makedirs(dest, exist_ok=True)
        with tarfile.open(archive, 'r:gz') as tar:
            # Should NOT raise.
            _safe_tar_extractall(tar, dest)
        # And the file should be readable.
        self.assertTrue(os.path.exists(os.path.join(dest, 'data', 'link.txt')))

    def test_uses_data_filter_on_python_312(self):
        # Sanity check: the helper should not regress on Python 3.12+
        # where filter='data' is the authoritative defence.
        archive = _make_tar_with_symlink(self.tmp)
        dest = os.path.join(self.tmp, 'extract5')
        os.makedirs(dest, exist_ok=True)
        if sys.version_info >= (3, 12):
            with tarfile.open(archive, 'r:gz') as tar:
                with self.assertRaises((ValueError, tarfile.LinkOutsideDestinationError, tarfile.OutsideDestinationError, tarfile.FilterError)):
                    _safe_tar_extractall(tar, dest)
        else:
            with tarfile.open(archive, 'r:gz') as tar:
                with self.assertRaises(ValueError):
                    _safe_tar_extractall(tar, dest)
