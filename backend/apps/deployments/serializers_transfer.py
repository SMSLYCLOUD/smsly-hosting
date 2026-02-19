from rest_framework import serializers
from .models_transfer import ServerTransfer

class ServerTransferSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServerTransfer
        fields = '__all__'
        read_only_fields = [
            'id', 'status', 'progress_percent', 'current_step', 'logs',
            'error_message', 'created_at', 'completed_at',
            'can_rollback', 'rollback_deadline'
        ]

class ServerTransferCreateSerializer(serializers.Serializer):
    target_server_ip = serializers.IPAddressField()
    target_ssh_key = serializers.CharField(write_only=True)
    transfer_type = serializers.ChoiceField(choices=['SERVICE', 'FULL'])
    service_id = serializers.UUIDField(required=False)
