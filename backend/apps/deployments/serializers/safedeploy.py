import re

from rest_framework import serializers

from ..models.safedeploy import (
    DatabaseClone,
    DeploymentApproval,
    DeploymentArtifact,
    MigrationValidation,
    PreviewEnvironment,
)


class PreviewCreateSerializer(serializers.Serializer):
    branch_name = serializers.CharField(max_length=255, min_length=1, trim_whitespace=True)
    commit_sha = serializers.CharField(max_length=64, min_length=7)

    def validate_branch_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("branch_name must not be blank")
        if re.search(r'[^a-zA-Z0-9._\-/]', value):
            raise serializers.ValidationError("branch_name contains invalid characters")
        return value

    def validate_commit_sha(self, value):
        if not re.match(r'^[0-9a-fA-F]{7,64}$', value):
            raise serializers.ValidationError("commit_sha must be a valid hex SHA (7-64 characters)")
        return value


class PreviewRebuildSerializer(serializers.Serializer):
    commit_sha = serializers.CharField(max_length=64, min_length=7, required=False)

    def validate_commit_sha(self, value):
        if not re.match(r'^[0-9a-fA-F]{7,64}$', value):
            raise serializers.ValidationError("commit_sha must be a valid hex SHA (7-64 characters)")
        return value


class ApprovalApproveSerializer(serializers.Serializer):
    pass


class ApprovalRejectSerializer(serializers.Serializer):
    notes = serializers.CharField(max_length=2000, required=False, allow_blank=True, trim_whitespace=True)


class ApprovalCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeploymentApproval
        fields = ['service', 'deployment', 'preview_environment', 'requested_by', 'risk_level', 'approval_notes']


class DatabaseCloneSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatabaseClone
        fields = '__all__'


class MigrationValidationSerializer(serializers.ModelSerializer):
    class Meta:
        model = MigrationValidation
        fields = '__all__'


class DeploymentArtifactSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeploymentArtifact
        fields = '__all__'


class DeploymentApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeploymentApproval
        fields = '__all__'


class PreviewEnvironmentSerializer(serializers.ModelSerializer):
    database_clone = DatabaseCloneSerializer(read_only=True)
    migration_validation = MigrationValidationSerializer(read_only=True)
    artifacts = DeploymentArtifactSerializer(many=True, read_only=True)

    class Meta:
        model = PreviewEnvironment
        fields = '__all__'
