from rest_framework import serializers
from .models import Notification, NotificationPreference, ResourceAlert

class ResourceAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceAlert
        fields = '__all__'

class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = '__all__'

class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = '__all__'
