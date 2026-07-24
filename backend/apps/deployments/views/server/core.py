"""
ManagedServerViewSet — CRUD for managed remote servers, plus health check, proxy, and provisioning.
"""

import logging

import requests
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ...models.core import Deployment, Service
from ...models.servers import ManagedServer
from apps.core.rate_limiting import ServerHealthCheckRateThrottle
from ...serializers import DeploymentSerializer, ServiceSerializer
from .helpers import (
    _append_log_safe,
    _build_remote_headers,
    _extract_page_results_and_next,
    _fetch_remote_json_with_fallback,
    _is_command_allowed,
    _lite_agent_proxy_response,
    _normalize_remote_api_path,
    _proxy_error_response,
    _redact_transfer_text,
    _refresh_managed_server_health,
    _safe_remote_error_payload,
    _truncate_dict,
)
from .serializers import (
    ALLOWED_PROXY_METHODS,
    ALLOWED_PROXY_PATHS,
    ManagedServerCreateSerializer,
    ManagedServerProvisionSerializer,
    ManagedServerSerializer,
    ServerCheckAllThrottle,
    ServerCommandThrottle,
    ServerHealThrottle,
    ServerProvisionThrottle,
    ServerProxyThrottle,
)

logger = logging.getLogger(__name__)
class ManagedServerViewSet(viewsets.ModelViewSet):
    """CRUD for managed remote servers, plus health check, proxy, and provisioning."""

    queryset = ManagedServer.objects.all()
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        """Per-action throttles: prefer the bound action's ``throttle_classes``
        when present, otherwise fall back to the view's class-level setting.
        """
        action_attr = getattr(self, self.action, None) if self.action else None
        action_throttles = getattr(action_attr, "throttle_classes", None)
        if action_throttles:
            return [throttle() for throttle in action_throttles]
        return super().get_throttles()

    def get_queryset(self):
        qs = self.queryset.filter(owner=self.request.user)
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    def _get_object_for_agent(self, pk):
        """Look up a server by PK without owner filtering.

        Used by HMAC-authenticated agent endpoints where
        request.user is AnonymousUser. The HMAC check in the
        caller verifies authenticity before any data is returned.
        """
        try:
            return ManagedServer.objects.get(pk=pk)
        except ManagedServer.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound

    def get_serializer_class(self):
        if self.action == "provision_new":
            return ManagedServerProvisionSerializer
        if self.action in ["create", "update", "partial_update"]:
            return ManagedServerCreateSerializer
        return ManagedServerSerializer

    def perform_create(self, serializer):
        server = serializer.save(owner=self.request.user)
        self._start_server_health_sync(server)

    def perform_update(self, serializer):
        server = serializer.save()
        self._start_server_health_sync(server)

    def _start_server_health_sync(self, server):
        """Start background health/auth/mesh repair for a connected server."""
        has_connection_hint = bool(
            server.api_url
            or server.api_token
            or server.gateway_secret
            or server.ssh_key
            or server.ssh_password
        )
        if server.host and has_connection_hint:
            from threading import Thread
            Thread(target=self._sync_server_health, args=(server.id,), daemon=True).start()

    def _sync_server_health(self, server_id):
        """Background worker to check a server's health and service count upon connection."""
        try:
            server = ManagedServer.objects.get(id=server_id)
            _refresh_managed_server_health(server)
        except Exception as e:
            logger.warning(f"Background server sync failed for {server_id}: {e}")

    # ── Provision New Server ─────────────────────────────────────────────

    @action(detail=False, methods=["post"], url_path="provision")
    @throttle_classes([ServerProvisionThrottle])
    def provision_new(self, request):
        """
        Create a server record and kick off auto-provisioning via SSH.
        The installer will run on the remote VPS and auto-fill api_url/api_token.

        SECURITY: marking a server as ``is_primary=True`` displaces the
        platform's existing primary control plane. Any non-superuser
        with valid SSH credentials could previously register a
        competing primary and seize traffic for their workloads.
        Only superusers may set the flag.
        """
        is_primary_raw = request.data.get("is_primary", False)
        if isinstance(is_primary_raw, str):
            is_primary_requested = is_primary_raw.strip().lower() in (
                "true", "1", "yes", "t", "on",
            )
        else:
            is_primary_requested = bool(is_primary_raw)
        if is_primary_requested and not request.user.is_superuser:
            return Response(
                {"error": "Only superusers can provision a primary server."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ManagedServerProvisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Remove the non-model field before saving
        validated = serializer.validated_data.copy()
        validated.pop("ssh_auth_method", None)
        validated.pop("node_certificate", None)

        server = ManagedServer.objects.create(
            owner=request.user,
            provision_status=ManagedServer.ProvisionStatus.PENDING,
            **validated,
        )
        if server.is_primary and server.allow_user_workloads:
            server.allow_user_workloads = False
            server.save(update_fields=["allow_user_workloads", "updated_at"])

        # Kick off async provisioning
        from .services.provisioner import provision_server
        provision_server.delay(str(server.id))

        return Response(
            ManagedServerSerializer(server).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["post"], url_path="provision-batch")
    @throttle_classes([ServerProvisionThrottle])
    def provision_batch(self, request):
        """Provision multiple lite agent servers in parallel.

        Accepts {"servers": [...]} where each item has the same fields as
        the single provision endpoint (name, host, ssh_password, etc.).

        Returns 202 with a list of created server records. Each server's
        provisioning runs as an independent Celery task.
        """
        servers_data = request.data.get("servers")
        if not servers_data or not isinstance(servers_data, list):
            return Response(
                {"error": "'servers' must be a non-empty list."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(servers_data) > 20:
            return Response(
                {"error": "Maximum 20 servers per batch."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = []
        errors = []
        from .services.provisioner import provision_server

        for idx, item in enumerate(servers_data):
            serializer = ManagedServerProvisionSerializer(data=item)
            if not serializer.is_valid():
                errors.append({"index": idx, "host": item.get("host", ""), "errors": serializer.errors})
                continue

            validated = serializer.validated_data.copy()
            validated.pop("ssh_auth_method", None)
            validated.pop("node_certificate", None)

            # Force lite agent for batch provisioning
            validated["is_lite_agent"] = True
            validated["is_primary"] = False

            server = ManagedServer.objects.create(
                owner=request.user,
                provision_status=ManagedServer.ProvisionStatus.PENDING,
                **validated,
            )
            provision_server.delay(str(server.id))
            created.append(ManagedServerSerializer(server).data)

        return Response(
            {"created": created, "errors": errors, "total": len(created)},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["get"], url_path="provision-logs")
    def provision_logs(self, request, pk=None):
        """Get the provisioning logs for a server."""
        server = self.get_object()
        return Response({
            "provision_status": server.provision_status,
            "provision_logs": server.provision_logs,
        })

    @action(detail=True, methods=["post"], url_path="retry-provision")
    @throttle_classes([ServerProvisionThrottle])
    def retry_provision(self, request, pk=None):
        """Retry the provisioning process for an existing server."""
        server = self.get_object()

        # Reset status and clear logs
        server.provision_status = ManagedServer.ProvisionStatus.PENDING
        server.provision_logs = f"--- Retry started by {request.user.username} at {timezone.now()} ---\n"
        server.save(update_fields=["provision_status", "provision_logs", "updated_at"])

        # Kick off async provisioning
        from .services.provisioner import provision_server
        provision_server.delay(str(server.id))

        return Response(
            ManagedServerSerializer(server).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="update-server")
    @throttle_classes([ServerProvisionThrottle])
    def update_server(self, request, pk=None):
        """
        Trigger a remote update on a managed server.

        Uses the same idempotent provision flow (install.sh) which handles
        both fresh installs and updates without clearing existing data/volumes.
        """
        server = self.get_object()

        blocked_statuses = {
            ManagedServer.ProvisionStatus.PENDING,
            ManagedServer.ProvisionStatus.PROVISIONING,
            ManagedServer.ProvisionStatus.UPDATING,
        }
        if server.provision_status in blocked_statuses:
            logger.warning(
                "update_server: auto-clearing in-flight provision_status=%s for server %s (user=%s)",
                server.provision_status, server.id, request.user.id,
            )
            server.provision_status = ManagedServer.ProvisionStatus.DONE
            server.save(update_fields=["provision_status", "updated_at"])

        if not (server.ssh_key or server.ssh_password):
             return Response(
                 {"error": "Server has no SSH credentials configured for updates."},
                 status=status.HTTP_400_BAD_REQUEST,
             )

        # Reset status and set log header for update
        server.provision_status = ManagedServer.ProvisionStatus.PENDING
        server.provision_logs = f"--- Update started by {request.user.username} at {timezone.now()} ---\n"
        server.save(update_fields=["provision_status", "provision_logs", "updated_at"])

        # Use the same provision task (idempotent), but skip post-install reboot
        from .services.provisioner import provision_server
        provision_server.delay(str(server.id), skip_reboot=True)

        return Response(
            ManagedServerSerializer(server).data,
            status=status.HTTP_202_ACCEPTED,
        )

    # ── Agent Self-Registration (lite-agent registrar) ──────────────────
    #
    # The lite agent's registrar (a small service running inside the
    # agent's docker-compose stack) calls these endpoints to tell the
    # master "I am booted and ready" (one-shot) and "I am still alive"
    # (every 30s). Both are HMAC-authenticated with the
    # server.gateway_secret that the master provisioned into the
    # agent's .env during install. They never require an admin
    # session — that way the agent can self-register before the
    # operator has logged in to the platform.

    @action(
        detail=True,
        methods=["post"],
        url_path="agent-ready",
        permission_classes=[],
        authentication_classes=[],
        throttle_classes=[],
    )
    def agent_ready(self, request, pk=None):
        """
        Mark this node as ``agent_ready=True`` and stamp its runtime
        info from the agent's first successful boot.

        The agent-registrar service on the lite node calls this once
        it has finished installing and confirmed all of its own
        containers are healthy. Authentication is via the
        per-server ``gateway_secret`` HMAC (not a user session) so
        the agent can register itself before any operator has
        logged in to the platform.
        """
        from apps.deployments.services.agent_registrar_auth import (
            verify_agent_hmac,
        )

        server = self._get_object_for_agent(pk)
        if not verify_agent_hmac(request, server):
            return Response(
                {"error": "Invalid or missing HMAC signature."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        runtime_info = {}
        if isinstance(request.data, dict):
            runtime_info = request.data.get("runtime_info") or {}
            if not isinstance(runtime_info, dict):
                runtime_info = {}

        update_fields = ["agent_ready", "last_agent_heartbeat_at", "updated_at"]
        server.agent_ready = True
        server.last_agent_heartbeat_at = timezone.now()
        if runtime_info:
            server.agent_runtime_info = runtime_info
            update_fields.append("agent_runtime_info")
        # If the agent reports it is ready, mark the provision as done
        if server.provision_status in {
            ManagedServer.ProvisionStatus.PENDING,
            ManagedServer.ProvisionStatus.PROVISIONING,
        }:
            server.provision_status = ManagedServer.ProvisionStatus.DONE
            update_fields.append("provision_status")
        server.save(update_fields=update_fields)

        _append_log_safe(
            server,
            f"✅ Agent ready: runtime={_truncate_dict(runtime_info)}",
        )

        return Response({
            "status": "ok",
            "agent_ready": True,
            "server_id": str(server.id),
            "node_id": runtime_info.get("node_id", ""),
            "master_time": timezone.now().isoformat(),
        })

    @action(
        detail=True,
        methods=["post"],
        url_path="agent-heartbeat",
        permission_classes=[],
        authentication_classes=[],
        throttle_classes=[],
    )
    def agent_heartbeat(self, request, pk=None):
        """
        Receive a periodic heartbeat from the agent's registrar.

        The agent posts every 30s with:

        * ``runtime_info``: docker version, image versions, host
          uptime, disk/mem (refreshed on every heartbeat)
        * ``status``: free-form health string (e.g. ``"ok"``,
          ``"degraded"``)
        * ``queues``: dict of celery queue depths (best-effort,
          empty on lite agents that don't have access to the
          master queue)
        """
        from apps.deployments.services.agent_registrar_auth import (
            verify_agent_hmac,
        )

        server = self._get_object_for_agent(pk)
        if not verify_agent_hmac(request, server):
            return Response(
                {"error": "Invalid or missing HMAC signature."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        runtime_info = {}
        if isinstance(request.data, dict):
            runtime_info = request.data.get("runtime_info") or {}
            if not isinstance(runtime_info, dict):
                runtime_info = {}

        status_payload = ""
        if isinstance(request.data, dict):
            status_payload = str(request.data.get("status", "") or "").strip()

        update_fields = [
            "last_agent_heartbeat_at",
            "agent_runtime_info",
            "agent_ready",
            "updated_at",
        ]
        server.last_agent_heartbeat_at = timezone.now()
        if runtime_info:
            server.agent_runtime_info = runtime_info
        # Heartbeats always confirm readiness — the agent is alive
        # and reporting in, even if the operator hasn't manually
        # marked the node ready. The first heartbeat implicitly
        # asserts readiness.
        if not server.agent_ready:
            server.agent_ready = True
            _append_log_safe(
                server,
                "✅ Agent ready (implicit via first heartbeat)",
            )
        server.save(update_fields=update_fields)

        # Update status if heartbeat says "degraded" or "down" so
        # operators see the agent's self-reported state in the
        # dashboard. Only override ONLINE; never auto-promote to
        # ONLINE from a heartbeat alone.
        if status_payload.lower() in {"degraded", "down", "unhealthy"}:
            if server.status == ManagedServer.Status.ONLINE:
                server.status = ManagedServer.Status.DEGRADED
                server.save(update_fields=["status", "updated_at"])

        return Response({
            "status": "ok",
            "server_id": str(server.id),
            "master_time": timezone.now().isoformat(),
        })

    # ── Health Check ─────────────────────────────────────────────────────

    @action(detail=True, methods=["post"],
            throttle_classes=[ServerHealthCheckRateThrottle])
    def health_check(self, request, pk=None):
        """Ping a remote server's API to check if it's online."""
        server = self.get_object()
        server = _refresh_managed_server_health(server)
        return Response(ManagedServerSerializer(server).data)

    @action(detail=False, methods=["post"])
    @throttle_classes([ServerCheckAllThrottle])
    def check_all(self, request):
        """Health check all servers — dispatched to Celery for parallelism."""
        from .tasks_health import refresh_managed_server_health
        servers = list(self.get_queryset())
        for server in servers:
            refresh_managed_server_health.delay(str(server.id))
        return Response(
            {"status": "scheduled", "count": len(servers)},
            status=status.HTTP_202_ACCEPTED,
        )

    # ── Proxy ────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    @throttle_classes([ServerProxyThrottle])
    def proxy(self, request, pk=None):
        """
        Forward an API request to a remote server.
        Body: { "method": "GET", "path": "/api/v1/services/", "body": null }
        """
        import json as json_mod
        import posixpath
        from urllib.parse import urlparse

        MAX_PROXY_BODY_SIZE = 1_048_576  # 1MB

        server = self.get_object()
        method = request.data.get("method", "GET").upper()
        if method not in ALLOWED_PROXY_METHODS:
            return Response(
                {"error": f"Method {method} is not allowed."},
                status=status.HTTP_405_METHOD_NOT_ALLOWED,
            )
        raw_path = str(request.data.get("path", "") or "")
        body = request.data.get("body")

        if body is not None:
            serialized = json_mod.dumps(body, sort_keys=True)
            if len(serialized.encode('utf-8')) > MAX_PROXY_BODY_SIZE:
                return Response(
                    {"error": f"Proxy body too large; max {MAX_PROXY_BODY_SIZE} bytes."},
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )

        # Preserve query params while normalizing just the path segment.
        path_part, _, query_part = raw_path.partition("?")
        normalized_path = posixpath.normpath(path_part or "/")
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        path = f"{normalized_path}?{query_part}" if query_part else normalized_path

        # Reject any path containing ".." even after normalization
        if ".." in path:
            return Response(
                {"error": "Directory traversal is not allowed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Constrain the proxy to a fixed allowlist of safe read-only platform
        # endpoints. This blocks tenant-controlled SSRF amplification via
        # the platform's API token. Compare the path portion only, ignoring
        # any query string so legitimate ?detail=1 etc. still work.
        path_only_for_match = normalized_path.rstrip("/")
        if not any(
            path_only_for_match == allowed.rstrip("/")
            or path_only_for_match.startswith(allowed.rstrip("/") + "/")
            for allowed in ALLOWED_PROXY_PATHS
        ):
            return Response(
                {"error": "Path not in proxy allowlist."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Re-verify /api/ prefix after normalization
        if not path.startswith("/api/"):
            return Response(
                {"error": "Only /api/ paths can be proxied."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lite_agent_response = _lite_agent_proxy_response(server, request, method, path)
        if lite_agent_response is not None:
            return lite_agent_response

        # Validate the server URL scheme is HTTP/HTTPS (prevent file://, etc.)
        parsed = urlparse(server.api_url)
        if parsed.scheme not in ("http", "https"):
            return Response(
                {"error": "Server API URL must use HTTP or HTTPS."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # SECURITY: refuse to forward authenticated requests to a hostname
        # that does not match the registered server.host. A tenant can
        # otherwise register api_url=http://attacker.example.com and have
        # the platform ship the gateway secret / API token straight to the
        # attacker.
        api_host = (parsed.hostname or "").strip().lower()
        server_host = (server.host or "").strip().lower()
        if not server_host:
            return Response(
                {"error": "Server host is not configured."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if api_host != server_host:
            return Response(
                {
                    "error": (
                        "api_url hostname does not match server.host; "
                        "refusing to forward authenticated proxy request."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        url = f"{server.api_url.rstrip('/')}{path}"
        body_bytes = json_mod.dumps(body, sort_keys=True).encode() if body is not None else b""
        headers = _build_remote_headers(server, method=method, path=path, body=body_bytes)

        try:
            resp = requests.request(
                method, url,
                headers=headers,
                data=body_bytes if body is not None else None,
                timeout=30,
            )
            try:
                data = resp.json()
            except ValueError:
                data = {"raw": resp.text[:2000]}

            return Response({
                "status_code": resp.status_code,
                "data": data,
            })
        except requests.RequestException as e:
            return _proxy_error_response(f"Proxy request failed: {e!s}")

    # ── Remote Services (convenience) ────────────────────────────────────

    @action(detail=True, methods=["get"])
    def services(self, request, pk=None):
        """Fetch services for a managed server.

        Lite agents share the master DB so we query locally.
        Full remote servers are proxied via their API.
        """
        server = self.get_object()

        # ── Lite agent: local DB query ──
        if server.is_lite_agent:
            qs = Service.objects.filter(
                server=server,
            ).exclude(
                status=Service.Status.DELETED,
            ).select_related('project').order_by('-updated_at')
            serializer = ServiceSerializer(qs, many=True, context={'request': request})
            return Response({'results': serializer.data, 'count': len(serializer.data)})

        # ── Full remote server: proxy ──
        if not server.api_url:
            return Response(_safe_remote_error_payload("services", "Server has no API URL yet."))

        api_path = "/api/v1/services/"
        payload, error_payload = _fetch_remote_json_with_fallback(
            server, "services", api_path, timeout=15
        )
        if error_payload:
            return Response(error_payload)
        return Response(payload)

    @action(detail=True, methods=["get"])
    def deployments(self, request, pk=None):
        """Fetch deployments for a managed server.

        Lite agents share the master DB so we query locally.
        Full remote servers are proxied via their API.
        """
        server = self.get_object()

        # ── Lite agent: local DB query ──
        if server.is_lite_agent:
            qs = Deployment.objects.filter(
                service__server=server,
            ).select_related('service').order_by('-created_at')[:50]
            serializer = DeploymentSerializer(qs, many=True)
            return Response({'results': serializer.data, 'count': len(serializer.data)})

        # ── Full remote server: proxy ──
        if not server.api_url:
            return Response(_safe_remote_error_payload("deployments", "Server has no API URL yet."))

        api_path = "/api/v1/deployments/"
        payload, error_payload = _fetch_remote_json_with_fallback(
            server, "deployments", api_path, timeout=15
        )
        if error_payload:
            return Response(error_payload)
        return Response(payload)

    @action(detail=True, methods=["get"])
    def domains(self, request, pk=None):
        """Aggregate all custom domains across all services on a managed server.

        Lite agents share the master DB so we query locally.
        Full remote servers are proxied via their API.
        """
        server = self.get_object()

        # ── Lite agent: local DB query ──
        if server.is_lite_agent:
            services_qs = Service.objects.filter(
                server=server,
            ).exclude(
                status=Service.Status.DELETED,
            ).only('id', 'name', 'public_domain', 'custom_domains', 'domain_verified', 'verification_token')

            domains = []
            for svc in services_qs:
                custom = svc.custom_domains if isinstance(svc.custom_domains, list) else []
                for domain in custom:
                    domains.append({
                        "domain": domain,
                        "service_id": str(svc.id),
                        "service_name": svc.name,
                        "public_domain": svc.public_domain or "",
                        "verified": svc.domain_verified,
                        "verification_token": svc.verification_token or "",
                    })
            return Response({"domains": domains, "count": len(domains)})

        # ── Full remote server: proxy ──
        if not server.api_url:
            return Response(_safe_remote_error_payload("domains", "Server has no API URL yet."))

        all_services = []
        seen_paths = set()
        next_path = "/api/v1/services/"
        max_pages = 50

        for _ in range(max_pages):
            normalized_path = _normalize_remote_api_path(next_path)
            if not normalized_path or normalized_path in seen_paths:
                break
            seen_paths.add(normalized_path)

            payload, error_payload = _fetch_remote_json_with_fallback(
                server, "domains", normalized_path, timeout=15
            )
            if error_payload:
                if not all_services:
                    return Response(error_payload)
                break

            services_page, next_link = _extract_page_results_and_next(payload)
            all_services.extend(services_page)
            if not next_link:
                break
            next_path = _normalize_remote_api_path(next_link)

        domains = []
        for svc in all_services:
            svc_id = svc.get("id", "")
            svc_name = svc.get("name", "")
            public_domain = svc.get("public_domain", "")
            custom_domains = svc.get("custom_domains", [])
            if not isinstance(custom_domains, list):
                custom_domains = []
            for domain in custom_domains:
                domains.append({
                    "domain": domain,
                    "service_id": svc_id,
                    "service_name": svc_name,
                    "public_domain": public_domain,
                    "verified": svc.get("domain_verified", False),
                    "verification_token": svc.get("verification_token", ""),
                })

        return Response({"domains": domains, "count": len(domains)})

    # ── Registry Access ───────────────────────────────────────────────

    @action(detail=True, methods=["get", "post"], url_path="registries")
    def registries(self, request, pk=None):
        """
        GET  /api/v1/servers/{id}/registries/  — list registries this node can access
        POST /api/v1/servers/{id}/registries/  — set which registries this node can access

        POST body::
            {
                "registry_ids": ["uuid1", "uuid2"]
            }

        The node's installer runs ``docker login`` for each registry
        during provisioning, so the node can pull images from them.
        """
        server = self.get_object()

        if request.method == "POST":
            registry_ids = request.data.get("registry_ids", [])
            if not isinstance(registry_ids, list):
                return Response(
                    {"error": "registry_ids must be a list of UUIDs"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Per-registry ownership check.
            #
            # Before this filter, any authenticated user could POST a list of
            # active ScopedRegistry UUIDs and attach them to their own server.
            # The node installer would then ``docker login`` with credentials
            # belonging to a different tenant. Restrict to registries whose
            # GenericForeignKey scope (Organization / Team / Project) is one
            # the requesting user has a relationship with.
            from django.contrib.contenttypes.models import ContentType
            from django.db.models import Q

            from apps.organizations.models import Organization, OrganizationMembership
            from apps.teams.models import Team, TeamMember

            from apps.deployments.models import ScopedRegistry
            from apps.deployments.models.core import Project
            from apps.deployments.models.project import ProjectMember

            user_org_ids = set(
                OrganizationMembership.objects
                .filter(user=request.user)
                .values_list("organization_id", flat=True)
            ) | set(
                Organization.objects.filter(owner=request.user).values_list("id", flat=True)
            )
            user_team_ids = set(
                TeamMember.objects
                .filter(user=request.user, is_active=True)
                .values_list("team_id", flat=True)
            )
            # Team owners also reach team-scoped registries.
            user_team_ids |= set(
                Team.objects.filter(owner=request.user).values_list("id", flat=True)
            )
            user_project_ids = set(
                Project.objects.filter(owner=request.user).values_list("id", flat=True)
            ) | set(
                ProjectMember.objects.filter(user=request.user).values_list("project_id", flat=True)
            )

            org_ct = ContentType.objects.get_for_model(Organization)
            team_ct = ContentType.objects.get_for_model(Team)
            project_ct = ContentType.objects.get_for_model(Project)

            accessible_scopes = (
                Q(content_type=org_ct, object_id__in=user_org_ids)
                | Q(content_type=team_ct, object_id__in=user_team_ids)
                | Q(content_type=project_ct, object_id__in=user_project_ids)
            )

            registries = (
                ScopedRegistry.objects
                .filter(id__in=registry_ids, is_active=True)
                .filter(accessible_scopes)
            )
            if len(registries) != len(registry_ids):
                # Collapse "missing / inactive / inaccessible" into one opaque
                # 400 so users cannot enumerate which IDs exist by probing.
                return Response(
                    {"error": "One or more registry IDs are invalid, inactive, or inaccessible"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            server.registry_access.set(registries)
            logger.info(
                "Set %d registries for server %s (%s)",
                len(registries), server.name, server.id,
            )
            return Response({
                "status": "ok",
                "registry_ids": [str(r.id) for r in registries],
            })

        # GET: return registries this node can access
        registries = server.registry_access.filter(is_active=True)
        return Response({
            "count": registries.count(),
            "registries": [
                {
                    "id": str(r.id),
                    "registry_url": r.registry_url,
                    "is_internal": r.is_internal,
                    "scope_type": r.content_type.model if r.content_type else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in registries
            ],
        })

    # ── Self-Healing ─────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    @throttle_classes([ServerHealThrottle])
    def heal(self, request, pk=None):
        """
        Trigger self-healing on a remote server.

        Body (optional):
        {
            "deployment_id": "uuid",  // specific deployment to heal
            "action": "restart_container" | "restart_stack" | "restart_docker_daemon" | "diagnose" | "full"
        }

        If no deployment_id is provided, runs node-level diagnostics and healing.
        """
        server = self.get_object()

        if not server.ssh_key and not server.ssh_password:
            return Response(
                {"error": "No SSH credentials stored for this server"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        action = request.data.get("action", "full")
        deployment_id = request.data.get("deployment_id")

        if action == "diagnose":
            return self._run_diagnostics(server)

        if deployment_id:
            try:
                from apps.deployments.models.core import Deployment
                deployment = Deployment.objects.get(id=deployment_id)
            except (Deployment.DoesNotExist, ValueError):
                return Response(
                    {"error": f"Deployment {deployment_id} not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            from apps.deployments.tasks.deployment.tasks_deploy_remote import self_heal_remote_deployment
            self_heal_remote_deployment.delay(
                deployment_id=str(deployment.id),
                server_id=str(server.id),
            )
            return Response({
                "status": "healing_triggered",
                "deployment_id": str(deployment.id),
                "message": "Self-healing task queued",
            })

        if action in ("restart_container", "restart_docker_daemon", "restart_stack", "full"):
            return self._trigger_node_healing(server, action)

        return Response(
            {"error": f"Unknown action: {action}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=True, methods=["get", "post"])
    def diagnostics(self, request, pk=None):
        """
        Get current diagnostics for a remote server.

        Returns Docker status, resource usage, container states, etc.
        """
        server = self.get_object()
        return self._run_diagnostics(server)

    @action(detail=True, methods=["post"])
    @throttle_classes([ServerCommandThrottle])
    def run_command(self, request, pk=None):
        """
        Run a diagnostic/recovery command on a remote server via SSH.

        Body: { "command": "docker ps -a" }

        Only allows safe diagnostic and recovery commands.
        """
        server = self.get_object()

        if not server.ssh_key and not server.ssh_password:
            return Response(
                {"error": "No SSH credentials stored for this server"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        command = request.data.get("command", "").strip()
        if not command:
            return Response(
                {"error": "Command is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not _is_command_allowed(command):
            return Response(
                {"error": "Command not allowed. Only safe docker subcommands (ps, logs, stats, inspect, images, info, version, df, top, port, events) and system diagnostic commands are permitted."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            from apps.deployments.services.self_healing_orchestrator import (
                SelfHealingOrchestrator,
            )
            orchestrator = SelfHealingOrchestrator(server)
            out, err, code = orchestrator._exec(command, timeout=60)
            orchestrator._close_ssh()

            try:
                redacted_out = _redact_transfer_text(out or "")
            except Exception as exc:
                logger.error("Redaction failed for run_command stdout: %s", exc)
                redacted_out = "[REDACTION FAILED — output suppressed for safety]"
            try:
                redacted_err = _redact_transfer_text(err or "")
            except Exception as exc:
                logger.error("Redaction failed for run_command stderr: %s", exc)
                redacted_err = "[REDACTION FAILED — output suppressed for safety]"

            return Response({
                "command": command,
                "exit_code": code,
                "stdout": redacted_out[:10000],
                "stderr": redacted_err[:5000],
            })
        except Exception as exc:
            return Response(
                {"error": f"Command execution failed: {exc!s}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=['get'], url_path='incident-report')
    def incident_report(self, request, pk=None):
        """Aggregate server-level incident report.

        GET /api/v1/servers/{id}/incident-report/

        Returns all incidents affecting this server: failed deployments,
        health transitions, provisioning failures, transfer failures,
        service lifecycle events, and mesh/network problems.
        """
        server = self.get_object()
        from django.db.models import Q

        from apps.deployments.models.audit import AuditLog
        from apps.deployments.models.backup import ServiceBackup
        from apps.deployments.models.core import Deployment, Service
        from apps.deployments.models.transfer import ServerTransfer

        events: list = []
        server_name = server.name or server.host or str(server.id)

        # ── 1. Failed deployments on this server ─────────────────────
        failure_statuses = [
            'FAILED', 'CANCELLED', 'BUILD_FAILED', 'BACKUP_FAILED',
            'MIGRATION_FAILED', 'HEALTH_CHECK_FAILED',
        ]
        services = Service.objects.filter(server=server)
        failed_deploys = (
            Deployment.objects
            .filter(service__in=services, status__in=failure_statuses)
            .select_related('service')
            .order_by('-created_at')[:30]
        )
        for d in failed_deploys:
            events.append({
                'type': 'deployment',
                'severity': 'critical' if d.status == 'FAILED' else 'warning',
                'timestamp': d.created_at.isoformat() if d.created_at else '',
                'title': f"{d.service.name}: deployment {d.status.lower().replace('_', ' ')}",
                'detail': (d.commit_message or '')[:500],
                'service_id': str(d.service_id),
                'service_name': d.service.name,
                'deployment_id': str(d.id),
                'status': d.status,
            })

        # ── 2. Failed backups on this server ─────────────────────────
        failed_backups = (
            ServiceBackup.objects
            .filter(service__in=services, status='FAILED')
            .select_related('service')
            .order_by('-created_at')[:10]
        )
        for b in failed_backups:
            events.append({
                'type': 'backup_failure',
                'severity': 'warning',
                'timestamp': b.created_at.isoformat() if b.created_at else '',
                'title': f"{b.service.name}: backup failed",
                'detail': b.error_message or '',
                'service_id': str(b.service_id),
                'backup_id': str(b.id),
            })

        # ── 3. Health transitions on this server ─────────────────────
        health_actions = [
            'HEALTH_TRANSITION', 'SERVICE_HEALTHY', 'SERVICE_UNHEALTHY',
        ]
        service_ids = [str(s.id) for s in services]
        health_audits = []
        if service_ids:
            from django.db.models import Q as QQ
            health_filter = QQ()
            for sid in service_ids:
                health_filter |= QQ(metadata__contains={'service_id': sid})
            health_audits = list(
                AuditLog.objects
                .filter(health_filter)
                .filter(action__in=health_actions)
                .order_by('-timestamp')[:20]
            )
        for a in health_audits:
            previous = (a.metadata or {}).get('previous', '')
            current = (a.metadata or {}).get('current', '')
            events.append({
                'type': 'health',
                'severity': (
                    'critical' if a.action == 'SERVICE_UNHEALTHY' else 'warning'
                ),
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': f'{previous} → {current}' if previous and current else a.action.replace('_', ' ').title(),
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
                'action': a.action,
            })

        # ── 4. Transfers involving this server ───────────────────────
        target_ip_match = Q(target_server_ip=server.host)
        if server.private_ip:
            target_ip_match |= Q(target_server_ip=server.private_ip)
        transfers = (
            ServerTransfer.objects
            .filter(
                Q(source_server_id=str(server.id)) | target_ip_match,
            )
            .exclude(status='COMPLETED')
            .order_by('-created_at')[:10]
        )
        for t in transfers:
            events.append({
                'type': 'transfer',
                'severity': 'critical' if t.status == 'FAILED' else 'warning',
                'timestamp': t.created_at.isoformat() if t.created_at else '',
                'title': f"Server transfer {t.status.lower()}",
                'detail': t.error_message or 'Source → Target',
                'transfer_id': str(t.id),
                'status': t.status,
            })

        # ── 5. Provisioning failures ─────────────────────────────────
        prov_logs = getattr(server, 'provision_logs', '') or ''
        if prov_logs:
            prov_lines = prov_logs.split('\n')
            for line in reversed(prov_lines[-20:]):
                lower = line.strip().lower()
                if not lower:
                    continue
                if 'error' in lower or 'fail' in lower or 'exception' in lower:
                    events.append({
                        'type': 'provisioning',
                        'severity': 'warning',
                        'timestamp': '',
                        'title': 'Provisioning error detected',
                        'detail': line.strip()[:300],
                    })

        # ── 6. Server metadata ──────────────────────────────────────
        service_list = [
            {'id': str(s.id), 'name': s.name, 'status': s.status}
            for s in services
        ]
        active_count = sum(1 for s in services if s.status == 'ACTIVE')

        events.sort(key=lambda e: e['timestamp'] or '', reverse=True)

        severity_counts = {'critical': 0, 'warning': 0, 'info': 0}
        for e in events:
            sev = e.get('severity', 'info')
            if sev in severity_counts:
                severity_counts[sev] += 1

        return Response({
            'server_id': str(server.id),
            'server_name': server_name,
            'server_status': server.status,
            'total_services': len(service_list),
            'active_services': active_count,
            'total_events': len(events),
            'critical': severity_counts['critical'],
            'warning': severity_counts['warning'],
            'info': severity_counts['info'],
            'services': service_list,
            'events': events,
        })

    def _run_diagnostics(self, server):
        try:
            from apps.deployments.services.self_healing_orchestrator import (
                SelfHealingOrchestrator,
            )
            orchestrator = SelfHealingOrchestrator(server)
            diagnostics = orchestrator.run_full_diagnostics()
            orchestrator._close_ssh()

            return Response({
                "server": {
                    "id": str(server.id),
                    "name": server.name,
                    "host": server.host,
                },
                "docker_running": diagnostics.docker_running,
                "disk_usage_pct": diagnostics.disk_usage_pct,
                "memory_usage_pct": diagnostics.memory_usage_pct,
                "network_reachable": diagnostics.network_reachable,
                "failure_type": diagnostics.failure_type.value,
                "container_state": diagnostics.container_state,
                "error_details": diagnostics.error_details,
                "suggested_actions": [a.value for a in diagnostics.suggested_actions],
                "exited_containers": diagnostics.raw_diagnostics.get("exited_containers", ""),
            })
        except Exception as exc:
            return Response(
                {"error": f"Diagnostics failed: {exc!s}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _trigger_node_healing(self, server, action: str):
        """Trigger node-level healing actions."""
        try:
            from apps.deployments.services.self_healing_orchestrator import (
                RecoveryAction,
                SelfHealingOrchestrator,
            )

            orchestrator = SelfHealingOrchestrator(server)

            action_map = {
                "restart_container": RecoveryAction.RESTART_CONTAINER,
                "restart_docker_daemon": RecoveryAction.RESTART_DOCKER_DAEMON,
                "restart_stack": RecoveryAction.RESTART_STACK,
                "full": RecoveryAction.RESTART_STACK,
            }
            recovery_action = action_map.get(action, RecoveryAction.RESTART_STACK)

            class _FakeDeployment:
                id = "manual"
                container_id = ""
                service = type("obj", (object,), {"name": ""})()

            result = orchestrator._execute_recovery(
                recovery_action, _FakeDeployment(), orchestrator._diagnostics
            )
            orchestrator._close_ssh()

            return Response({
                "action": recovery_action.value,
                "success": result.success,
                "details": result.details,
                "post_recovery_status": result.post_recovery_status,
                "next_action": result.next_action.value if result.next_action else None,
                "heal_log": orchestrator.get_heal_log()[-10:],
            })
        except Exception as exc:
            return Response(
                {"error": f"Healing failed: {exc!s}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
