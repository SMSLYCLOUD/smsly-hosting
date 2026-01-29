from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from .models_cron import CronJob

class CronJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = CronJob
        fields = '__all__'

class CronJobViewSet(viewsets.ModelViewSet):
    serializer_class = CronJobSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return CronJob.objects.all()

    def perform_create(self, serializer):
        cron = serializer.save()
        # In prod: ClusterManager.create_cronjob(cron)
