"""
Views for scoped Docker network configuration.
"""

from rest_framework import permissions, viewsets

from ..models.network_scope import ScopedNetwork
from ..serializers.network_scope import ScopedNetworkSerializer


class ScopedNetworkViewSet(viewsets.ModelViewSet):
    """CRUD for scoped Docker network configurations."""

    queryset = ScopedNetwork.objects.all().select_related("content_type")
    serializer_class = ScopedNetworkSerializer
    permission_classes = [permissions.IsAdminUser]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        scope_type = self.request.query_params.get("scope_type")
        object_id = self.request.query_params.get("object_id")
        if scope_type:
            qs = qs.filter(content_type__model=scope_type)
        if object_id:
            qs = qs.filter(object_id=object_id)
        return qs
