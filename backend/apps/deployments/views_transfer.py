from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models_transfer import ServerTransfer
from .serializers import ServerTransferSerializer, ServerTransferCreateSerializer
from .models import Service, PlatformConfig
from .tasks import execute_server_transfer_task, rollback_transfer_task


class ServerTransferViewSet(viewsets.ModelViewSet):
    queryset = ServerTransfer.objects.select_related('service').all()
    serializer_class = ServerTransferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(service__owner=self.request.user).order_by('-created_at')

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

        # Create transfer object
        transfer = ServerTransfer.objects.create(
            source_server_ip=source_server_ip,
            target_server_ip=payload['target_server_ip'],
            target_ssh_key=payload['target_ssh_key'],
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

        rollback_transfer_task.delay(str(transfer.id))
        return Response({'status': 'rollback_started'})
