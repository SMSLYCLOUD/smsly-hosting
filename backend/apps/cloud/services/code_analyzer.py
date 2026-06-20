"""Shared repository file-analysis helpers for the cloud app.

Centralises the total-byte cap and the file-walk logic that is
shared between ``cloud.views.analyze_repo`` and
``cloud.views_code_analysis.analyze_codebase``. The 50 MB cap is
the canonical guard against an attacker shipping a 10 GB file
that would OOM the analysis process (Issue 65 / 154 / 195).
"""
import os
from collections.abc import Iterator
from dataclasses import dataclass

MAX_TOTAL_BYTES = 50 * 1024 * 1024


def _safe_getsize(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


@dataclass
class RepoWalk:
    total_bytes: int
    capped: bool
    file_count: int


def iter_repo_files(
    repo_path: str,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> Iterator[tuple[str, str, int]]:
    """Yield (absolute_path, rel_path, size) for files under ``repo_path``.

    Stops once accumulated size would exceed ``max_total_bytes``;
    callers that want to surface the over-cap error should call
    :func:`walk_repo_with_cap` instead.

    Symbolic links are not followed.
    """
    total = 0
    for root, _, filenames in os.walk(repo_path, followlinks=False):
        for filename in filenames:
            abs_path = os.path.join(root, filename)
            size = _safe_getsize(abs_path)
            if total + size > max_total_bytes:
                return
            total += size
            rel_path = os.path.relpath(abs_path, repo_path)
            yield abs_path, rel_path, size


def walk_repo_with_cap(
    repo_path: str,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> RepoWalk:
    """Walk the repo and return whether the cap was hit.

    Callers (e.g. ``analyze_repo``) translate ``capped=True`` into
    an HTTP 413 response. ``analyze_codebase`` raises its own
    ``ValidationError`` from inside the loop.
    """
    total = 0
    count = 0
    capped = False
    for root, _, filenames in os.walk(repo_path, followlinks=False):
        for filename in filenames:
            abs_path = os.path.join(root, filename)
            size = _safe_getsize(abs_path)
            if total + size > max_total_bytes:
                capped = True
                return RepoWalk(
                    total_bytes=total,
                    capped=True,
                    file_count=count,
                )
            total += size
            count += 1
    return RepoWalk(
        total_bytes=total,
        capped=capped,
        file_count=count,
    )


def check_repo_size(
    repo_path: str,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> int:
    """Return total bytes under ``repo_path`` and stop walking once
    the ``max_total_bytes`` cap is hit.
    """
    total = 0
    for _abs, _rel, size in iter_repo_files(repo_path, max_total_bytes):
        total += size
    return total
