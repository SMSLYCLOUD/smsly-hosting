"""Blueprint Manager module."""
import json
import logging
import re
import time

from django.conf import settings

from apps.cloud.models import CloudProvider
from apps.cloud.services.build_constants import is_secret_env_var
from apps.deployments.models import Deployment, EnvironmentVariable, Project, Service
from apps.deployments.models.addons import Addon
from apps.deployments.utils.env_sanitizer import sanitize_env_value
from apps.addons.tasks.crud import provision_addon_task
from apps.deployments.tasks.deploy.queue import enqueue_smart_deploy_task

logger = logging.getLogger(__name__)

# How long to wait for async addon provisioning before creating services.
ADDON_PROVISION_TIMEOUT_SECONDS = 300
ADDON_PROVISION_POLL_SECONDS = 5


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

    def _unique_service_name(self, base: str) -> str:
        """Return a non-colliding service name (unique=True on the model)."""
        name = base
        counter = 1
        while Service.objects.filter(name=name).exists():
            counter += 1
            name = f"{base}-{counter}"
        return name

    def _validate_blueprint(self, data: dict) -> None:
        if not isinstance(data, dict) or not data.get("name"):
            raise ValueError("Invalid blueprint: missing 'name'")
        services = data.get("services")
        if not isinstance(services, list) or not services:
            raise ValueError("Invalid blueprint: 'services' must be a non-empty list")
        for svc in services:
            if not isinstance(svc, dict) or not svc.get("name") or not svc.get("image"):
                raise ValueError(
                    "Invalid blueprint: each service needs 'name' and 'image'"
                )
            env = svc.get("env", {})
            if not isinstance(env, dict):
                raise ValueError(
                    f"Invalid blueprint: service {svc.get('name')!r} 'env' must be a mapping"
                )

    def _wait_for_addons(self, addons: list) -> dict:
        """Poll async provisioning; return {DATABASE_URL, REDIS_URL} context.

        Fails fast on timeout or FAILED addons instead of deploying
        services with empty connection strings (which previously shipped
        DATABASE_URL='' and crashed every service at boot).
        """
        deadline = time.monotonic() + ADDON_PROVISION_TIMEOUT_SECONDS
        pending = {str(a.id): a for a in addons}
        context: dict = {}
        while pending and time.monotonic() < deadline:
            time.sleep(ADDON_PROVISION_POLL_SECONDS)
            for addon_id in list(pending):
                addon = Addon.objects.filter(id=addon_id).first()
                if addon is None:
                    pending.pop(addon_id)
                    continue
                if addon.status == Addon.Status.ACTIVE and addon.connection_url:
                    if addon.addon_type == "POSTGRES" and "DATABASE_URL" not in context:
                        context["DATABASE_URL"] = addon.connection_url
                    elif addon.addon_type == "REDIS" and "REDIS_URL" not in context:
                        context["REDIS_URL"] = addon.connection_url
                    pending.pop(addon_id)
                elif addon.status == Addon.Status.FAILED:
                    raise ValueError(
                        f"Addon {addon.name} ({addon.addon_type}) failed to provision — "
                        "aborting blueprint deploy"
                    )
        if pending:
            names = ", ".join(a.name for a in pending.values())
            raise ValueError(
                f"Addon provisioning timed out after "
                f"{ADDON_PROVISION_TIMEOUT_SECONDS}s for: {names}"
            )
        return context

    def deploy(self, blueprint_name: str):
        data = self.load_blueprint(blueprint_name)
        self._validate_blueprint(data)
        logger.info(f"Deploying blueprint: {data['name']}")

        # Anchor everything to a real project so containers land on the
        # project-scoped bridge (previously services had project=None and
        # fell back to plain smsly-net, breaking addon DNS).
        project, _ = Project.objects.get_or_create(
            owner=self.user,
            slug=f"blueprint-{blueprint_name}",
            defaults={"name": f"Blueprint: {data['name']}"},
        )

        # 1. Create services FIRST (addons require a service FK — the old
        # service=None crashed with IntegrityError on every deploy).
        services: list = []
        for service_def in data["services"]:
            base_name = f"{service_def['name']}-{self.user.username}"
            service = Service.objects.create(
                name=self._unique_service_name(base_name),
                deploy_type="DOCKER",
                docker_image=service_def["image"],
                internal_port=int(service_def.get("port") or 8000),
                provider=self.provider,
                owner=self.user,
                project=project,
                env_scan_depth="shallow",
            )
            services.append((service, service_def))
        if not services:
            raise ValueError("Invalid blueprint: no services to deploy")
        anchor = services[0][0]

        # 2. Provision addons attached to the anchor service.
        addons: list = []
        for addon_def in data.get("addons", []):
            addon_type = addon_def.get("type")
            if addon_type not in dict(Addon.Type.choices):
                raise ValueError(f"Invalid blueprint addon type: {addon_type!r}")
            base_name = f"{addon_def.get('name', addon_type.lower())}-{self.user.username}"
            name = base_name
            counter = 1
            while Addon.objects.filter(name=name).exists():
                counter += 1
                name = f"{base_name}-{counter}"
            addon = Addon.objects.create(
                service=anchor,
                project=project,
                name=name,
                addon_type=addon_type,
                status=Addon.Status.PROVISIONING,
            )
            provision_addon_task.delay(str(addon.id))
            addons.append(addon)

        # 3. Wait for real connection strings (no placeholders — the old
        # ALLOW_BLUEPRINT_PLACEHOLDERS path shipped user:pass@ URLs).
        context: dict = {}
        if addons:
            context = self._wait_for_addons(addons)

        # 4. Env vars (sanitized, secret-flagged) + deployments.
        for service, service_def in services:
            for key, value in (service_def.get("env") or {}).items():
                # Resolve placeholders against provisioned addons.
                if value == "${DATABASE_URL}":
                    value = context.get("DATABASE_URL", "")
                elif value == "${REDIS_URL}":
                    value = context.get("REDIS_URL", "")
                cleaned = sanitize_env_value(value, key=key, allow_empty=True)
                if cleaned is None or (isinstance(cleaned, str) and not cleaned.strip()):
                    logger.warning(
                        "Blueprint %s: omitting empty/placeholder env %s for %s",
                        blueprint_name, key, service.name,
                    )
                    continue
                EnvironmentVariable.objects.create(
                    service=service,
                    key=key,
                    value=cleaned,
                    is_secret=is_secret_env_var(key),
                    source="SYSTEM",
                )

            # commit_hash is required (no blank=True) — blueprints have no
            # git SHA, so record the image reference instead.
            image_ref = (service_def.get("image") or "")[:40] or "blueprint-deploy"
            deployment = Deployment.objects.create(
                service=service,
                commit_hash=image_ref,
                status=Deployment.Status.QUEUED,
                commit_message=f"Blueprint: {data['name']}",
                is_fast_deploy=True,
            )

            # Admin-triggered image deploys must not stall at REVIEW —
            # skip explicitly (message-based skipping is disabled).
            enqueue_smart_deploy_task(
                str(deployment.id), str(self.provider.id),
                skip_review=True, fast_deploy=True,
            )
            logger.info(f"Scheduled deployment for {service.name}")

        return True
