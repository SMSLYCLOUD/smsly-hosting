import ipaddress

from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.licensing.decorators import require_tier
from .models_transfer import ServerTransfer
from .serializers import ServerTransferSerializer, ServerTransferCreateSerializer
from .models import Service, PlatformConfig
from .models_servers import ManagedServer
from .tasks import execute_server_transfer_task, rollback_transfer_task


class ServerTransferViewSet(viewsets.ModelViewSet):
    queryset = ServerTransfer.objects.select_related('service').all()
    serializer_class = ServerTransferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(service__owner=self.request.user).order_by('-created_at')

    @require_tier('pro', 'enterprise')
    def create(self, request, *args, **kwargs):
        serializer = ServerTransferCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
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
        try:
            ipaddress.ip_address(target_server_ip)
        except ValueError:
            return Response(
                {
                    'error': (
                        'Target server host must be an IP address for transfer SSH. '
                        'Use an IP-based connected server or pass target_server_ip.'
                    )
                },
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
    @require_tier('pro', 'enterprise')
    def rollback(self, request, pk=None):
        transfer = self.get_object()
        if not transfer.can_rollback:
            return Response({'error': 'Rollback not available'}, status=status.HTTP_400_BAD_REQUEST)

        rollback_transfer_task.delay(str(transfer.id))
        return Response({'status': 'rollback_started'})
