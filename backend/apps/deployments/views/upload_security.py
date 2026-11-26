"""Shared upload validation helpers for file upload security."""
import io
import logging
import stat
import tarfile
import zipfile

from django.conf import settings

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB
DEFAULT_MAX_UNCOMPRESSED_SIZE = 500 * 1024 * 1024  # 500MB (zip bomb limit)
MAX_KEY_FILE_SIZE = 1024 * 1024  # 1MB for JSON key files

# Magic bytes for archive validation
ZIP_MAGIC = b'PK\x03\x04'
GZIP_MAGIC = b'\x1f\x8b'


def validate_upload_size(uploaded_file, max_size=None):
    """Validate uploaded file size. Returns error Response or None."""
    from rest_framework import status
    from rest_framework.response import Response

    if max_size is None:
        max_size = getattr(settings, 'MAX_UPLOAD_SIZE', DEFAULT_MAX_UPLOAD_SIZE)
    if uploaded_file.size > max_size:
        size_mb = uploaded_file.size / 1024 / 1024
        max_mb = max_size / 1024 / 1024
        return Response(
            {'error': f'File size ({size_mb:.1f}MB) exceeds maximum limit ({max_mb:.0f}MB).'},
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        )
    return None


def validate_zip_magic(uploaded_file):
    """Validate that a file has valid zip magic bytes. Returns error Response or None."""
    from rest_framework import status
    from rest_framework.response import Response

    try:
        uploaded_file.seek(0)
        header = uploaded_file.read(4)
        uploaded_file.seek(0)
    except Exception:
        return Response(
            {'error': 'Unable to read file header.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if header != ZIP_MAGIC:
        return Response(
            {'error': 'Invalid zip file: missing magic bytes.'},
            status=status.HTTP_400_BAD_REQUEST
        )
    return None


def validate_tar_magic(uploaded_file):
    """Validate that a file has valid tar.gz magic bytes. Returns error Response or None."""
    from rest_framework import status
    from rest_framework.response import Response

    try:
        uploaded_file.seek(0)
        header = uploaded_file.read(2)
        uploaded_file.seek(0)
    except Exception:
        return Response(
            {'error': 'Unable to read file header.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if header != GZIP_MAGIC:
        return Response(
            {'error': 'Invalid tar.gz file: missing gzip magic bytes.'},
            status=status.HTTP_400_BAD_REQUEST
        )
    return None


def _is_unsafe_archive_path(raw_name: str) -> bool:
    norm = (raw_name or '').replace('\\', '/')
    if not norm or norm.startswith('/'):
        return True
    parts = [p for p in norm.split('/') if p not in ('', '.')]
    if '..' in parts:
        return True
    if parts and ':' in parts[0]:
        return True
    return False


def validate_zip_entries(uploaded_file):
    """Check zip for path traversal and symlink entries. Returns (is_safe, error_msg)."""
    try:
        uploaded_file.seek(0)
        with zipfile.ZipFile(uploaded_file, 'r') as zf:
            for info in zf.infolist():
                name = info.filename
                if _is_unsafe_archive_path(name):
                    return False, f'Unsafe zip entry detected: {name}'
                unix_mode = (info.external_attr >> 16) & 0o170000
                if unix_mode == stat.S_IFLNK:
                    return False, f'Unsafe symlink entry detected in zip: {name}'
        uploaded_file.seek(0)
        return True, None
    except zipfile.BadZipFile:
        return False, 'Invalid zip file.'
    except Exception as exc:
        logger.warning("Zip validation error: %s", exc)
        return False, f'Failed to validate zip: {exc}'


def validate_tar_entries(uploaded_file, max_uncompressed=None):
    """Check tar.gz/tgz for path traversal, special/symlink entries, and size bombs."""
    if max_uncompressed is None:
        max_uncompressed = getattr(settings, 'MAX_UNCOMPRESSED_UPLOAD_SIZE', DEFAULT_MAX_UNCOMPRESSED_SIZE)
    try:
        uploaded_file.seek(0)
        total_uncompressed = 0
        with tarfile.open(fileobj=uploaded_file, mode='r:gz') as tf:
            for member in tf.getmembers():
                if _is_unsafe_archive_path(member.name):
                    uploaded_file.seek(0)
                    return False, f'Unsafe tar entry detected: {member.name}'
                if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    uploaded_file.seek(0)
                    return False, f'Unsafe link/device entry detected in archive: {member.name}'
                total_uncompressed += max(0, member.size)
                if total_uncompressed > max_uncompressed:
                    uploaded_file.seek(0)
                    max_mb = max_uncompressed / 1024 / 1024
                    return False, f'Archive bomb detected: uncompressed size exceeds {max_mb:.0f}MB limit.'
        uploaded_file.seek(0)
        return True, None
    except tarfile.TarError:
        return False, 'Invalid tar.gz archive.'
    except Exception as exc:
        logger.warning("Tar validation error: %s", exc)
        return False, f'Failed to validate tar.gz archive: {exc}'


def validate_zip_no_bomb(uploaded_file, max_uncompressed=None):
    """Protect against zip bombs by checking uncompressed size. Returns error Response or None."""
    from rest_framework import status
    from rest_framework.response import Response

    if max_uncompressed is None:
        max_uncompressed = getattr(settings, 'MAX_UNCOMPRESSED_UPLOAD_SIZE', DEFAULT_MAX_UNCOMPRESSED_SIZE)

    try:
        uploaded_file.seek(0)
        with zipfile.ZipFile(uploaded_file, 'r') as zf:
            total_uncompressed = 0
            for info in zf.infolist():
                total_uncompressed += info.file_size
                if total_uncompressed > max_uncompressed:
                    uploaded_file.seek(0)
                    max_mb = max_uncompressed / 1024 / 1024
                    return Response(
                        {'error': f'Zip bomb detected: uncompressed size exceeds {max_mb:.0f}MB limit.'},
                        status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                    )
        uploaded_file.seek(0)
        return None
    except zipfile.BadZipFile:
        return None  # Not a valid zip, let other validation handle it
    except Exception as exc:
        logger.warning("Zip bomb check error: %s", exc)
        uploaded_file.seek(0)
        return None


def validate_key_file(uploaded_file, max_size=None):
    """Validate an uploaded JSON key file for size and basic safety. Returns error Response or None."""
    from rest_framework import status
    from rest_framework.response import Response

    if max_size is None:
        max_size = MAX_KEY_FILE_SIZE
    if uploaded_file.size > max_size:
        return Response(
            {'error': f'Key file too large. Maximum size is {max_size // 1024}KB.'},
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        )
    return None
