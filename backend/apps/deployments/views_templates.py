from rest_framework import serializers, viewsets
from .models_templates import Template

class TemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Template
        fields = '__all__'

class TemplateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Template.objects.all()
    serializer_class = TemplateSerializer

    def get_queryset(self):
        # Allow pre-populating dummy data for MVP if table is empty
        if not Template.objects.exists():
            Template.objects.create(
                name="Django Starter",
                slug="django-starter",
                description="Production-ready Django template with Postgres.",
                repository_url="https://github.com/smsly/django-starter",
                default_port=8000,
                icon_url="https://static.djangoproject.com/img/logos/django-logo-negative.png"
            )
            Template.objects.create(
                name="Node.js Express",
                slug="node-starter",
                description="Simple Express.js server.",
                repository_url="https://github.com/smsly/node-starter",
                default_port=3000,
                icon_url="https://nodejs.org/static/images/logo.svg"
            )
        return super().get_queryset()
