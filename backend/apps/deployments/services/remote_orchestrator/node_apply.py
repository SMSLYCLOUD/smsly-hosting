"""Node-side apply helpers for master-shipped deploy payload state.

The remote deploy trigger ships mesh_env / addons / volumes / SOPS key
material that the node (which owns none of those rows) applies to its
local DB before enqueueing the pipeline. Update-only, capped, validated,
fail-open individually — a bad entry never blocks the deploy.
"""
import logging
import re
import uuid

logger = logging.getLogger(__name__)

_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def apply_mesh_env(service, mesh_env) -> int:
    """Upsert master-rewritten env vars. Returns applied count."""
    applied = 0
    try:
        if not isinstance(mesh_env, dict) or not mesh_env:
            return 0
        from apps.deployments.models import EnvironmentVariable
        for key, val in list(mesh_env.items())[:200]:
            name = str(key or "").strip()[:255]
            if not name or not isinstance(val, str):
                continue
            value = val[:10000]
            row, created = EnvironmentVariable.objects.get_or_create(
                service=service, key=name,
                defaults={"value": value},
            )
            if not created and row.value != value:
                row.value = value
                row.save(update_fields=["value", "updated_at"])
                applied += 1
            elif created:
                applied += 1
    except Exception as exc:
        logger.debug("mesh_env apply skipped: %s", exc)
    return applied


def apply_mesh_addons(service, items) -> int:
    """Upsert master-shipped addon rows as mesh-backed. Returns count.

    Items flagged ``local`` describe backends already living on the
    service's node: they apply as ordinary (non-mesh) rows so the node
    pipeline gates them as local containers instead of mesh endpoints.

    Existing LOCAL (non-mesh) rows with the same name are left untouched —
    an operator's working local addon always wins.
    """
    applied = 0
    try:
        if not isinstance(items, list) or not items:
            return 0
        from apps.deployments.models.addons import Addon
        valid_types = {c for c, _ in Addon.Type.choices}
        for item in items[:50]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()[:255]
            atype = str(item.get("addon_type") or "").strip()[:20]
            url = item.get("connection_url")
            if not name or atype not in valid_types:
                continue
            if not isinstance(url, str) or not url.strip():
                continue
            _local = bool(item.get("local"))
            master_uuid = None
            raw_id = item.get("id")
            if raw_id:
                try:
                    master_uuid = uuid.UUID(str(raw_id).strip())
                except (ValueError, TypeError, AttributeError):
                    master_uuid = None
            synced_cname = ""
            if _local:
                raw_cname = str(item.get("container_name") or "").strip()[:128]
                if raw_cname and _CONTAINER_NAME_RE.match(raw_cname):
                    synced_cname = raw_cname
                elif master_uuid is not None:
                    synced_cname = f"smsly-addon-{atype.lower()}-{master_uuid}"
            existing = Addon.objects.filter(
                service=service, name=name,
            ).exclude(status="DELETED").first()
            if existing is not None:
                try:
                    meta = dict(getattr(existing, "provider_metadata", None) or {})
                except Exception:
                    meta = {}
                if not (meta.get("mesh_backed") or meta.get("master_synced")) and not _local:
                    continue
                existing.addon_type = atype
                existing.connection_url = url[:512]
                existing.status = "ACTIVE"
                if _local:
                    meta.pop("mesh_backed", None)
                    meta.pop("mesh_forward_port", None)
                    meta["master_synced"] = True
                    if synced_cname:
                        meta["container_name"] = synced_cname
                else:
                    meta.pop("container_name", None)
                    meta.pop("master_synced", None)
                    meta["mesh_backed"] = True
                    fport = item.get("mesh_forward_port")
                    if fport:
                        try:
                            meta["mesh_forward_port"] = int(fport)
                        except (TypeError, ValueError):
                            pass
                existing.provider_metadata = meta
                existing.save(update_fields=[
                    "addon_type", "connection_url", "status",
                    "provider_metadata", "updated_at",
                ])
                applied += 1
                continue
            if _local:
                _meta = {"master_synced": True}
                if synced_cname:
                    _meta["container_name"] = synced_cname
            else:
                _meta = {"mesh_backed": True, "mesh_forward_port": item.get("mesh_forward_port")}
            create_kwargs = {
                "service": service,
                "project": getattr(service, "project", None),
                "name": name,
                "addon_type": atype,
                "connection_url": url[:512],
                "status": "ACTIVE",
                "provider_metadata": _meta,
            }
            if _local and master_uuid is not None and not Addon.objects.filter(id=master_uuid).exists():
                create_kwargs["id"] = master_uuid
            Addon.objects.create(**create_kwargs)
            applied += 1
    except Exception as exc:
        logger.debug("mesh addon apply skipped: %s", exc)
    return applied


def apply_volumes(service, items) -> int:
    """Create missing volume definitions. Never modifies existing rows
    (operator-managed win) and never transfers data — fresh empty
    docker volumes are created by the pipeline. Returns created count.
    """
    created = 0
    try:
        if not isinstance(items, list) or not items:
            return 0
        from apps.deployments.models.storage import Volume
        for item in items[:20]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()[:255]
            path = str(item.get("mount_path") or "").strip()[:255]
            try:
                size = int(item.get("size_gb") or 0)
            except (TypeError, ValueError):
                continue
            if not name or not path:
                continue
            if Volume.objects.filter(service=service, name=name).exists():
                continue
            row = Volume(service=service, name=name, mount_path=path, size_gb=size)
            try:
                row.full_clean(exclude=["service"])
                row.save()
                created += 1
            except Exception:
                continue
    except Exception as exc:
        logger.debug("volume apply skipped: %s", exc)
    return created


def apply_sops_bundle(service, bundle) -> bool:
    """Store the master-shipped SOPS bundle for local verify/decrypt.

    Thin wrapper over secrets_sops.store_service_bundle with the
    node_apply fail-open contract (never raises).
    """
    try:
        from apps.deployments.services.secrets_sops import store_service_bundle
        return bool(store_service_bundle(service, bundle))
    except Exception as exc:
        logger.debug("sops bundle apply skipped: %s", exc)
        return False
