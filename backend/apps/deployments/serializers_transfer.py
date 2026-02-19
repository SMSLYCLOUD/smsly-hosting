from rest_framework import serializers
from .models_transfer import ServerTransfer


class ServerTransferSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServerTransfer
        fields = [
            'id',
            'status',
            'source_server_ip',
            'source_backup',
            'target_server_ip',
            'transfer_type',
            'service',
            'progress_percent',
            'current_step',
            'logs',
            'error_message',
            'created_at',
            'completed_at',
            'estimated_downtime_seconds',
            'can_rollback',
            'rollback_deadline',
        ]
        read_only_fields = [
            'id',
            'status',
            'progress_percent',
            'current_step',
            'logs',
            'error_message',
            'created_at',
            'completed_at',
            'can_rollback',
            'rollback_deadline',
            'source_backup',
        ]


class ServerTransferCreateSerializer(serializers.Serializer):
    source_server_ip = serializers.IPAddressField(required=False)
    target_server_ip = serializers.IPAddressField()
    target_ssh_key = serializers.CharField(write_only=True, trim_whitespace=False)
    transfer_type = serializers.ChoiceField(choices=['SERVICE', 'FULL'])
    service_id = serializers.UUIDField(required=False)

    def validate_target_ssh_key(self, value):
        if not value.strip():
            raise serializers.ValidationError("SSH private key is required.")
        # Basic sanity check for common PEM/OpenSSH key formats.
        if "BEGIN" not in value:
            raise serializers.ValidationError("Invalid SSH private key format.")
        return value

    def validate(self, attrs):
        transfer_type = attrs.get('transfer_type')
        service_id = attrs.get('service_id')

        if transfer_type == 'SERVICE' and not service_id:
            raise serializers.ValidationError(
                {'service_id': "service_id is required when transfer_type=SERVICE."}
            )
        if transfer_type == 'FULL' and service_id:
            raise serializers.ValidationError(
                {'service_id': "service_id must not be provided when transfer_type=FULL."}
            )
        return attrs
