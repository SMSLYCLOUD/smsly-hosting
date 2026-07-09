import contextlib
import hashlib
import hmac
import ipaddress
import logging
import os
import re
import shlex
import socket
import time

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from .models import PlatformConfig, Service  # type: ignore[attr-defined]
from .models_servers import ManagedServer
from .models_transfer import ServerTransfer
from .serializers import (  # type: ignore[attr-defined]    # defined in serializers_transfer.py; not re-exported by serializers.py hub.
    ServerTransferCreateSerializer,
    ServerTransferSerializer,
)
from .services.server_guard import ServerGuard
from .tasks_transfer import execute_server_transfer_task, rollback_transfer_task

logger = logging.getLogger(__name__)

ACTIVE_TRANSFER_STATUSES = [
    'PREPARING',
    'UPLOADING',
    'RESTORING',
    'DNS_CUTOVER',
    'VERIFYING',
]

# Allowed image name pattern: must start with a known registry prefix or be
# a bare Docker Hub library image (e.g. "nginx:latest").  This prevents an
# attacker from specifying an arbitrary image that could pull malicious code.
_VALID_IMAGE_RE = re.compile(
    r'^('
    r'(?:[a-zA-Z0-9._-]+\.)+[a-zA-Z]{2,}'  # registry host (e.g. registry.example.com)
    r'(?::\d+)?'                              # optional port
    r'/)?'                                    # slash (group optional for bare images)
    r'[a-zA-Z0-9][a-zA-Z0-9._/-]*'           # image name
    r'(?::[a-zA-Z0-9._-]+)?'                 # optional tag
    r'(@sha256:[a-f0-9]{64})?$'              # optional digest
)


def _validate_transfer_image(image: str) -> bool:
    """Return True if *image* matches a safe pattern.

    Additionally, if the platform has CONTAINER_REGISTRY_URL configured,
    the image must either start with that registry prefix or be a bare
    Docker Hub library reference (no host component).
    """
    if not image or not _VALID_IMAGE_RE.match(image):
        return False
    # Reject anything with shell metacharacters (defense-in-depth)
    if any(ch in image for ch in (';', '|', '&', '$', '`', '(', ')', '{', '}', '\n')):
        return False
    registry_url = getattr(settings, 'CONTAINER_REGISTRY_URL', None) or ''
    if not registry_url:
        return True
    # Normalize: strip scheme for comparison
    registry_host = registry_url.replace('https://', '').replace('http://', '').rstrip('/')
    # If the image has a registry component, it must match the platform registry
    if '/' in image:
        image_host = image.split('/')[0]
        if '.' in image_host or ':' in image_host or image_host == 'localhost':
            return image_host == registry_host
    # Bare library image (e.g. "nginx:latest") — allow it
    return True


class TransferCreateThrottle(UserRateThrottle):
    scope = 'transfers'
    rate = '5/minute'


