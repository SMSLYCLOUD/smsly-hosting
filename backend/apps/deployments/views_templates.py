"""Views Templates module."""
import json
import os
from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny


class TemplateViewSet(viewsets.ViewSet):
    """
    Returns a list of predefined application templates.
    """
    permission_classes = [AllowAny]

    def list(self, request):
        # Load from fixtures
        try:
            path = os.path.join(settings.BASE_DIR,
                                'apps/deployments/fixtures/templates.json')
            with open(path, 'r') as f:
                data = json.load(f)

            category = request.query_params.get('category')
            search = request.query_params.get('search')

            if category:
                data = [t for t in data if t.get('category') == category]

            if search:
                search = search.lower()
                data = [
                    t for t in data if search in t.get(
                        'name', '').lower() or search in t.get(
                        'description', '').lower()]

            return Response(data)
        except Exception as e:
            return Response({'error': str(e)},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def retrieve(self, request, pk=None):
        try:
            path = os.path.join(settings.BASE_DIR,
                                'apps/deployments/fixtures/templates.json')
            with open(path, 'r') as f:
                data = json.load(f)

            template = next((t for t in data if t['id'] == pk), None)
            if template:
                return Response(template)
            return Response(status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
