"""Node execution API — runs locally on every node, called by master via RemoteOrchestrator."""
import logging
import re

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

logger = logging.getLogger(__name__)

_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _scrub_error(exc: object, limit: int = 200) -> str:
    """Redact credential-like material from error text (fail-closed logging)."""
    import re

    text = str(exc or "")[:limit]
    return re.sub(
        r"(?i)((?:authorization|api[_-]?key|token|secret|password|access[_-]?key)\s*[:=]\s*)[^\s,;}{]+",
        r"\1***",
        text,
    )


def _verify_node_caller(request, container_name: str | None = None, require_privileged: bool = False):
    """Fail-closed caller check for node-local APIs.

    Privilege tiers (highest first):
    - inter-server HMAC / node token exchange -> full trust (privileged).
    - staff / superuser session -> full trust (privileged).
    - service owner -> logs/stats ONLY, and ONLY for containers belonging
      to services they own. All other endpoints require privileged.
    """
    from apps.deployments.models.servers import ManagedServer
    from apps.deployments.services.agent_registrar_auth import verify_agent_hmac

    server_id = request.headers.get("X-Node-Server-Id", "")
    server = None
    if server_id:
        try:
            server = ManagedServer.objects.filter(id=server_id).first()
        except Exception:
            server = None
    if server is not None:
        if verify_agent_hmac(request, server):
            return True, None
    else:
        try:
            from apps.core.models.api_token import RemoteSyncHMACAuthentication
            auth = RemoteSyncHMACAuthentication()
            result = auth.authenticate(request)
            if result is not None:
                return True, None
        except Exception:
            pass
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
            return True, None
        if require_privileged or not container_name:
            return False, Response({"error": "Unauthorized node caller."}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            from apps.deployments.models import Service
            owned = Service.objects.filter(owner=user).only("id", "name")
            allowed: set[str] = set()
            for svc in owned:
                if svc.name:
                    allowed.add(svc.name)
            try:
                from apps.autoscaler.models.replica import ServiceReplica
                for replica_name in ServiceReplica.objects.filter(
                    service__owner=user,
                ).exclude(status="DESTROYED").values_list("container_name", flat=True)[:200]:
                    if replica_name:
                        allowed.add(str(replica_name))
            except Exception:
                pass
            header_service_id = request.headers.get("X-Node-Service-Id", "").strip()
            if header_service_id:
                if not owned.filter(id=header_service_id).exists():
                    return False, Response({"error": "Unauthorized node caller."}, status=status.HTTP_401_UNAUTHORIZED)
                try:
                    header_svc = owned.get(id=header_service_id)
                    scoped = {header_svc.name}
                    try:
                        from apps.autoscaler.models.replica import ServiceReplica
                        for replica_name in ServiceReplica.objects.filter(
                            service=header_svc,
                        ).exclude(status="DESTROYED").values_list("container_name", flat=True)[:200]:
                            if replica_name:
                                scoped.add(str(replica_name))
                    except Exception:
                        pass
                    allowed = scoped
                except Exception:
                    pass
            if container_name in allowed:
                return True, None
        except Exception:
            pass
    return False, Response({"error": "Unauthorized node caller."}, status=status.HTTP_401_UNAUTHORIZED)


def _resolve_container(client, name: str):
    try:
        return client.containers.get(name)
    except Exception:
        pass
    try:
        matches = client.containers.list(filters={"name": name})
        for c in matches:
            if getattr(c, "name", "") == name:
                return c
        if matches:
            return matches[0]
    except Exception:
        pass
    return None


class NodeExecViewSet(viewsets.ViewSet):
    @action(detail=False, methods=["get"], url_path=r"containers/(?P<name>[^/]+)/logs")
    def container_logs(self, request, name=None):
        if not name or not _CONTAINER_RE.match(name):
            return Response({"error": "Invalid container name."}, status=status.HTTP_400_BAD_REQUEST)
        ok, err = _verify_node_caller(request, container_name=name)
        if not ok:
            return err
        try:
            tail = max(1, min(int(request.query_params.get("tail", 200)), 1000))
        except (TypeError, ValueError):
            tail = 200
        try:
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            container = _resolve_container(client, name)
            if container is None:
                return Response({"logs": "", "status": "not-found", "message": f"Container {name} not found on this node."})
            raw = container.logs(stdout=True, stderr=True, tail=tail, timestamps=True)
            return Response({
                "logs": raw.decode("utf-8", errors="replace"),
                "status": getattr(container, "status", "unknown"),
                "container_id": getattr(container, "short_id", ""),
            })
        except Exception as exc:
            logger.debug("Node container logs failed for %s: %s", name, exc)
            return Response({"logs": "", "status": "error", "message": _scrub_error(exc, 300)})

    @action(detail=False, methods=["get"], url_path=r"containers/(?P<name>[^/]+)/stats")
    def container_stats(self, request, name=None):
        if not name or not _CONTAINER_RE.match(name):
            return Response({"error": "Invalid container name."}, status=status.HTTP_400_BAD_REQUEST)
        ok, err = _verify_node_caller(request, container_name=name)
        if not ok:
            return err
        try:
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            container = _resolve_container(client, name)
            if container is None:
                return Response({"error": f"Container {name} not found."}, status=status.HTTP_404_NOT_FOUND)
            stats = container.stats(stream=False)
            cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats["precpu_stats"]["cpu_usage"]["total_usage"]
            system_delta = stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
            num_cpus = stats["cpu_stats"].get("online_cpus", 1)
            cpu_cores_used = round((cpu_delta / system_delta) * num_cpus, 4) if system_delta > 0 else 0.0
            mem_usage_mb = stats["memory_stats"].get("usage", 0) // (1024 * 1024)
            mem_limit_mb = stats["memory_stats"].get("limit", 0) // (1024 * 1024)
            networks = stats.get("networks", {})
            rx = sum(n.get("rx_bytes", 0) for n in networks.values())
            tx = sum(n.get("tx_bytes", 0) for n in networks.values())
            blkio = stats.get("blkio_stats", {}).get("io_service_bytes_recursive", []) or []
            rd = sum(e["value"] for e in blkio if e.get("op") == "read")
            wr = sum(e["value"] for e in blkio if e.get("op") == "write")
            return Response({
                "cpu_usage": cpu_cores_used,
                "cpu_limit": float(num_cpus),
                "memory_usage": mem_usage_mb,
                "memory_limit": mem_limit_mb if mem_limit_mb > 0 else 512,
                "network_rx_bytes": rx,
                "network_tx_bytes": tx,
                "disk_read_bytes": rd,
                "disk_write_bytes": wr,
                "container_id": getattr(container, "short_id", ""),
                "status": getattr(container, "status", "unknown"),
            })
        except Exception as exc:
            logger.debug("Node container stats failed for %s: %s", name, exc)
            return Response({"error": _scrub_error(exc, 300)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["get"], url_path="storage-overview")
    def storage_overview(self, request):
        ok, err = _verify_node_caller(request, require_privileged=True)
        if not ok:
            return err
        from apps.core.views.system import build_storage_overview
        return Response(build_storage_overview())

    @action(detail=False, methods=["get"], url_path="volumes")
    def volumes(self, request):
        ok, err = _verify_node_caller(request, require_privileged=True)
        if not ok:
            return err
        try:
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            out = []
            for v in client.volumes.list():
                attrs = getattr(v, "attrs", {}) or {}
                usage = attrs.get("UsageData") or {}
                out.append({
                    "name": getattr(v, "name", ""),
                    "driver": attrs.get("Driver", ""),
                    "mountpoint": attrs.get("Mountpoint", ""),
                    "size_bytes": usage.get("Size", 0),
                    "ref_count": usage.get("RefCount", 0),
                    "labels": attrs.get("Labels") or {},
                })
            return Response({"volumes": out})
        except Exception as exc:
            return Response({"error": _scrub_error(exc, 300)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["get"], url_path="access-log-tail")
    def access_log_tail(self, request):
        ok, err = _verify_node_caller(request, require_privileged=True)
        if not ok:
            return err
        try:
            lines = max(1, min(int(request.query_params.get("lines", 500)), 2000))
        except (TypeError, ValueError):
            lines = 500
        from pathlib import Path
        entries = []
        for candidate in ("/var/log/caddy/access.log", "/var/log/traefik/access.log"):
            try:
                p = Path(candidate)
                if not p.exists():
                    continue
                with p.open("rb") as fh:
                    fh.seek(0, 2)
                    size = fh.tell()
                    fh.seek(max(0, size - 512 * 1024))
                    tail_text = fh.read().decode("utf-8", errors="replace")
                for line in tail_text.splitlines()[-lines:]:
                    entries.append({"source": candidate, "line": line})
            except Exception as exc:
                logger.debug("Node access log tail failed for %s: %s", candidate, exc)
        return Response({"entries": entries[-lines:]})

    @action(detail=False, methods=["post"], url_path="network/ensure")
    def network_ensure(self, request):
        ok, err = _verify_node_caller(request, require_privileged=True)
        if not ok:
            return err
        data = request.data if isinstance(request.data, dict) else {}
        network_name = str(data.get("network_name", "") or "").strip()
        egress = data.get("egress") or ["0.0.0.0/0"]
        if not network_name or not _CONTAINER_RE.match(network_name):
            return Response({"error": "network_name is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            from apps.deployments.services.network_scope import apply_egress_restrictions, ensure_scoped_network
            ensure_scoped_network({"name": network_name})
            apply_egress_restrictions(network_name, list(egress))
            return Response({"ok": True, "network": network_name})
        except Exception as exc:
            return Response({"error": _scrub_error(exc, 300)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="network/reconcile")
    def network_reconcile(self, request):
        ok, err = _verify_node_caller(request, require_privileged=True)
        if not ok:
            return err
        try:
            from apps.deployments.services.network_scope import reconcile_network_isolation
            reconcile_network_isolation()
            return Response({"ok": True})
        except Exception as exc:
            return Response({"error": _scrub_error(exc, 300)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="mtls/ensure")
    def mtls_ensure(self, request):
        ok, err = _verify_node_caller(request, require_privileged=True)
        if not ok:
            return err
        data = request.data if isinstance(request.data, dict) else {}
        service_id = str(data.get("service_id", "") or "").strip()
        service_name = str(data.get("service_name", "") or "").strip()
        try:
            from apps.deployments.models import Service
            service = None
            if service_id:
                service = Service.objects.filter(id=service_id).first()
            if service is None and service_name:
                service = Service.objects.filter(name=service_name).first()
            if service is None:
                return Response({"error": "Service not found on this node. Sync the service first."}, status=status.HTTP_404_NOT_FOUND)
            from apps.mtls.services.envoy_sidecar import EnvoySidecar
            try:
                status_info = EnvoySidecar.get_sidecar_status(service)
            except Exception:
                status_info = {"running": False}
            if not (status_info or {}).get("running"):
                EnvoySidecar.inject_sidecar(service)
            else:
                try:
                    EnvoySidecar.remount_if_stale(service)
                except Exception:
                    pass
            try:
                final_status = EnvoySidecar.get_sidecar_status(service)
            except Exception:
                final_status = {"running": False}
            return Response({"ok": True, "status": final_status})
        except Exception as exc:
            logger.warning("Node mTLS ensure failed: %s", exc)
            return Response({"error": _scrub_error(exc, 300)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="metrics/batch")
    def metrics_batch(self, request):
        ok, err = _verify_node_caller(request, require_privileged=True)
        if not ok:
            return err
        data = request.data if isinstance(request.data, dict) else {}
        samples = data.get("samples") or []
        if not isinstance(samples, list):
            return Response({"error": "samples must be a list."}, status=status.HTTP_400_BAD_REQUEST)
        from django.utils import timezone
        from apps.autoscaler.models.metrics import ServiceMetric
        from apps.deployments.models import Service
        now = timezone.now()
        stored = 0
        for sample in samples[:200]:
            if not isinstance(sample, dict):
                continue
            service_name = str(sample.get("service_name", "") or "")
            svc = Service.objects.filter(name=service_name).first()
            if svc is None:
                continue
            try:
                ServiceMetric.objects.create(
                    service=svc,
                    timestamp=now,
                    cpu_usage=float(sample.get("cpu_usage", 0) or 0),
                    cpu_limit=float(sample.get("cpu_limit", 1) or 1),
                    memory_usage=int(sample.get("memory_usage", 0) or 0),
                    memory_limit=int(sample.get("memory_limit", 512) or 512),
                    network_rx_bytes=int(sample.get("network_rx_bytes", 0) or 0),
                    network_tx_bytes=int(sample.get("network_tx_bytes", 0) or 0),
                    disk_read_bytes=int(sample.get("disk_read_bytes", 0) or 0),
                    disk_write_bytes=int(sample.get("disk_write_bytes", 0) or 0),
                )
                stored += 1
            except Exception:
                continue
        return Response({"ok": True, "stored": stored})

    @action(detail=False, methods=["post"], url_path="storage/test")
    def storage_test(self, request):
        # Privileged-only: payload carries cloud credentials. Transport is the
        # authenticated orchestrator channel (token/HMAC + TLS verify in
        # RemoteClientMixin); never log the payload here — only scrubbed errors.
        ok, err = _verify_node_caller(request, require_privileged=True)
        if not ok:
            return err
        data = request.data if isinstance(request.data, dict) else {}
        try:
            from apps.cloud.models.cloud_storage import CloudStorageDestination
            dest = CloudStorageDestination(
                provider=str(data.get("provider", "s3") or "s3"),
                bucket=str(data.get("bucket", "") or ""),
                region=str(data.get("region", "") or ""),
                endpoint=str(data.get("endpoint", "") or ""),
                access_key=str(data.get("access_key", "") or ""),
                secret_key=str(data.get("secret_key", "") or ""),
            )
            result = dest.upload_test_file()
            return Response({"ok": True, "result": result})
        except Exception as exc:
            logger.debug("Node storage test failed: %s", _scrub_error(exc, 120))
            return Response({"error": _scrub_error(exc, 200)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
