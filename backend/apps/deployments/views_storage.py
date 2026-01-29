from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from .models_storage import Volume

class VolumeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Volume
        fields = '__all__'

class VolumeViewSet(viewsets.ModelViewSet):
    serializer_class = VolumeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Volume.objects.all()

    def perform_create(self, serializer):
        vol = serializer.save()
        # In prod: ClusterManager.create_pvc(vol)
