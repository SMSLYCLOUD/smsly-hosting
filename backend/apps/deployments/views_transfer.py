import ipaddress
import socket
import requests

from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models_transfer import ServerTransfer
from .serializers import ServerTransferSerializer, ServerTransferCreateSerializer
from .models import Service, PlatformConfig
from .models_servers import ManagedServer
from .tasks import execute_server_transfer_task, rollback_transfer_task


def is_safe_ip(ip_str):
    """
    Check if an IP is safe for public transfer requests.
    Blocks private, loopback, and reserved ranges to prevent SSRF.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        # Block private, loopback, link-local, multicast, and reserved ranges
        if (ip.is_private or ip.is_loopback or ip.is_link_local or
            ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False
        return True
    except ValueError:
        return False


class ServerTransferViewSet(viewsets.ModelViewSet):
    queryset = ServerTransfer.objects.select_related('service').all()
    serializer_class = ServerTransferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(service__owner=self.request.user).order_by('-created_at')

    @action(detail=False, methods=['post'], url_path='register-incoming')
    def register_incoming(self, request):
        """
        Internal endpoint: Source node notifies target node of an incoming transfer.
        This allows the target dashboard to show the transfer status.
        """
        source_ip = request.data.get('source_ip')
        target_ip = request.data.get('target_ip')
        transfer_type = request.data.get('transfer_type', 'SERVICE')
        service_name = request.data.get('service_name')

        # Check if a similar incoming transfer already exists
        existing = ServerTransfer.objects.filter(
            source_node_id=source_ip,
            target_server_ip=target_ip,
            status__in=['PREPARING', 'UPLOADING', 'RESTORING']
        ).first()

        if existing:
            return Response({'id': str(existing.id), 'status': existing.status})

        transfer = ServerTransfer.objects.create(
            status='PREPARING',
            source_server_ip=source_ip,
            target_server_ip=target_ip,
            transfer_type=transfer_type,
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
        logger.info(f"DEBUG: Transfer request received. Data: {request.data}")
        
        serializer = ServerTransferCreateSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning(f"DEBUG: Transfer validation failed. Errors: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        payload = serializer.validated_data

        transfer_type = payload['transfer_type']
        if transfer_type == 'FULL':
            return Response(
                {'error': 'FULL server transfer is not available via API yet.'},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )

        service = Service.objects.filter(
            id=payload.get('service_id'),
            owner=request.user,
        ).first()
        if not service:
            return Response(
                {'error': 'Service not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        source_server_ip = payload.get('source_server_ip')
        if not source_server_ip:
            source_server_ip = PlatformConfig.load().server_ip
        
        # Fallback: Auto-detect local IP if still missing
        if not source_server_ip:
            try:
                # Try to get public IP via common check service
                source_server_ip = requests.get('https://api.ipify.org', timeout=5).text.strip()
            except Exception:
                # Local network fallback
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    source_server_ip = s.getsockname()[0]
                    s.close()
                except Exception:
                    source_server_ip = None

        if not source_server_ip:
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
                return Response(
                    {'error': 'Connected target server not found'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        target_server_ip = payload.get('target_server_ip') or (
            target_server.host if target_server else PlatformConfig.load().server_ip
        )
        target_server_ip = str(target_server_ip or '').strip()
        if not target_server_ip:
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
        if not is_safe_ip(target_server_ip):
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
