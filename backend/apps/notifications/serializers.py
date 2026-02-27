from rest_framework import serializers
from .models import Notification, NotificationPreference, ResourceAlert


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
