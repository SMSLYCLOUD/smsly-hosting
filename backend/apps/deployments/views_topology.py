from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.decorators import action
from .services.topology import TopologyService

class TopologyViewSet(viewsets.ViewSet):
    def list(self, request):
        service = TopologyService()
        graph = service.build_graph()
        return Response(graph)
