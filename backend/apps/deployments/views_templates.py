from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from services.app_templates import list_templates, get_template

class TemplateViewSet(viewsets.ViewSet):
    """
    ViewSet for listing available App Templates from the registry.
    """
    permission_classes = [IsAuthenticated]

    def list(self, request):
        """
        List all available templates.
        """
        category = request.query_params.get('category')
        templates = list_templates(category)

        # Serialize dataclasses to dicts
        data = [
            {
                'id': t.id,
                'name': t.name,
                'description': t.description,
                'category': t.category,
                'docker_image': t.docker_image,
                'default_port': t.default_port,
                'env_vars': t.env_vars,
                'volumes': t.volumes,
                'docs_url': t.docs_url,
                'health_check': t.health_check
            }
            for t in templates
        ]
        return Response(data)

    def retrieve(self, request, pk=None):
        """
        Get a specific template by ID.
        """
        template = get_template(pk)
        if not template:
            return Response({'error': 'Template not found'}, status=status.HTTP_404_NOT_FOUND)

        data = {
            'id': template.id,
            'name': template.name,
            'description': template.description,
            'category': template.category,
            'docker_image': template.docker_image,
            'default_port': template.default_port,
            'env_vars': template.env_vars,
            'volumes': template.volumes,
            'docs_url': template.docs_url,
            'health_check': template.health_check
        }
        return Response(data)
