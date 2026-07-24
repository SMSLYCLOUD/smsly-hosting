from rest_framework import serializers

from ..models import AlertRule, Notification, NotificationChannel, NotificationPreference, ResourceAlert


class ResourceAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceAlert
        fields = (
            'id',
            'service',
            'severity',
            'metric',
            'threshold',
            'current_value',
            'message',
            'acknowledged',
            'created_at',
        )
        read_only_fields = fields


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            'id',
            'title',
            'message',
            'event_type',
            'read',
            'created_at',
        )
        read_only_fields = ('id', 'title', 'message', 'event_type', 'created_at')


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = ('id', 'user', 'event_type', 'channels')
        read_only_fields = ('id', 'user')


class NotificationChannelSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationChannel
        fields = (
            'id', 'name', 'channel_type', 'target', 'enabled',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')


class AlertRuleSerializer(serializers.ModelSerializer):
    channels = serializers.PrimaryKeyRelatedField(
        many=True, queryset=NotificationChannel.objects.all(), required=False)

    class Meta:
        model = AlertRule
        fields = (
            'id', 'name', 'enabled', 'metric', 'operator', 'threshold',
            'severity', 'channels', 'cooldown_minutes', 'message_template',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')