def is_safe_ip(ip_str, allow_private=False):
    """
    Check if an IP is safe for public transfer requests.
    Blocks private, loopback, and reserved ranges to prevent SSRF.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        if (ip.is_loopback or ip.is_link_local or
            ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False
        return not (ip.is_private and not allow_private)
    except ValueError:
        return False


def _gateway_secret_candidates(source_ip):
    secrets = []
    for server in ManagedServer.objects.filter(
        Q(host=source_ip) | Q(private_ip=source_ip)
    ):
        secret = str(server.gateway_secret or '').strip()
        if secret:
            secrets.append(secret)

    configured_secret = str(
        getattr(settings, 'GATEWAY_SECRET', '') or getattr(settings, 'SECRET_KEY', '')
    ).strip()
    if configured_secret:
        secrets.append(configured_secret)

    return secrets


def _verify_transfer_sync_hmac(request, source_ip, body):
    signature = request.headers.get('X-Gateway-Signature-V2', '')
    timestamp = request.headers.get('X-Request-Timestamp', '')
    nonce = request.headers.get('X-Request-Nonce', '')
    if not signature or not timestamp or not nonce:
        return False

    try:
        request_ts = int(timestamp)
    except ValueError:
        return False

    if abs(int(time.time()) - request_ts) > 300:
        return False

    # SECURITY (Batch G): nonce replay protection. Each nonce is
    # one-use within the 5-minute window. Without this, a captured
    # request can be replayed to create duplicate ServerTransfer
    # rows. The nonce is also bound into the signed payload.
    from django.core.cache import cache
    nonce_key = f"transfer_nonce:{nonce}"
    if cache.get(nonce_key):
        return False
    cache.set(nonce_key, "1", timeout=600)

    body_hash = hashlib.sha256(body).hexdigest()
    payload = f"{request.method}|{request.get_full_path()}|{timestamp}|{nonce}|{body_hash}"

    for secret in _gateway_secret_candidates(source_ip):
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature):
            return True

    return False


def _resolve_incoming_owner(request, source_ip):
    if request.user and request.user.is_authenticated:
        return request.user

    server = ManagedServer.objects.filter(
        Q(host=source_ip) | Q(private_ip=source_ip)
    ).select_related('owner').first()
    if server and server.owner_id:
        return server.owner

    # No owner found — fall back to the first admin user
    from django.contrib.auth import get_user_model
    admin = get_user_model().objects.filter(is_superuser=True).first()
    if admin:
        return admin

    raise RuntimeError(
        f"Cannot resolve owner for incoming transfer from {source_ip}. "
        "The source ManagedServer has no owner assigned and no admin user exists."
    )


class ServerTransferViewSet(viewsets.ModelViewSet):
    queryset = ServerTransfer.objects.select_related('service').all()
    serializer_class = ServerTransferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.action == 'register_incoming' or self.action.startswith('incoming_'):
            return [permissions.AllowAny()]
        return super().get_permissions()

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return self.queryset.none()
        return self.queryset.filter(
            Q(service__owner=user) | Q(owner=user)
        ).distinct().order_by('-created_at')

    @action(detail=False, methods=['post'], url_path='register-incoming')
    def register_incoming(self, request):
        """
        Internal endpoint: Source node notifies target node of an incoming transfer.
        This allows the target dashboard to show the transfer status.
        """
        raw_body = request.body
        data = request.data
        source_ip = data.get('source_ip')
        target_ip = data.get('target_ip')
        transfer_type = data.get('transfer_type', 'SERVICE')
        service_name = data.get('service_name')

        if transfer_type not in {'SERVICE', 'FULL'}:
            return Response(
                {'error': 'transfer_type must be SERVICE or FULL.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not source_ip or not target_ip:
            return Response(
                {'error': 'source_ip and target_ip are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            ipaddress.ip_address(source_ip)
            ipaddress.ip_address(target_ip)
        except ValueError:
            return Response(
                {'error': 'source_ip and target_ip must be valid IP addresses.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Require HMAC + Remote-Sync header for ALL requests to this
        # internal endpoint, regardless of session/token auth.
        is_remote_sync = request.headers.get('X-SMSLY-Remote-Sync') == '1'
        if not is_remote_sync or not _verify_transfer_sync_hmac(request, source_ip, raw_body):
            return Response(
                {'error': 'Valid node authentication is required.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        # Verify the source IP corresponds to a known ManagedServer
        if not ManagedServer.objects.filter(
            Q(host=source_ip) | Q(private_ip=source_ip)
        ).exists():
            return Response(
                {'error': 'Unknown source node. Register the server first.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            owner = _resolve_incoming_owner(request, source_ip)
        except RuntimeError:
            logger.exception("Incoming owner resolution failed")
            return Response({'error': 'Authentication failed.'}, status=status.HTTP_401_UNAUTHORIZED)

        # Check if a similar incoming transfer already exists
        existing = ServerTransfer.objects.filter(
            source_node_id=source_ip,
            target_server_ip=target_ip,
            owner=owner,
            status__in=ACTIVE_TRANSFER_STATUSES,
        ).first()

        if existing:
            return Response({'id': str(existing.id), 'status': existing.status})

        transfer = ServerTransfer.objects.create(
            status='PREPARING',
            source_server_ip=source_ip,
            target_server_ip=target_ip,
            transfer_type=transfer_type,
            owner=owner,
            is_incoming=True,
            source_node_id=source_ip,
            current_step=f"Incoming {transfer_type} transfer from {source_ip}"
        )
        if service_name:
            transfer.logs = f"Targeting service: {service_name}\n"
            transfer.save(update_fields=['logs'])

        return Response({'id': str(transfer.id), 'status': transfer.status})

    @throttle_classes([TransferCreateThrottle])
    def create(self, request, *args, **kwargs):
        logger.info(
            "Transfer request received: transfer_type=%s service_id=%s target_server_id=%s target_ip_provided=%s auth_provided=%s",
            request.data.get('transfer_type'),
            request.data.get('service_id'),
            request.data.get('target_server_id'),
            bool(request.data.get('target_server_ip')),
            bool(request.data.get('target_ssh_key') or request.data.get('target_ssh_password')),
        )

        serializer = ServerTransferCreateSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning(f"Transfer validation failed: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        payload = serializer.validated_data

        transfer_type = payload['transfer_type']

        service = None
        if transfer_type == 'SERVICE':
            service = Service.objects.filter(
                id=payload.get('service_id'),
                owner=request.user,
            ).first()
            if not service:
                logger.warning(f"Transfer failed: Service {payload.get('service_id')} not found for user {request.user}")
                return Response(
                    {'error': 'Service not found'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        source_server_id = str(payload.get('source_server_id') or '')
        source_server_ip = payload.get('source_server_ip')
        source_ssh_key = (payload.get('source_ssh_key') or '').strip()
        source_ssh_password = (payload.get('source_ssh_password') or '').strip()
        source_server = None

        if source_server_id:
            source_server = ManagedServer.objects.filter(id=source_server_id, owner=request.user).first()
            if source_server:
                if not source_server_ip:
                    source_server_ip = source_server.host or source_server.private_ip or source_server.wg_address
                if not source_ssh_key and not source_ssh_password:
                    source_ssh_key = (source_server.ssh_key or '').strip()
                    source_ssh_password = (source_server.ssh_password or '').strip()

        if not source_server_ip:
            source_server_ip = PlatformConfig.load().server_ip

        if not source_server_ip:
            logger.warning("Transfer failed: Source server IP (local node IP) not set in PlatformConfig.")
            return Response(
                {
                    'error': (
                        'Source server IP is required. Set system domain config server_ip '
                        'or pass source_server_ip explicitly.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # SSRF Protection for source IP
        try:
            local_cfg_ip = PlatformConfig.load().server_ip
        except Exception:
            local_cfg_ip = ''
        local_cfg_ip = (local_cfg_ip or '').strip()
        source_is_local = source_server_ip in {'127.0.0.1', 'localhost'} or (
            local_cfg_ip and source_server_ip == local_cfg_ip
        )
        # Allow private IPs if the source resolves to a known ManagedServer
        # (WireGuard mesh addresses or internal VPC IPs are valid sources).
        source_is_managed = source_server and source_server.status == 'ONLINE'
        if not source_is_local and not source_is_managed and not is_safe_ip(source_server_ip, allow_private=False):
            logger.warning(
                "Transfer failed: Source IP %s blocked by SSRF protection (user=%s).",
                source_server_ip, request.user,
            )
            return Response(
                {
                    'error': (
                        'Source server IP is in a forbidden range (SSRF protection). '
                        'Only public IPs, known managed servers, or the local node IP are allowed.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        target_server = None
        if payload.get('target_server_id'):
            target_server = ManagedServer.objects.filter(
                id=payload['target_server_id'],
                owner=request.user,
            ).first()
            if not target_server:
                logger.warning(f"Transfer failed: Target server {payload.get('target_server_id')} not found for user {request.user}")
                return Response(
                    {'error': 'Connected target server not found'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            guard = ServerGuard.check_user_workload_allowed(target_server)
            if not guard["ok"]:
                logger.warning(
                    "Transfer failed: target server %s is not a workload target",
                    target_server.id,
                )
                return Response(guard, status=status.HTTP_400_BAD_REQUEST)

        target_server_ip = payload.get('target_server_ip') or (
            target_server.host if target_server else PlatformConfig.load().server_ip
        )
        target_server_ip = str(target_server_ip or '').strip()
        if not target_server_ip:
            logger.warning("Transfer failed: Target server IP could not be resolved.")
            return Response(
                {'error': 'Target server IP is required (local node IP not set).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        resolved_target_ip = None
        try:
            ipaddress.ip_address(target_server_ip)
            resolved_target_ip = target_server_ip
        except ValueError:
            try:
                for family, _, _, _, sockaddr in socket.getaddrinfo(target_server_ip, None):
                    if family in (socket.AF_INET, socket.AF_INET6):
                        resolved_target_ip = sockaddr[0]
                        break
            except socket.gaierror:
                resolved_target_ip = None

            if not resolved_target_ip:
                logger.warning(f"Transfer failed: Target host {target_server_ip} did not resolve to IP.")
                return Response(
                    {
                        'error': (
                            'Target server host must resolve to an IP address for transfer SSH. '
                            'Use an IP-based connected server or pass target_server_ip.'
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        target_server_ip = resolved_target_ip

        # SSRF Protection
        if not is_safe_ip(target_server_ip, allow_private=bool(target_server)):
            logger.warning(f"Transfer failed: Target IP {target_server_ip} blocked by SSRF protection.")
            return Response(
                {'error': 'Target server IP is in a forbidden range (SSRF protection).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if target_server:
            logger.info(
                "Transfer SSRF: target_ip=%s source_ip=%s user_id=%s used allow_private override",
                target_server_ip, source_server_ip, request.user.id,
            )

        target_ssh_key = str(payload.get('target_ssh_key') or '').strip()
        target_ssh_password = str(payload.get('target_ssh_password') or '').strip()
        if not target_ssh_key and not target_ssh_password and target_server:
            target_ssh_key = str(target_server.ssh_key or '').strip()
            target_ssh_password = str(target_server.ssh_password or '').strip()

        # Determine if target is local — no SSH needed when deploying to the local node
        local_ips = {'127.0.0.1', 'localhost', ''}
        try:
            local_cfg_ip = PlatformConfig.load().server_ip
            if local_cfg_ip:
                local_ips.add(local_cfg_ip.strip())
        except Exception:
            pass
        target_is_local = (
            not payload.get('target_server_id')
            and target_server_ip in local_ips
        )

        if transfer_type == 'SERVICE':
            pass  # SERVICE transfers use the REST API — no SSH needed
        elif not target_is_local and not target_ssh_key and not target_ssh_password:
            logger.warning(f"Transfer failed: No SSH credentials found for target {target_server_ip}")
            return Response(
                {
                    'error': (
                        'No SSH credentials available for target server. '
                        'Provide password/key in the transfer form or update the connected server credentials.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = ServerTransfer.objects.filter(
            owner=request.user,
            target_server_ip=target_server_ip,
            transfer_type=transfer_type,
            status__in=ACTIVE_TRANSFER_STATUSES,
        )
        if transfer_type == 'SERVICE':
            existing = existing.filter(service=service)
        else:
            existing = existing.filter(service__isnull=True)
        existing = existing.first()
        if existing:
            return Response(
                {
                    'error': 'A transfer for this target is already running.',
                    'id': str(existing.id),
                    'status': existing.status,
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Create transfer object
        transfer = ServerTransfer.objects.create(
            source_server_ip=source_server_ip,
            source_server_id=source_server_id,
            source_ssh_key=source_ssh_key,
            source_ssh_password=source_ssh_password,
            target_server_ip=target_server_ip,
            target_ssh_key=target_ssh_key,
            target_ssh_password=target_ssh_password,
            target_public_domain=payload.get('target_public_domain', ''),
            transfer_type=transfer_type,
            service=service,
            owner=request.user,
        )

        try:
            execute_server_transfer_task.delay(str(transfer.id))
        except Exception as exc:
            logger.exception("Failed to queue transfer %s: %s", transfer.id, exc)
            transfer.status = 'FAILED'
            transfer.error_message = 'Transfer could not be queued. Please retry after checking worker availability.'
            transfer.target_ssh_key = ''
            transfer.target_ssh_password = ''
            transfer.source_ssh_key = ''
            transfer.source_ssh_password = ''
            transfer.save(update_fields=[
                'status',
                'error_message',
                'target_ssh_key',
                'target_ssh_password',
                'source_ssh_key',
                'source_ssh_password',
            ])
            return Response(
                {'error': transfer.error_message, 'id': str(transfer.id)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(ServerTransferSerializer(transfer).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def rollback(self, request, pk=None):
        transfer = self.get_object()
        if not transfer.can_rollback:
            return Response({'error': 'Rollback not available'}, status=status.HTTP_400_BAD_REQUEST)

        rollback_transfer_task.delay(transfer_id=str(transfer.id))
        return Response({'status': 'rollback_started'})

    @action(detail=True, methods=['post'])
    def extend_rollback(self, request, pk=None):
        """Extend the rollback deadline by N hours (default 24).

        Body: {"hours": 48}
        """
        transfer = self.get_object()
        if transfer.status != 'COMPLETED':
            return Response(
                {'error': 'Can only extend rollback for COMPLETED transfers.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not transfer.rollback_deadline:
            return Response(
                {'error': 'This transfer has no rollback deadline.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        hours = int(request.data.get('hours', 24))
        if hours < 1 or hours > 720:  # max 30 days
            return Response(
                {'error': 'hours must be between 1 and 720.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from datetime import timedelta
        transfer.rollback_deadline = transfer.rollback_deadline + timedelta(hours=hours)
        transfer.save(update_fields=['rollback_deadline', 'updated_at'])
        return Response({
            'status': 'deadline_extended',
            'rollback_deadline': transfer.rollback_deadline.isoformat(),
            'added_hours': hours,
        })

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        transfer = self.get_object()
        if transfer.status in {'COMPLETED', 'FAILED', 'ROLLED_BACK'}:
            return Response(
                {'error': f'Cannot cancel transfer in terminal state {transfer.status}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if transfer.status == 'CANCELLED':
            return Response(
                {'error': 'Transfer already cancelled.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        transfer.status = 'CANCELLED'
        transfer.error_message = 'Cancelled by user.'
        transfer.save(update_fields=['status', 'error_message'])
        return Response({'status': 'CANCELLED'})

    # ─── Incoming Transfer REST Endpoints ─────────────────────────────────
    # These replace SSH-based operations. The master calls these endpoints
    # on the target node's API with HMAC V2 auth. The node executes the
    # operation locally via the Docker SDK (socket-proxy).

    def _incoming_auth_required(self, request, transfer):
        """Verify HMAC signature for incoming transfer operations.

        Checks both REMOTE_ADDR (actual TCP peer) and the transfer's
        recorded source IP (in case of NAT/proxy).
        """
        raw_body = request.body
        remote_addr = request.META.get('REMOTE_ADDR', '')

        # Try REMOTE_ADDR first (most reliable when direct)
        if remote_addr and _verify_transfer_sync_hmac(request, remote_addr, raw_body):
            return True

        # Fallback: check the transfer's recorded source IPs (NAT/proxy case)
        source_ip = str(getattr(transfer, 'source_server_ip', '') or '').strip()
        if source_ip and source_ip != remote_addr:
            if _verify_transfer_sync_hmac(request, source_ip, raw_body):
                return True

        source_node = str(getattr(transfer, 'source_node_id', '') or '').strip()
        if source_node and source_node not in (remote_addr, source_ip):
            if _verify_transfer_sync_hmac(request, source_node, raw_body):
                return True

        return False

    @action(detail=True, methods=['post'], url_path='incoming/pull-image')
    def incoming_pull_image(self, request, pk=None):
        """Pull a Docker image from the registry to this node."""
        transfer = self.get_object()
        if not self._incoming_auth_required(request, transfer):
            return Response({'error': 'Invalid HMAC signature'}, status=status.HTTP_401_UNAUTHORIZED)
        image = request.data.get('image')
        if not image:
            return Response({'error': 'image required'}, status=status.HTTP_400_BAD_REQUEST)
        if not _validate_transfer_image(image):
            return Response(
                {'error': 'Image name rejected: must match platform registry or be a valid Docker Hub library image.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            client.images.pull(image)
            return Response({'status': 'pulled', 'image': image})
        except Exception:
            logger.exception("Image pull failed")
            return Response({'error': 'An internal error occurred. Please check server logs.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='incoming/deploy')
    def incoming_deploy(self, request, pk=None):
        """Start a service container on this node using pre-configured params."""
        transfer = self.get_object()
        if not self._incoming_auth_required(request, transfer):
            return Response({'error': 'Invalid HMAC signature'}, status=status.HTTP_401_UNAUTHORIZED)
        image = request.data.get('image')
        container_name = request.data.get('container_name')
        env = request.data.get('env', {})
        labels = request.data.get('labels', {})
        network = request.data.get('network', 'smsly-net')
        restart_policy = request.data.get('restart_policy', 'unless-stopped')
        if not image or not container_name:
            return Response({'error': 'image and container_name required'}, status=status.HTTP_400_BAD_REQUEST)
        if not _validate_transfer_image(image):
            return Response(
                {'error': 'Image name rejected: must match platform registry or be a valid Docker Hub library image.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            container = client.containers.run(
                image=image,
                name=container_name,
                environment=env,
                labels=labels,
                network=network,
                restart_policy={"Name": restart_policy},
                detach=True,
            )
            return Response({'container_id': container.id, 'status': 'running'})
        except Exception:
            logger.exception("Transfer operation failed")
            return Response({'error': 'An internal error occurred. Please check server logs.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='incoming/exec')
    def incoming_exec(self, request, pk=None):
        """Execute a Python script in this node's backend container (or shell command)."""
        transfer = self.get_object()
        if not self._incoming_auth_required(request, transfer):
            return Response({'error': 'Invalid HMAC signature'}, status=status.HTTP_401_UNAUTHORIZED)
        script = request.data.get('script')
        shell = request.data.get('shell', False)
        container = request.data.get('container', '')
        if not script:
            return Response({'error': 'script required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            if shell:
                import subprocess
                # SECURITY: Use shell=False with shlex.split to prevent
                # shell injection. If the command requires shell features,
                # the caller must break it into safe components.
                cmd = shlex.split(script)
                if not cmd:
                    return Response({'error': 'Empty command'}, status=status.HTTP_400_BAD_REQUEST)
                result = subprocess.run(
                    cmd, shell=False, capture_output=True, text=True, timeout=300,
                )
            elif container:
                from apps.cloud.docker_client import get_docker_client
                client = get_docker_client()
                try:
                    target = client.containers.get(container)
                except Exception:
                    targets = client.containers.list(filters={"name": container})
                    if not targets:
                        return Response(
                            {'error': f'Container matching "{container}" not found'},
                            status=status.HTTP_404_NOT_FOUND,
                        )
                    target = targets[0]
                # Use list-form command so docker-py doesn't shlex.split() multi-line scripts
                exit_code, output = target.exec_run(["python3", "-c", script])
                output_text = output.decode() if isinstance(output, bytes) else str(output)
                return Response({'stdout': output_text, 'exit_code': exit_code})
            else:
                import subprocess
                result = subprocess.run(
                    ['python3', '-c', script],
                    capture_output=True, text=True, timeout=300,
                )
            return Response({
                'stdout': result.stdout,
                'stderr': result.stderr,
                'exit_code': result.returncode,
            })
        except subprocess.TimeoutExpired:
            return Response({'error': 'Script timed out'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception:
            logger.exception("Incoming exec failed")
            return Response({'error': 'An internal error occurred. Please check server logs.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='incoming/stop-container')
    def incoming_stop_container(self, request, pk=None):
        """Stop and remove a container on this node (used for source-side stop)."""
        transfer = self.get_object()
        if not self._incoming_auth_required(request, transfer):
            return Response({'error': 'Invalid HMAC signature'}, status=status.HTTP_401_UNAUTHORIZED)
        container_name = request.data.get('container_name')
        if not container_name:
            return Response({'error': 'container_name required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            try:
                container = client.containers.get(container_name)
                container.stop(timeout=10)
                container.remove()
            except Exception:
                pass
            return Response({'status': 'stopped'})
        except Exception:
            logger.exception("Container stop failed")
            return Response({'error': 'An internal error occurred. Please check server logs.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='incoming/container-status')
    def incoming_container_status(self, request, pk=None):
        """Check if a container is running on this node."""
        transfer = self.get_object()
        if not self._incoming_auth_required(request, transfer):
            return Response({'error': 'Invalid HMAC signature'}, status=status.HTTP_401_UNAUTHORIZED)
        container_name = request.query_params.get('container_name')
        if not container_name:
            return Response({'error': 'container_name required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            container = client.containers.get(container_name)
            return Response({
                'running': container.status == 'running',
                'status': container.status,
            })
        except Exception:
            return Response({'running': False, 'status': 'not_found'})

    @action(detail=True, methods=['post'], url_path='incoming/ensure-docker')
    def incoming_ensure_docker(self, request, pk=None):
        """Check if Docker is installed on this node (always true for containerized backend)."""
        transfer = self.get_object()
        if not self._incoming_auth_required(request, transfer):
            return Response({'error': 'Invalid HMAC signature'}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            client.ping()
            return Response({'docker_available': True})
        except Exception:
            logger.exception("Docker health check failed")
            return Response({'docker_available': False, 'error': 'An internal error occurred. Please check server logs.'})

    @action(detail=True, methods=['post'], url_path='incoming/upload-file')
    def incoming_upload_file(self, request, pk=None):
        """Receive a file upload and write it to a path on this node.

        Used by FULL transfers to ship the backup archive to the target.
        Expects JSON body: {path, content_base64} or raw binary with
        X-Transfer-Path header.
        """
        transfer = self.get_object()
        if not self._incoming_auth_required(request, transfer):
            return Response({'error': 'Invalid HMAC signature'}, status=status.HTTP_401_UNAUTHORIZED)

        import base64

        content_base64 = request.data.get('content_base64') if isinstance(request.data, dict) else None
        dest_path = (request.data.get('path') if isinstance(request.data, dict) else None) or \
                    request.headers.get('X-Transfer-Path', '')

        if not dest_path:
            return Response({'error': 'path is required'}, status=status.HTTP_400_BAD_REQUEST)

        # Security: only allow writes to /tmp/
        if not dest_path.startswith('/tmp/'):
            return Response({'error': 'Only /tmp/ paths are allowed'}, status=status.HTTP_400_BAD_REQUEST)

        if content_base64:
            try:
                raw = base64.b64decode(content_base64)
            except Exception:
                return Response({'error': 'Invalid base64 content'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            raw = request.body

        if not raw or len(raw) < 10:
            return Response({'error': 'Empty or invalid file data'}, status=status.HTTP_400_BAD_REQUEST)

        offset = 0
        chunk_index = 0
        append_flag = False

        if isinstance(request.data, dict):
            try:
                offset = int(request.data.get('offset', 0) or 0)
            except (ValueError, TypeError):
                offset = 0
            try:
                chunk_index = int(request.data.get('chunk_index', 0) or request.data.get('chunk', 0) or 0)
            except (ValueError, TypeError):
                chunk_index = 0
            append_flag = bool(request.data.get('append', False))

        try:
            header_offset = int(request.headers.get('X-Chunk-Offset', 0) or 0)
            if header_offset > 0:
                offset = header_offset
        except (ValueError, TypeError):
            pass

        try:
            header_chunk = int(request.headers.get('X-Chunk-Index', 0) or request.headers.get('X-Chunk', 0) or 0)
            if header_chunk > 0:
                chunk_index = header_chunk
        except (ValueError, TypeError):
            pass

        if str(request.headers.get('X-Append', '')).lower() in ('true', '1', 'yes'):
            append_flag = True

        mode = 'ab' if (offset > 0 or chunk_index > 0 or append_flag) else 'wb'

        try:
            os.makedirs(os.path.dirname(dest_path) or '/tmp', exist_ok=True)
            with open(dest_path, mode) as f:
                f.write(raw)
            return Response({'status': 'written', 'path': dest_path, 'size': len(raw), 'mode': mode})
        except Exception:
            logger.exception("Incoming write-file failed")
            return Response({'error': 'An internal error occurred. Please check server logs.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='incoming/db-backup')
    def incoming_db_backup(self, request):
        """
        Receive and store a master DB backup on this node.
        Used for disaster recovery — if the master goes down, this node
        can help restore the database on a replacement master.

        Expects raw binary body (Content-Type: application/gzip) containing
        a gzipped pg_dump. Stored at DB_BACKUP_DIR with a timestamp.
        """
        if not self._incoming_auth_required(request, None):
            return Response({'error': 'Invalid HMAC signature'}, status=status.HTTP_401_UNAUTHORIZED)

        raw_body = request.body
        if not raw_body or len(raw_body) < 100:
            return Response({'error': 'Empty or invalid backup data'}, status=status.HTTP_400_BAD_REQUEST)

        backup_dir = getattr(settings, 'DB_BACKUP_DIR', '/opt/smsly-hosting/backups/master-db')
        try:
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
            dest_path = os.path.join(backup_dir, f'master_db_{timestamp}.sql.gz')

            with open(dest_path, 'wb') as f:
                f.write(raw_body)

            # Keep only the 5 most recent backups
            existing = sorted(
                [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith('.sql.gz')],
                key=os.path.getmtime,
            )
            while len(existing) > 5:
                old = existing.pop(0)
                with contextlib.suppress(OSError):
                    os.remove(old)

            return Response({
                'status': 'stored',
                'path': dest_path,
                'size_bytes': len(raw_body),
            })
        except Exception:
            logger.exception("Incoming tar-upload failed")
            return Response({'error': 'An internal error occurred. Please check server logs.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='incoming/db-backup/status')
    def incoming_db_backup_status(self, request):
        """Return info about the latest stored master DB backup on this node."""
        if not self._incoming_auth_required(request, None):
            return Response({'error': 'Invalid HMAC signature'}, status=status.HTTP_401_UNAUTHORIZED)

        backup_dir = getattr(settings, 'DB_BACKUP_DIR', '/opt/smsly-hosting/backups/master-db')
        try:
            backups = sorted(
                [f for f in os.listdir(backup_dir) if f.endswith('.sql.gz')],
                key=lambda f: os.path.getmtime(os.path.join(backup_dir, f)),
                reverse=True,
            )
            latest = backups[0] if backups else None
            latest_path = os.path.join(backup_dir, latest) if latest else None
            return Response({
                'backups_available': len(backups),
                'latest': latest,
                'latest_size_bytes': os.path.getsize(latest_path) if latest_path and os.path.exists(latest_path) else 0,
                'backup_dir': backup_dir,
            })
        except Exception:
            logger.exception("Incoming list-backups failed")
            return Response({'backups_available': 0, 'error': 'An internal error occurred. Please check server logs.'})
