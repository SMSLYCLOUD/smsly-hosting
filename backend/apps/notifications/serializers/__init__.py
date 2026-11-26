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
    is_read = serializers.BooleanField(source='read', read_only=True)

    class Meta:
        model = Notification
        fields = (
            'id',
            'title',
            'message',
            'event_type',
            'read',
            'is_read',
            'created_at',
        )
        read_only_fields = ('id', 'title', 'message', 'event_type', 'is_read', 'created_at')


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    VALID_CHANNELS = {'email', 'sms', 'webhook', 'slack', 'discord', 'in_app'}

    class Meta:
        model = NotificationPreference
        fields = ('id', 'user', 'event_type', 'channels')
        read_only_fields = ('id', 'user')

    def validate_channels(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("channels must be a list of channel names.")
        cleaned = []
        for item in value:
            ch = str(item or '').strip().lower()
            if ch not in self.VALID_CHANNELS:
                raise serializers.ValidationError(f"Unsupported notification channel: {item!r}")
            if ch not in cleaned:
                cleaned.append(ch)
        return cleaned


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
