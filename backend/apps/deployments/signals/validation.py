import ipaddress
import logging
import re

from django.core.exceptions import ValidationError
from django.db.models.signals import pre_save
from django.dispatch import receiver

from ..models import ManagedServer
from ..models.backup import BackupSchedule
from ..models.cron import CronJob
from ..models.storage import Volume

logger = logging.getLogger(__name__)


_VOLUME_MOUNT_PATH_ALLOWED_PREFIXES = (
    "/var/lib/smsly/volumes/",
    "/data/",
    "/opt/smsly/data/",
    "/srv/",
    "/storage/",
    "/workspace/",
    "/home/smsly/",
    "/mnt/",
    "/opt/app/",
)


@receiver(pre_save, sender=Volume)
def validate_volume_name_pre_save(sender, instance, **kwargs):
    """SECURITY (Issue 140): defence-in-depth for Volume.name.

    The serializer runs ``_validate_volume_name`` first, but admin
    scripts or direct ORM writes can bypass it.  The model-level
    ``clean()`` is not invoked automatically by ``save()``, so we
    attach a ``pre_save`` signal that enforces the same
    ``^[a-zA-Z0-9_-]{1,64}$`` regex.
    """
    if instance.name is None:
        return
    name = str(instance.name)
    if not Volume._VOLUME_NAME_RE.match(name):
        raise ValidationError({
            "name": (
                "name must match ^[a-zA-Z0-9_-]{1,64}$ "
                "(letters, digits, underscore or hyphen; max 64 chars)."
            )
        })


@receiver(pre_save, sender=Volume)
def validate_volume_mount_path_pre_save(sender, instance, **kwargs):
    mount = getattr(instance, "mount_path", None)
    if mount is None:
        return
    if not isinstance(mount, str) or not mount:
        raise ValidationError({"mount_path": "mount_path is required."})
    if not any(mount == prefix.rstrip("/") or mount.startswith(prefix)
               for prefix in _VOLUME_MOUNT_PATH_ALLOWED_PREFIXES):
        raise ValidationError({
            "mount_path": (
                "mount_path must start with one of "
                f"{', '.join(_VOLUME_MOUNT_PATH_ALLOWED_PREFIXES)}."
            )
        })


_MANAGED_SERVER_HOST_RE = re.compile(r"^[a-zA-Z0-9.\-]+$")

_RFC1918_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]


def _is_private_or_internal_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    return bool(any(ip in net for net in _RFC1918_RANGES))


@receiver(pre_save, sender=ManagedServer)
def validate_managed_server_host_pre_save(sender, instance, **kwargs):
    del sender
    host = getattr(instance, "host", None)
    if host is None:
        return
    if not isinstance(host, str) or not host:
        raise ValidationError({"host": "host is required."})
    if not _MANAGED_SERVER_HOST_RE.match(host):
        raise ValidationError({
            "host": (
                "host must match ^[a-zA-Z0-9.-]+$ (letters, digits, "
                "dot and dash only)."
            )
        })
    if _is_private_or_internal_ip(host):
        raise ValidationError({
            "host": (
                f"host {host!r} is a loopback, link-local, RFC1918, "
                "multicast, or unspecified address."
            )
        })


_CRON_FIELD_RE = re.compile(r"^[\d*/,\-\s]+$")
_CRON_FIELD_COUNT = 5
_CRON_MIN_GAP_SECONDS = 300


def _parse_cron_field(field: str, lo: int, hi: int) -> list[int]:
    parts: list[int] = []
    for piece in field.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if piece == "*":
            return list(range(lo, hi + 1))
        if piece.startswith("*/"):
            step_str = piece[2:]
            if not step_str.isdigit():
                raise ValueError(f"invalid step in {field!r}")
            step = int(step_str)
            if step <= 0:
                raise ValueError(f"step must be positive in {field!r}")
            return list(range(lo, hi + 1, step))
        if "-" in piece:
            lo_s, hi_s = piece.split("-", 1)
            if not (lo_s.isdigit() and hi_s.isdigit()):
                raise ValueError(f"invalid range in {field!r}")
            a, b = int(lo_s), int(hi_s)
            if a > b:
                a, b = b, a
            parts.extend(range(a, b + 1))
            continue
        if not piece.isdigit():
            raise ValueError(f"invalid token in {field!r}")
        parts.append(int(piece))
    return parts


def _smallest_gap(values: list[int], modulus: int) -> int:
    if not values:
        return modulus
    sorted_vals = sorted(set(values))
    gaps = []
    for idx, val in enumerate(sorted_vals):
        nxt = sorted_vals[(idx + 1) % len(sorted_vals)]
        gap = (nxt - val) % modulus
        if gap == 0:
            gap = modulus
        gaps.append(gap)
    return min(gaps)


def _cron_minute_gap(minute_field: str) -> int:
    minutes = _parse_cron_field(minute_field, 0, 59)
    return _smallest_gap(minutes, 60) * 60


@receiver(pre_save, sender=CronJob)
def validate_cron_schedule_pre_save(sender, instance, **kwargs):
    schedule = getattr(instance, "schedule", None)
    if schedule is None or not isinstance(schedule, str):
        return
    schedule = schedule.strip()
    if not schedule:
        raise ValidationError({"schedule": "schedule is required."})
    fields = schedule.split()
    if len(fields) != _CRON_FIELD_COUNT:
        raise ValidationError({
            "schedule": (
                f"cron expression must have exactly {_CRON_FIELD_COUNT} fields; "
                f"got {len(fields)}."
            )
        })
    for field in fields:
        if not _CRON_FIELD_RE.match(field):
            raise ValidationError({
                "schedule": (
                    f"invalid characters in cron field {field!r}; "
                    "only digits, '*', '/', ',', '-' and whitespace are allowed."
                )
            })
    try:
        minute_gap = _cron_minute_gap(fields[0])
    except ValueError as exc:
        raise ValidationError({"schedule": f"invalid minute field: {exc}"})
    if minute_gap < _CRON_MIN_GAP_SECONDS:
        raise ValidationError({
            "schedule": (
                f"schedule fires more often than every "
                f"{_CRON_MIN_GAP_SECONDS // 60} minutes "
                f"(minimum gap detected: {minute_gap}s)."
            )
        })


@receiver(pre_save, sender=BackupSchedule)
def validate_backup_schedule_cron_pre_save(sender, instance, **kwargs):
    """Validate BackupSchedule.cron_expression — same rules as CronJob.schedule."""
    cron_expr = getattr(instance, "cron_expression", None)
    if cron_expr is None or not isinstance(cron_expr, str):
        return
    cron_expr = cron_expr.strip()
    if not cron_expr:
        raise ValidationError({"cron_expression": "cron_expression is required."})
    fields = cron_expr.split()
    if len(fields) != _CRON_FIELD_COUNT:
        raise ValidationError({
            "cron_expression": (
                f"cron expression must have exactly {_CRON_FIELD_COUNT} fields; "
                f"got {len(fields)}."
            )
        })
    for field in fields:
        if not _CRON_FIELD_RE.match(field):
            raise ValidationError({
                "cron_expression": (
                    f"invalid characters in cron field {field!r}; "
                    "only digits, '*', '/', ',', '-' and whitespace are allowed."
                )
            })
