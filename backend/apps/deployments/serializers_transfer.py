from rest_framework import serializers
from .models_transfer import ServerTransfer


class ServerTransferSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServerTransfer
        fields = [
            'id',
            'owner',
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
            'is_incoming',
            'source_node_id',
            'can_rollback',
            'rollback_deadline',
        ]
        read_only_fields = [
            'id',
            'owner',
            'status',
            'progress_percent',
            'current_step',
            'logs',
            'error_message',
            'created_at',
            'completed_at',
            'is_incoming',
            'source_node_id',
            'can_rollback',
            'rollback_deadline',
            'source_backup',
        ]


class ServerTransferCreateSerializer(serializers.Serializer):
    source_server_ip = serializers.IPAddressField(required=False)
    target_server_ip = serializers.IPAddressField(required=False)
    target_server_id = serializers.UUIDField(required=False)
    target_ssh_key = serializers.CharField(
        write_only=True, trim_whitespace=False, required=False, default='', allow_blank=True,
    )
    target_ssh_password = serializers.CharField(
        write_only=True, required=False, default='', allow_blank=True,
    )
    transfer_type = serializers.ChoiceField(choices=['SERVICE', 'FULL'])
    service_id = serializers.UUIDField(required=False)

    def validate_target_ssh_key(self, value):
        if value and value.strip():
            # Basic sanity check for common PEM/OpenSSH key formats.
            if "BEGIN" not in value:
                raise serializers.ValidationError("Invalid SSH private key format.")
        return value

    def validate(self, attrs):
        transfer_type = attrs.get('transfer_type')
        service_id = attrs.get('service_id')
        target_server_ip = attrs.get('target_server_ip')
        target_server_id = attrs.get('target_server_id')

        if transfer_type == 'SERVICE' and not service_id:
            raise serializers.ValidationError(
                {'service_id': "service_id is required when transfer_type=SERVICE."}
            )
        if transfer_type == 'FULL' and service_id:
            raise serializers.ValidationError(
                {'service_id': "service_id must not be provided when transfer_type=FULL."}
            )

        if not target_server_ip and not target_server_id:
            from .models import PlatformConfig
            if not PlatformConfig.load().server_ip:
                raise serializers.ValidationError(
                    {'target_server_ip': "target_server_ip or target_server_id is required (local node IP not set)."}
                )

        # Require at least one SSH auth method
        has_key = bool(attrs.get('target_ssh_key', '').strip())
        has_password = bool(attrs.get('target_ssh_password', '').strip())
        has_managed_target = bool(target_server_id)
        if not has_key and not has_password and not has_managed_target:
            raise serializers.ValidationError(
                "Provide target_ssh_key, target_ssh_password, or target_server_id with saved SSH credentials."
            )

        return attrs
