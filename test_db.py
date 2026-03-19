import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from apps.deployments.models import Service, EnvironmentVariable
from apps.deployments.services.pipeline import DeploymentPipeline

svc = Service.objects.get(name='ai-router-cc22a7a5')
p = DeploymentPipeline(svc.deployments.first())
print(p.env)
