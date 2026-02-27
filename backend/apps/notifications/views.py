from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Notification, NotificationPreference, ResourceAlert
from .serializers import NotificationSerializer, NotificationPreferenceSerializer, ResourceAlertSerializer

class ResourceAlertViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ResourceAlertSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = ResourceAlert.objects.filter(service__owner=self.request.user, acknowledged=False)
        service_id = self.request.query_params.get('service')
        if service_id:
            qs = qs.filter(service_id=service_id)
        return qs

    @action(detail=True, methods=['post'])
    def dismiss(self, request, pk=None):
        alert = self.get_object()
        alert.acknowledged = True
        alert.save()
        return Response({'status': 'dismissed'})

class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.read = True
        notification.save(update_fields=['read'])
        return Response({'status': 'ok'})

    @action(detail=False, methods=['POST'])
    def mark_all_read(self, request):
        self.get_queryset().update(read=True)
        return Response({'status': 'ok'})

class NotificationPreferenceViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationPreferenceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return NotificationPreference.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
