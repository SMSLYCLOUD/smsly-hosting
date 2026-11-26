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
        from apps.deployments.models import Service
        from apps.permissions.utils import get_accessible_q

        user = self.request.user
        accessible_services = Service.objects.filter(get_accessible_q(user))
        qs = ResourceAlert.objects.filter(
            service__in=accessible_services,
            acknowledged=False,
        ).select_related('service').distinct().order_by('-created_at')
        service_id = self.request.query_params.get('service')
        if service_id:
            qs = qs.filter(service_id=service_id)
        return qs

    @action(detail=True, methods=['post'])
    def dismiss(self, request, pk=None):
        from apps.teams.permissions import assert_can_write

        alert = self.get_object()
        assert_can_write(request.user, alert.service, action='dismiss resource alert')
        alert.acknowledged = True
        alert.save(update_fields=['acknowledged'])
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

class IsPlatformAdmin(permissions.BasePermission):
    """Allow Django staff or superusers."""

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


_DEFAULT_PREFERENCE_EVENTS = (
    ('deploy_success', ['in_app', 'email']),
    ('deploy_failed', ['in_app', 'email']),
    ('health_alert', ['in_app', 'email']),
    ('ssl_expiring', ['in_app', 'email']),
    ('backup_completed', ['in_app']),
)


class NotificationPreferenceViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationPreferenceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return NotificationPreference.objects.filter(user=self.request.user).order_by('id')

    def list(self, request, *args, **kwargs):
        if not NotificationPreference.objects.filter(user=request.user).exists():
            for event_type, default_channels in _DEFAULT_PREFERENCE_EVENTS:
                NotificationPreference.objects.get_or_create(
                    user=request.user,
                    event_type=event_type,
                    defaults={'channels': list(default_channels)},
                )
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        obj, _created = NotificationPreference.objects.update_or_create(
            user=self.request.user,
            event_type=serializer.validated_data['event_type'],
            defaults={'channels': serializer.validated_data.get('channels', [])},
        )
        serializer.instance = obj


class NotificationChannelViewSet(viewsets.ModelViewSet):
    """CRUD for notification delivery channels (email, Slack, SMS, webhook)."""
    serializer_class = NotificationChannelSerializer
    permission_classes = [IsPlatformAdmin]
    queryset = NotificationChannel.objects.all().order_by('name', 'id')

    @action(detail=True, methods=['post'])
    def test(self, request, pk=None):
        """Send a test notification through this channel."""
        channel = self.get_object()
        if not channel.enabled:
            return Response({'error': 'Channel is disabled'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            if channel.channel_type == 'email':
                from apps.deployments.models.core import PlatformConfig
                from apps.notifications.tasks import get_platform_smtp_connection
                config = PlatformConfig.load()
                if not config.smtp_host or not config.smtp_from_email:
                    return Response(
                        {'error': 'SMTP is not configured. Set SMTP settings in Platform Settings first.'},
                        status=status.HTTP_400_BAD_REQUEST)
                _host, from_email, connection = get_platform_smtp_connection()
                send_mail(
                    subject='[SMSLY] Test Alert Notification',
                    message='This is a test alert notification from your SMSLY platform. If you received this, your email channel is working correctly.',
                    from_email=from_email,
                    recipient_list=[channel.target],
                    fail_silently=False,
                    connection=connection,
                )
            elif channel.channel_type in ('slack', 'webhook'):
                # Route through the same SSRF-guarded webhook path used for
                # real dispatches (host allowlist + DNS checks + validate_ssrf).
                from ..webhooks import _post_notification
                if channel.channel_type == 'slack':
                    payload = {
                        'text': ':white_check_mark: *SMSLY Test Alert*\nThis is a test notification from your SMSLY hosting platform.'
                    }
                else:
                    payload = {
                        'event': 'test',
                        'message': 'SMSLY test alert notification',
                    }
                ok = _post_notification(channel.target, payload, provider=channel.channel_type)
                if not ok:
                    return Response(
                        {'error': 'Webhook target rejected or unreachable. Only http(s) URLs on the Slack/Discord allowlist are permitted.'},
                        status=status.HTTP_400_BAD_REQUEST)
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
    permission_classes = [IsPlatformAdmin]
    queryset = AlertRule.objects.all().order_by('name', 'id')

    @action(detail=True, methods=['post'])
    def toggle(self, request, pk=None):
        """Toggle a rule on/off."""
        rule = self.get_object()
        rule.enabled = not rule.enabled
        rule.save(update_fields=['enabled', 'updated_at'])
        return Response({'enabled': rule.enabled})


@api_view(['POST'])
@perms([IsPlatformAdmin])
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
        from apps.notifications.tasks import get_platform_smtp_connection
        _host, from_email, connection = get_platform_smtp_connection()
        send_mail(
            subject='[SMSLY] SMTP Configuration Test',
            message=(
                'Your SMTP configuration is working correctly.\n\n'
                f'Server: {config.smtp_host}:{config.smtp_port}\n'
                f'From: {config.smtp_from_name} <{config.smtp_from_email}>\n'
                f'TLS: {"Enabled" if config.smtp_use_tls else "Disabled"}'
            ),
            from_email=from_email,
            recipient_list=[to_email],
            fail_silently=False,
            connection=connection,
        )
        return Response({'status': 'ok', 'message': f'Test email sent to {to_email}'})
    except Exception as e:
        return Response(
            {'error': f'Failed to send test email: {e!s}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
