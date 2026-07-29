from rest_framework import serializers

from apps.deployments.models.transfer import ServerTransfer


class ServerTransferSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServerTransfer
        fields = [
            'id',
            'owner',
            'status',
            'source_server_ip',
            'source_server_id',
            'source_backup',
            'target_server_ip',
            'target_public_domain',
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
    source_server_id = serializers.UUIDField(required=False)
    source_ssh_key = serializers.CharField(
        write_only=True, trim_whitespace=False, required=False, default='', allow_blank=True,
    )
    source_ssh_password = serializers.CharField(
        write_only=True, required=False, default='', allow_blank=True,
    )
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
    target_public_domain = serializers.CharField(required=False, default='', allow_blank=True,
        help_text='New platform domain for cross-platform migration')

    def validate_target_ssh_key(self, value):
        if value and value.strip():
            key = value.strip()
            if not (key.startswith('-----BEGIN ') and 'PRIVATE KEY-----' in key.splitlines()[0]):
                raise serializers.ValidationError(
                    "Invalid SSH private key format. Must be a valid PEM-encoded private key "
                    "starting with '-----BEGIN ... PRIVATE KEY-----'."
                )
            if '-----END ' not in key or 'PRIVATE KEY-----' not in key.rsplit('\n', 2)[-2]:
                raise serializers.ValidationError(
                    "Invalid SSH private key format. Missing '-----END ... PRIVATE KEY-----' footer."
                )
        return value

    def validate(self, attrs):
        transfer_type = attrs.get('transfer_type')
        service_id = attrs.get('service_id')
        target_server_ip = attrs.get('target_server_ip')
        target_server_id = attrs.get('target_server_id')
        attrs.get('source_server_ip')
        source_server_id = attrs.get('source_server_id')

        if transfer_type == 'SERVICE' and not service_id:
            raise serializers.ValidationError(
                {'service_id': "service_id is required when transfer_type=SERVICE."}
            )
        if transfer_type == 'FULL' and service_id:
            raise serializers.ValidationError(
                {'service_id': "service_id must not be provided when transfer_type=FULL."}
            )

        if not target_server_ip and not target_server_id:
            from apps.deployments.models import PlatformConfig
            if not PlatformConfig.load().server_ip:
                raise serializers.ValidationError(
                    {'target_server_ip': "target_server_ip or target_server_id is required (local node IP not set)."}
                )

        # Determine if target is the local node (no SSH needed)
        target_is_local = not target_server_id and not target_server_ip

        # Require at least one SSH auth method for target (skip when target is local)
        has_key = bool(attrs.get('target_ssh_key', '').strip())
        has_password = bool(attrs.get('target_ssh_password', '').strip())
        target_server = None

        if target_server_id:
            from apps.deployments.models.servers import ManagedServer
            target_server = ManagedServer.objects.filter(id=target_server_id).first()
            if not target_server:
                raise serializers.ValidationError({'target_server_id': "Target server not found."})

            if target_server.status != ManagedServer.Status.ONLINE:
                raise serializers.ValidationError(
                    {'target_server_id': f"Target server '{target_server.name}' is currently {target_server.status}. Transfers are only allowed to ONLINE nodes."}
                )

        # SERVICE transfers use the REST API — no SSH needed
        if transfer_type == 'SERVICE':
            pass
        elif not target_is_local and not has_key and not has_password and not target_server:
            raise serializers.ValidationError(
                "No SSH credentials available for target server. Provide target_ssh_key, target_ssh_password, or select a target server with saved SSH credentials."
            )

        # Validate source SSH when source is a known remote server (FULL transfers only)
        if transfer_type != 'SERVICE' and source_server_id:
            from apps.deployments.models.servers import ManagedServer
            source_server = ManagedServer.objects.filter(id=source_server_id).first()
            if source_server:
                source_has_key = bool(attrs.get('source_ssh_key', '').strip())
                source_has_password = bool(attrs.get('source_ssh_password', '').strip())
                stored_key = (source_server.ssh_key or '').strip()
                stored_password = (source_server.ssh_password or '').strip()
                if not source_has_key and not source_has_password and not stored_key and not stored_password:
                    raise serializers.ValidationError(
                        {'source_ssh_key': "Source SSH credentials required for node-to-node transfer. Provide source_ssh_key/source_ssh_password or update the source server's stored credentials."}
                    )

        return attrs
