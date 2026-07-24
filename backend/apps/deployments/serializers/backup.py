from rest_framework import serializers

from ..models.backup import BackupSchedule, ServerBackup, ServiceBackup, ServiceSnapshot


class ServiceBackupSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceBackup
        fields = [
            'id', 'service', 'created_by', 'label', 'status', 'db_only',
            'backup_type', 'size_bytes', 'error_message',
            'created_at', 'completed_at',
            'cloud_uploaded', 'cloud_destination', 'cloud_bucket',
        ]
        read_only_fields = [
            'id', 'created_by', 'status', 'size_bytes', 'error_message',
            'created_at', 'completed_at', 'cloud_uploaded', 'cloud_bucket',
        ]


class ServerBackupSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServerBackup
        fields = [
            'id', 'label', 'status', 'db_only', 'size_bytes',
            'services_included', 'error_message',
            'created_at', 'completed_at',
            'cloud_uploaded', 'cloud_destination', 'cloud_bucket',
        ]
        read_only_fields = [
            'id', 'status', 'size_bytes', 'services_included',
            'error_message', 'created_at', 'completed_at',
            'cloud_uploaded', 'cloud_bucket',
        ]


class BackupScheduleSerializer(serializers.ModelSerializer):
    cloud_destination_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = BackupSchedule
        fields = '__all__'
        extra_kwargs = {
            's3_access_key': {'write_only': True},
            's3_secret_key': {'write_only': True},
        }

    def _apply_cloud_destination(self, validated_data):
        from ..models.cloud_storage import CloudStorageDestination
        cloud_destination_id = validated_data.pop('cloud_destination_id', None)
        if cloud_destination_id:
            try:
                dest = CloudStorageDestination.objects.get(id=cloud_destination_id)
                validated_data['storage_backend'] = 's3'
                validated_data['s3_bucket'] = dest.bucket
                validated_data['s3_region'] = dest.region
                validated_data['s3_endpoint'] = dest.endpoint
                validated_data['s3_access_key'] = dest.access_key
                validated_data['s3_secret_key'] = dest.secret_key
            except CloudStorageDestination.DoesNotExist:
                pass

    def create(self, validated_data):
        self._apply_cloud_destination(validated_data)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        self._apply_cloud_destination(validated_data)
        return super().update(instance, validated_data)

    def validate_s3_endpoint(self, value):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from ..models.backup import validate_endpoint_url
        try:
            validate_endpoint_url(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value


class SnapshotScheduleSerializer(serializers.ModelSerializer):
    cloud_destination_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)

    class Meta:
        from ..models.backup import SnapshotSchedule
        model = SnapshotSchedule
        fields = '__all__'
        extra_kwargs = {
            's3_access_key': {'write_only': True},
            's3_secret_key': {'write_only': True},
        }

    def create(self, validated_data):
        dest_id = validated_data.pop('cloud_destination_id', None)
        instance = super().create(validated_data)
        if dest_id:
            from ..models.cloud_storage import CloudStorageDestination
            dest = CloudStorageDestination.objects.filter(id=dest_id).first()
            if dest:
                dest.apply_to_schedule(instance)
        return instance

    def update(self, instance, validated_data):
        dest_id = validated_data.pop('cloud_destination_id', None)
        instance = super().update(instance, validated_data)
        if dest_id:
            from ..models.cloud_storage import CloudStorageDestination
            dest = CloudStorageDestination.objects.filter(id=dest_id).first()
            if dest:
                dest.apply_to_schedule(instance)
        elif 'cloud_destination_id' in self.initial_data and self.initial_data['cloud_destination_id'] is None:
            instance.storage_backend = 'local'
            instance.s3_bucket = ''
            instance.s3_access_key = ''
            instance.s3_secret_key = ''
            instance.s3_endpoint = ''
            instance.save()
        return instance


class ServiceSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceSnapshot
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'created_at', 'config_data', 'diff_summary', 'parent_snapshot']


class ServiceSnapshotRestoreSerializer(serializers.Serializer):
    target_service_id = serializers.UUIDField(required=False, allow_null=True)
    redeploy = serializers.BooleanField(default=False)


class ServiceSnapshotDiffSerializer(serializers.Serializer):
    compare_with_id = serializers.UUIDField(required=True)
