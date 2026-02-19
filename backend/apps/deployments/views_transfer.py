from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models_transfer import ServerTransfer
from .serializers import ServerTransferSerializer, ServerTransferCreateSerializer
from .tasks import execute_server_transfer_task, rollback_transfer_task

class ServerTransferViewSet(viewsets.ModelViewSet):
    serializer_class = ServerTransferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ServerTransfer.objects.filter(service__owner=self.request.user).order_by('-created_at')

    def create(self, request, *args, **kwargs):
        serializer = ServerTransferCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Create transfer object
        # Note: encrypt target_ssh_key in real impl
        transfer = ServerTransfer.objects.create(
            target_server_ip=serializer.validated_data['target_server_ip'],
            target_ssh_key=serializer.validated_data['target_ssh_key'],
            transfer_type=serializer.validated_data['transfer_type'],
            service_id=serializer.validated_data.get('service_id')
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
