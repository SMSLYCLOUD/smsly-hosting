"""Blueprint Manager module."""
import json
import logging
import os
import re

from django.conf import settings

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, EnvironmentVariable, Service
from apps.deployments.models.addons import Addon
from apps.addons.tasks.crud import provision_addon_task
from apps.deployments.tasks.deployment.tasks_deploy import enqueue_smart_deploy_task

logger = logging.getLogger(__name__)


class BlueprintManager:
    def __init__(self, provider: CloudProvider, user):
        self.provider = provider
        self.user = user

    def load_blueprint(self, name: str):
        # Reject path traversal: only allow alphanumeric names with hyphens.
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            raise ValueError(f"Invalid blueprint name: {name}")
        path = os.path.join(settings.BASE_DIR, 'blueprints', f'{name}.json')
        with open(path) as f:
            return json.load(f)

    def deploy(self, blueprint_name: str):
        data = self.load_blueprint(blueprint_name)
        logger.info(f"Deploying blueprint: {data['name']}")

        context = {}  # Store resolved variables (e.g. DATABASE_URL)

        # 1. Provision Addons
        for addon_def in data.get('addons', []):
            addon = Addon.objects.create(
                service=None,  # Shared addons might not have a parent service initially
                # Unique name
                name=f"{addon_def['name']}-{self.user.username}",
                addon_type=addon_def['type'],
                status=Addon.Status.PROVISIONING
            )
            # Do not inject placeholder credentials; require real provision result
            if os.environ.get("ALLOW_BLUEPRINT_PLACEHOLDERS", "").lower() in {"1", "true", "yes", "on"}:
                if addon.addon_type == 'POSTGRES':
                    context['DATABASE_URL'] = f"postgres://user:pass@db-{addon.id}:5432/db"
                elif addon.addon_type == 'REDIS':
                    context['REDIS_URL'] = f"redis://redis-{addon.id}:6379/0"
            else:
                logger.warning(
                    "Skipping placeholder connection strings for addon %s; waiting for real provisioning result",
                    addon.addon_type,
                )

            # Trigger actual provision task
            provision_addon_task.delay(str(addon.id))

        # 2. Deploy Services (Topological sort simplified)
        # Assuming the JSON list order respects dependencies for now

        for service_def in data['services']:
            service = Service.objects.create(
                name=f"{service_def['name']}-{self.user.username}",
                deploy_type='DOCKER',
                docker_image=service_def['image'],
                internal_port=service_def['port'],
                provider=self.provider,
                owner=self.user
            )

            # Env Vars
            for key, value in service_def['env'].items():
                # Resolve placeholders
                if value == "${DATABASE_URL}":
                    value = context.get('DATABASE_URL', '')
                elif value == "${REDIS_URL}":
                    value = context.get('REDIS_URL', '')

                EnvironmentVariable.objects.create(
                    service=service,
                    key=key,
                    value=value
                )

            # Create Deployment
            deployment = Deployment.objects.create(
                service=service,
                status=Deployment.Status.QUEUED,
                commit_message=f"Blueprint: {data['name']}"
            )

            # Trigger
            enqueue_smart_deploy_task(str(deployment.id), str(self.provider.id))
            logger.info(f"Scheduled deployment for {service.name}")

        return True
