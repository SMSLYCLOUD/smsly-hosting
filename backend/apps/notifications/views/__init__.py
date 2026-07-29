from django.core.mail import send_mail
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.decorators import permission_classes as perms
from rest_framework.response import Response

from ..models import AlertRule, Notification, NotificationChannel, NotificationPreference, ResourceAlert
from ..serializers import (
    AlertRuleSerializer,
    NotificationChannelSerializer,
    NotificationPreferenceSerializer,
    NotificationSerializer,
    ResourceAlertSerializer,
)


class ResourceAlertViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ResourceAlertSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = ResourceAlert.objects.filter(service__owner=self.request.user, acknowledged=False).order_by('-created_at')
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
        return Notification.objects.filter(user=self.request.user).order_by('-created_at')

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
        return NotificationPreference.objects.filter(user=self.request.user).order_by('id')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class NotificationChannelViewSet(viewsets.ModelViewSet):
    """CRUD for notification delivery channels (email, Slack, SMS, webhook)."""
    serializer_class = NotificationChannelSerializer
    permission_classes = [permissions.IsAdminUser]
    queryset = NotificationChannel.objects.all()

    @action(detail=True, methods=['post'])
    def test(self, request, pk=None):
        """Send a test notification through this channel."""
        channel = self.get_object()
        if not channel.enabled:
            return Response({'error': 'Channel is disabled'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            if channel.channel_type == 'email':
                from apps.deployments.models.core import PlatformConfig
                config = PlatformConfig.load()
                if not config.smtp_host or not config.smtp_from_email:
                    return Response(
                        {'error': 'SMTP is not configured. Set SMTP settings in Platform Settings first.'},
                        status=status.HTTP_400_BAD_REQUEST)
                send_mail(
                    subject='[SMSLY] Test Alert Notification',
                    message='This is a test alert notification from your SMSLY platform. If you received this, your email channel is working correctly.',
                    from_email=f"{config.smtp_from_name} <{config.smtp_from_email}>",
                    recipient_list=[channel.target],
                    fail_silently=False,
                )
            elif channel.channel_type == 'slack':
                import json
                import urllib.request
                payload = json.dumps({
                    'text': ':white_check_mark: *SMSLY Test Alert*\nThis is a test notification from your SMSLY hosting platform.'
                }).encode()
                req = urllib.request.Request(
                    channel.target,
                    data=payload,
                    headers={'Content-Type': 'application/json'},
                    method='POST',
                )
                urllib.request.urlopen(req, timeout=10)
            elif channel.channel_type == 'webhook':
                import json
                import urllib.request
                payload = json.dumps({
                    'event': 'test',
                    'message': 'SMSLY test alert notification',
                }).encode()
                req = urllib.request.Request(
                    channel.target,
                    data=payload,
                    headers={'Content-Type': 'application/json'},
                    method='POST',
                )
                urllib.request.urlopen(req, timeout=10)
            elif channel.channel_type == 'sms':
                return Response(
                    {'error': 'SMS test not yet implemented. Configure the SMSLY SMS API in platform settings.'},
                    status=status.HTTP_400_BAD_REQUEST)

            return Response({'status': 'ok', 'message': f'Test notification sent via {channel.channel_type}'})
        except Exception as e:
            return Response(
                {'error': f'Failed to send test notification: {e!s}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AlertRuleViewSet(viewsets.ModelViewSet):
    """CRUD for platform-wide alert rules."""
    serializer_class = AlertRuleSerializer
    permission_classes = [permissions.IsAdminUser]
    queryset = AlertRule.objects.all()

    @action(detail=True, methods=['post'])
    def toggle(self, request, pk=None):
        """Toggle a rule on/off."""
        rule = self.get_object()
        rule.enabled = not rule.enabled
        rule.save(update_fields=['enabled', 'updated_at'])
        return Response({'enabled': rule.enabled})


@api_view(['POST'])
@perms([permissions.IsAdminUser])
def test_smtp(request):
    """Send a test email using the current SMTP configuration."""
    from apps.deployments.models.core import PlatformConfig
    config = PlatformConfig.load()

    if not config.smtp_host:
        return Response(
            {'error': 'SMTP host is not configured'},
            status=status.HTTP_400_BAD_REQUEST)

    to_email = request.data.get('to_email', '')
    if not to_email:
        return Response(
            {'error': 'to_email is required'},
            status=status.HTTP_400_BAD_REQUEST)

    try:
        send_mail(
            subject='[SMSLY] SMTP Configuration Test',
            message=(
                'Your SMTP configuration is working correctly.\n\n'
                f'Server: {config.smtp_host}:{config.smtp_port}\n'
                f'From: {config.smtp_from_name} <{config.smtp_from_email}>\n'
                f'TLS: {"Enabled" if config.smtp_use_tls else "Disabled"}'
            ),
            from_email=f"{config.smtp_from_name} <{config.smtp_from_email}>",
            recipient_list=[to_email],
            fail_silently=False,
        )
        return Response({'status': 'ok', 'message': f'Test email sent to {to_email}'})
    except Exception as e:
        return Response(
            {'error': f'Failed to send test email: {e!s}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
