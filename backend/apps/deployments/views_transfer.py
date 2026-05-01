import ipaddress
import hashlib
import hmac
import json
import socket
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models_transfer import ServerTransfer
from .serializers import ServerTransferSerializer, ServerTransferCreateSerializer
from .models import Service, PlatformConfig
from .models_servers import ManagedServer
from .tasks import execute_server_transfer_task, rollback_transfer_task


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
        if ip.is_private and not allow_private:
            return False
        return True
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
    if not signature or not timestamp:
        return False

    try:
        request_ts = int(timestamp)
    except ValueError:
        return False

    if abs(int(time.time()) - request_ts) > 300:
        return False

    body_hash = hashlib.sha256(body).hexdigest()
    payload = f"{request.method}|{request.get_full_path()}|{timestamp}|{body_hash}"

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

    User = get_user_model()
    return User.objects.filter(is_superuser=True, is_active=True).first()


class ServerTransferViewSet(viewsets.ModelViewSet):
    queryset = ServerTransfer.objects.select_related('service').all()
    serializer_class = ServerTransferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.action == 'register_incoming':
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

        if not request.user.is_authenticated and not _verify_transfer_sync_hmac(request, source_ip, raw_body):
            return Response(
                {'error': 'Valid node authentication is required.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        owner = _resolve_incoming_owner(request, source_ip)

        # Check if a similar incoming transfer already exists
        existing = ServerTransfer.objects.filter(
            source_node_id=source_ip,
            target_server_ip=target_ip,
            owner=owner,
            status__in=['PREPARING', 'UPLOADING', 'RESTORING']
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

    def create(self, request, *args, **kwargs):
        import logging
        logger = logging.getLogger(__name__)
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
        if transfer_type == 'FULL':
            logger.warning("Transfer failed: FULL transfer not implemented yet.")
            return Response(
                {'error': 'FULL server transfer is not available via API yet.'},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )

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

        source_server_ip = payload.get('source_server_ip')
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

        target_ssh_key = str(payload.get('target_ssh_key') or '').strip()
        target_ssh_password = str(payload.get('target_ssh_password') or '').strip()
        if not target_ssh_key and not target_ssh_password and target_server:
            target_ssh_key = str(target_server.ssh_key or '').strip()
            target_ssh_password = str(target_server.ssh_password or '').strip()

        if not target_ssh_key and not target_ssh_password:
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

        # Create transfer object
        transfer = ServerTransfer.objects.create(
            source_server_ip=source_server_ip,
            target_server_ip=target_server_ip,
            target_ssh_key=target_ssh_key,
            target_ssh_password=target_ssh_password,
            transfer_type=transfer_type,
            service=service,
            owner=request.user,
        )

        execute_server_transfer_task.delay(str(transfer.id))

        return Response(ServerTransferSerializer(transfer).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def rollback(self, request, pk=None):
        transfer = self.get_object()
        if not transfer.can_rollback:
            return Response({'error': 'Rollback not available'}, status=status.HTTP_400_BAD_REQUEST)

        rollback_transfer_task.delay(transfer_id=str(transfer.id))
        return Response({'status': 'rollback_started'})
