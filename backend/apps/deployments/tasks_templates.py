import logging

logger = logging.getLogger(__name__)
import json
import logging
import os
import secrets
import subprocess
from urllib.parse import urlparse

from celery import shared_task
from django.conf import settings
from services.addon_provisioner import addon_provisioner

from apps.cloud.models import CloudProvider
from apps.deployments.ai_router import (
    DEFAULT_AI_ROUTER_API_BASE,
    DEFAULT_AI_ROUTER_UI_BASE,
    DEFAULT_BRAID_ALIAS,
)
from apps.deployments.models import (
    Deployment,
    EnvironmentVariable,
    Service,
)
from apps.deployments.models_addons import Addon
from apps.deployments.utils import (
    append_log,
)

from .tasks_ai_router import _ensure_shared_ollama_cpp, _pull_ollama_models_into_shared


@shared_task(bind=True, max_retries=0, name="apps.deployments.tasks.one_click_deploy_template_task")
def one_click_deploy_template_task(self, service_id: str, template_id: str):
    """
    Background orchestration for template deployments.
    """
    # pylint: disable=unused-argument
    # Lazy import to break circular import chain:
    # tasks_templates → tasks_deploy → tasks
    from .tasks_deploy import enqueue_smart_deploy_task  # noqa: F811

    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        return

    # Load template
    template_path = os.path.join(
        settings.BASE_DIR, 'apps/deployments/fixtures/templates.json'
    )
    try:
        with open(template_path, encoding='utf-8') as f:
            templates = json.load(f)
        template = next((t for t in templates if t.get('id') == template_id), None)
    except Exception as exc: # pylint: disable=broad-exception-caught
        logger.exception("Exception reading template JSON: %s", exc)
        template = None

    def _verify_image_available(image: str):
        """
        Best-effort check: docker manifest inspect <image>.
        Skippable via SKIP_TEMPLATE_IMAGE_VERIFY=true.

        Has a 15s timeout to prevent blocking the celery worker if the
        registry is slow or unreachable. On timeout or error, log a
        warning and continue — the deploy itself will surface any
        real image-pull failure with a proper error message.
        """
        skip = os.environ.get("SKIP_TEMPLATE_IMAGE_VERIFY", "").lower() in {"1", "true", "yes", "on"}
        if skip or not image:
            return
        # Skip for private/credentialed registries that the worker
        # may not have credentials for. The actual deploy will handle
        # auth via the platform's docker config.
        if any(host in image.lower() for host in ('ghcr.io/smslycloud', 'localhost', '127.0.0.1', 'smsly-registry', 'registry:')):
            return
        try:
            result = subprocess.run(
                ["docker", "manifest", "inspect", image],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or f"manifest inspect failed for {image}")
        except subprocess.TimeoutExpired:
            logger.warning("docker manifest inspect timed out for %s (15s) — continuing", image)
        except FileNotFoundError as exc:  # docker not installed
            logger.warning("Docker not available to verify image %s: %s", image, exc)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Template image %s not available: %s", image, exc)
            raise

    # Provision addons
    required_addons = (template.get('required_addons') or []) if template else []

    # Honor template minimum RAM hints (e.g. Ollama models).
    if template:
        try:
            min_ram_gb = int(template.get("min_ram_gb") or 0)
        except (TypeError, ValueError):
            min_ram_gb = 0
        if min_ram_gb > 0:
            min_ram_mb = min_ram_gb * 1024
            try:
                current_mb = int(service.memory_mb or 0)
            except (TypeError, ValueError):
                current_mb = 0
            if current_mb < min_ram_mb:
                service.memory_mb = min_ram_mb
                service.save(update_fields=["memory_mb"])

    # Template-specific minimum requirements / defaults
    if template and template.get('id') == 'khoj':
        # Khoj requires pgvector; ensure Postgres addon is present
        if 'POSTGRES' not in required_addons:
            required_addons.append('POSTGRES')
    if template and template.get('id') == 'librechat':
        # LibreChat needs a JWT secret; inject default if missing
        env_list = template.setdefault('env_vars', [])
        has_jwt = any((str(ev.get('key') or '').upper() == 'JWT_SECRET') for ev in env_list)
        if not has_jwt:
            env_list.append({
                "key": "JWT_SECRET",
                "value": "${RANDOM_PASSWORD}",
                "is_secret": True
            })
        has_cfg = any((str(ev.get('key') or '').upper() == 'LIBRECHAT_CONFIG_PATH') for ev in env_list)
        if not has_cfg:
            env_list.append({
                "key": "LIBRECHAT_CONFIG_PATH",
                "value": "/app/librechat.yaml",
                "is_secret": False
            })

    # Template crash-clarity: enforce required envs for intelligence templates
    intelligence_templates = {
        'librechat', 'khoj', 'flowise', 'langflow',
        'dify', 'memgpt', 'anythingllm', 'ai-router'
    }
    if template and template.get('id') in intelligence_templates:
        env_list = template.setdefault('env_vars', [])
        existing = {str(ev.get('key') or '').upper() for ev in env_list}
        required_defaults = {
            'JWT_SECRET': '${RANDOM_PASSWORD}',
            'SECRET_KEY': '${RANDOM_PASSWORD}',
            'DATABASE_URL': '${DATABASE_URL}',
            'REDIS_URL': '${REDIS_URL}',
        }
        for key, val in required_defaults.items():
            if key not in existing:
                env_list.append({
                    "key": key,
                    "value": val,
                    "is_secret": 'SECRET' in key or 'PASSWORD' in key,
                })
    if template and template.get('docker_image'):
        _verify_image_available(template['docker_image'])
    supported_addons = set(addon_provisioner.ADDON_IMAGES.keys())

    # Track addon URLs for template rendering
    addon_urls = {}

    for addon_type in required_addons:
        if addon_type not in supported_addons:
            logger.warning("Template addon %s is not supported yet; skipping", addon_type)
            continue

        # Check if service already has this addon type active
        addon = Addon.objects.filter(service=service, addon_type=addon_type, status=Addon.Status.ACTIVE).first()
        if not addon:
            addon = Addon.objects.create(
                service=service,
                name=f"{addon_type.lower()}-{service.name}"[:255],
                addon_type=addon_type,
                status=Addon.Status.PROVISIONING,
            )
            try:
                _, url = addon_provisioner.provision_dispatch(addon)
                addon.connection_url = url
                addon.status = Addon.Status.ACTIVE
                addon.save()
            except Exception as e:
                logger.error(f"Failed to provision {addon_type} for template: {e}")
                addon.status = Addon.Status.FAILED
                addon.save()
                return

        addon_urls[addon_type] = addon.connection_url

        # Parse connection URL to get host:port for template DB_HOST vars
        addon_hostname = ""
        addon_port = ""
        try:
            parsed_addon = urlparse(addon.connection_url)
            if parsed_addon.hostname:
                addon_hostname = parsed_addon.hostname
                addon_port = str(parsed_addon.port or "")
        except Exception:
            pass

        # Inject Env (legacy/direct injection)
        key_map = {
            'POSTGRES': 'DATABASE_URL',
            'REDIS': 'REDIS_URL',
            'MONGODB': 'MONGODB_URI',
            'MYSQL': 'MYSQL_URL',
            'ELASTICSEARCH': 'ELASTICSEARCH_URL',
        }
        key = key_map.get(addon_type, f"{addon_type}_URL")
        EnvironmentVariable.objects.update_or_create(
            service=service, key=key,
            defaults={'value': addon.connection_url, 'is_secret': True}
        )

        # Update template-specific DB_HOST vars so apps find the addon
        # (e.g. WordPress expects WORDPRESS_DB_HOST, not MYSQL_URL)
        host_port = f"{addon_hostname}:{addon_port}" if addon_hostname and addon_port else addon_hostname
        if host_port and addon_type == 'MYSQL':
            db_host_keys = ['WORDPRESS_DB_HOST', 'DB_HOST']
            for db_host_key in db_host_keys:
                existing = EnvironmentVariable.objects.filter(
                    service=service, key=db_host_key
                ).first()
                if existing:
                    # Only overwrite if it looks like a placeholder
                    val = str(existing.value or "")
                    if not val or val == 'db:3306' or 'localhost' in val:
                        existing.value = host_port
                        existing.save(update_fields=['value'])
        if host_port and addon_type in ('POSTGRES', 'MYSQL', 'MONGODB'):
            # Generic DB_HOST for any app that needs it
            generic = EnvironmentVariable.objects.filter(
                service=service, key='DB_HOST'
            ).first()
            if not generic and host_port:
                EnvironmentVariable.objects.create(
                    service=service, key='DB_HOST',
                    value=host_port, is_secret=False
                )

    # Regenerate the Caddyfile so the wildcard block's @known_hosts
    # includes the freshly provisioned addons (e.g. redis-9c5a408f.grid.smsly.cloud).
    # Without this, the main service's deploy later fails the route
    # readiness check because Caddy returns 404 for the un-listed host.
    if required_addons:
        try:
            from .tasks_caddy import _regenerate_caddyfile
            _regenerate_caddyfile()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to regenerate Caddyfile after addon provisioning: %s", exc)

    # Render and store template environment variables
    def render_value(raw: str) -> str:
        import secrets
        v = str(raw or '')
        v = v.replace('${RANDOM_PASSWORD}', secrets.token_urlsafe(24))
        v = v.replace('${DOMAIN}', service.public_domain or '')
        v = v.replace('${MONGODB_URL}', addon_urls.get('MONGODB', ''))
        v = v.replace('${MONGODB_URI}', addon_urls.get('MONGODB', ''))
        v = v.replace('${DATABASE_URL}', addon_urls.get('POSTGRES', os.environ.get('DATABASE_URL', '')))
        v = v.replace('${POSTGRES_URL}', addon_urls.get('POSTGRES', os.environ.get('DATABASE_URL', '')))
        v = v.replace('${REDIS_URL}', addon_urls.get('REDIS', os.environ.get('REDIS_URL', '')))
        v = v.replace('${MYSQL_URL}', addon_urls.get('MYSQL', os.environ.get('MYSQL_URL', '')))
        v = v.replace('${ELASTICSEARCH_URL}', addon_urls.get('ELASTICSEARCH', os.environ.get('ELASTICSEARCH_URL', '')))

        # Shared Ollama URL — use the freshly injected service env var if available,
        # fall back to OS environment, then default.
        injected_ollama = (
            EnvironmentVariable.objects
            .filter(service=service, key='OLLAMA_BASE_URL')
            .values_list('value', flat=True)
            .first()
        )
        ollama_base_default = injected_ollama or os.environ.get('OLLAMA_BASE_URL', 'http://ollama:11434')

        # System Environment Overrides & Defaults
        default_ai_senate = os.environ.get('AI_SENATE_URL') or 'http://ollama:11434'
        v = v.replace('${AI_SENATE_URL}', default_ai_senate)
        v = v.replace('${LITELLM_MASTER_KEY}', os.environ.get('LITELLM_MASTER_KEY', ''))
        v = v.replace('${OLLAMA_BASE_URL}', ollama_base_default)
        v = v.replace('${OLLAMA_MODEL}', os.environ.get('OLLAMA_MODEL', 'llama3'))
        v = v.replace('${AI_ROUTER_API_BASE}', os.environ.get('AI_ROUTER_API_BASE', DEFAULT_AI_ROUTER_API_BASE))
        v = v.replace('${AI_ROUTER_UI_BASE}', os.environ.get('AI_ROUTER_UI_BASE', DEFAULT_AI_ROUTER_UI_BASE))
        v = v.replace('${AI_ROUTER_BRAID_ALIAS}', os.environ.get('AI_ROUTER_BRAID_ALIAS', DEFAULT_BRAID_ALIAS))

        return v

    if template and 'env_vars' in template:
        env_vars = template.get('env_vars') or []
        if isinstance(env_vars, list):
            for item in env_vars:
                if not isinstance(item, dict):
                    continue
                key = str(item.get('key') or '').strip()
                if not key:
                    continue
                EnvironmentVariable.objects.update_or_create(
                    service=service,
                    key=key,
                    defaults={
                        'value': render_value(item.get('value', '')),
                        'is_secret': bool(item.get('is_secret', False)),
                    }
                )

                # Generic custom domain handling from Env Vars
                if key == 'CUSTOM_DOMAINS':
                    rendered_val = render_value(item.get('value', ''))
                    domains = [d.strip() for d in rendered_val.split(',') if d.strip()]
                    current_domains = service.custom_domains or []
                    updated = False
                    for domain in domains:
                        if domain not in current_domains:
                            current_domains.append(domain)
                            updated = True
                    if updated:
                        service.custom_domains = current_domains
                        service.save(update_fields=['custom_domains'])

    if template and template.get('id') == 'ai-router':
        update_fields = []
        start_command = "--port 4000 --host 0.0.0.0"

        if service.internal_port != 4000:
            service.internal_port = 4000
            update_fields.append('internal_port')
        if (service.health_check_path or '').strip() in {'', '/health'}:
            service.health_check_path = '/'
            update_fields.append('health_check_path')
        if (service.start_command or '').strip() != start_command:
            service.start_command = start_command
            update_fields.append('start_command')
        if int(service.memory_mb or 0) < 1024:
            service.memory_mb = 1024
            update_fields.append('memory_mb')
        try:
            cpu_cores = float(service.cpu_cores or 0)
        except (TypeError, ValueError):
            cpu_cores = 0.0
        if cpu_cores < 1.0:
            service.cpu_cores = 1.0
            update_fields.append('cpu_cores')

        # Ensure we set a Prisma migration env var instead of nonexistent model fields
        if not EnvironmentVariable.objects.filter(service=service, key="RUN_PRISMA_MIGRATE").exists():
            EnvironmentVariable.objects.create(
                service=service,
                key="RUN_PRISMA_MIGRATE",
                value="true",
                is_secret=False
            )

        # Critical env hints
        required = {
            "LITELLM_MASTER_KEY": "sk-${RANDOM_PASSWORD}",
            "AI_ROUTER_API_BASE": DEFAULT_AI_ROUTER_API_BASE,
            "AI_ROUTER_UI_BASE": DEFAULT_AI_ROUTER_UI_BASE,
            "AI_ROUTER_AUTO_DISCOVER_MODELS": "true",
            "AI_ROUTER_SELECTED_SERVICE_IDS": "[]",
            "AI_ROUTER_BRAID_ALIAS": DEFAULT_BRAID_ALIAS,
            "AI_ROUTER_BRAID_ENABLED": "true",
        }
        # Remove explicit DB migrations since we are running stateless
        env_list = template.setdefault('env_vars', [])
        existing_keys = {str(ev.get("key") or "").upper() for ev in env_list}
        for key, val in required.items():
            if key not in existing_keys:
                env_list.append({"key": key, "value": val, "is_secret": True})
        existing_service_keys = {
            str(key or "").upper()
            for key in EnvironmentVariable.objects.filter(service=service).values_list('key', flat=True)
        }
        for key, val in required.items():
            if key in existing_service_keys:
                continue
            EnvironmentVariable.objects.create(
                service=service,
                key=key,
                value=render_value(val),
                is_secret=key in {"LITELLM_MASTER_KEY"},
            )
            existing_service_keys.add(key)
        if update_fields:
            service.save(update_fields=update_fields)



    provider = service.provider or CloudProvider.objects.filter(is_active=True).first()

    # ── Shared Ollama CPP Orchestration ─────────────────────────────────
    # Intelligently manages a single Ollama CPP instance per project.
    # When deploying any LLM that needs Ollama, the system auto-creates a
    # shared Ollama CPP runtime if one doesn't exist, and wires the new
    # service to it. When the last LLM consumer is deleted, the shared
    # Ollama is removed to free VPS resources.
    # ────────────────────────────────────────────────────────────────────
    shared_ollama_id = _ensure_shared_ollama_cpp(service, provider)
    shared_ollama_url = ""
    if shared_ollama_id:
        try:
            shared_ollama = Service.objects.get(id=shared_ollama_id)
            shared_name = shared_ollama.name
            shared_port = shared_ollama.internal_port or 11434
            shared_ollama_url = f"http://{shared_name}:{shared_port}"
        except Service.DoesNotExist:
            shared_ollama_id = None

    # Inject OLLAMA_BASE_URL for any LLM that references it
    if shared_ollama_url:
        ollama_base_key = 'OLLAMA_BASE_URL'
        if template:
            env_vars = template.get('env_vars') or []
            has_ollama_ref = any(
                str(item.get('key') or '').upper() in {'OLLAMA_BASE_URL', 'OLLAMA_MODEL'}
                for item in env_vars if isinstance(item, dict)
            )
            # Also detect Ollama-based templates by their docker image
            docker_img = str(template.get('docker_image') or '').lower()
            is_ollama_template = docker_img.startswith('ollama/') or docker_img == 'ollama/ollama:latest'
            if has_ollama_ref or is_ollama_template:
                EnvironmentVariable.objects.update_or_create(
                    service=service,
                    key=ollama_base_key,
                    defaults={'value': shared_ollama_url, 'is_secret': False}
                )
                # For Ollama-native templates, also set OLLAMA_HOST so they
                # know the host to talk to for ollama pull / API calls
                if is_ollama_template and shared_ollama_id:
                    EnvironmentVariable.objects.update_or_create(
                        service=service,
                        key='OLLAMA_HOST',
                        defaults={'value': shared_ollama_url.replace('http://', '').replace(':11434', ':11434'), 'is_secret': False}
                    )

    # One-Click AI Router + Ollama auto-deployment
    if provider and template and template.get('id') == 'ai-router':
        import re
        def slugify(value: str) -> str:
            value = (value or 'service').lower()
            value = re.sub(r'[^a-z0-9]+', '-', value).strip('-')
            return (value[:48] or 'service')

        # AI Router uses shared Ollama CPP — no need for 3 separate containers.
        # Register companion models via the shared Ollama instead.
        if shared_ollama_id:
            companion_service_ids = [str(shared_ollama_id)]

            # Pull required default models into the shared Ollama
            _pull_ollama_models_into_shared(
                shared_ollama_id,
                ['llama3.1:7b', 'qwen2.5:0.5b', 'nomic-embed-text'],
            )

            # Automatically update the AI_ROUTER_SELECTED_SERVICE_IDS
            try:
                import json
                EnvironmentVariable.objects.update_or_create(
                    service=service,
                    key='AI_ROUTER_SELECTED_SERVICE_IDS',
                    defaults={
                        'value': json.dumps(companion_service_ids),
                        'is_secret': False,
                    }
                )
            except Exception:
                pass

            # Wire OLLAMA_BASE_URL even if not in template env_vars
            EnvironmentVariable.objects.update_or_create(
                service=service,
                key='OLLAMA_BASE_URL',
                defaults={'value': shared_ollama_url, 'is_secret': False}
            )
        else:
            # Fallback: shared Ollama unavailable — deploy separate companions
            companion_templates = ['llama3.1-7b', 'qwen2.5-0.5b', 'ollama-nomic-embed-text']
            companion_service_ids = []

            for c_template_id in companion_templates:
                c_template = next((t for t in templates if t.get('id') == c_template_id), None)
                if not c_template:
                    continue

                c_name = f"{slugify(c_template_id)}-{secrets.token_hex(4)}"[:63]
                c_internal_port = int(c_template.get('default_port') or 11434)

                c_service = Service.objects.create(
                    name=c_name,
                    deploy_type='DOCKER',
                    docker_image=str(c_template.get('docker_image', 'ollama/ollama:latest')),
                    internal_port=c_internal_port,
                    owner=service.owner,
                    provider=provider,
                    project=service.project,
                    memory_mb=int(c_template.get('min_ram_gb') or 1) * 1024,
                    cpu_cores=float(c_template.get('min_cpu_cores') or 1.0)
                )
                companion_service_ids.append(str(c_service.id))

                EnvironmentVariable.objects.update_or_create(
                    service=c_service,
                    key='PORT',
                    defaults={'value': str(c_internal_port), 'is_secret': False}
                )
                EnvironmentVariable.objects.update_or_create(
                    service=c_service,
                    key='PUBLIC_DOMAIN',
                    defaults={'value': c_service.public_domain, 'is_secret': False}
                )

                c_env_vars = c_template.get('env_vars') or []
                for item in c_env_vars:
                    key = str(item.get('key') or '').strip()
                    if key:
                        EnvironmentVariable.objects.update_or_create(
                            service=c_service,
                            key=key,
                            defaults={
                                'value': render_value(item.get('value', '')),
                                'is_secret': bool(item.get('is_secret', False)),
                            }
                        )

                c_deployment = Deployment.objects.create(
                    service=c_service,
                    status='QUEUED',
                    commit_hash='template',
                    commit_message=f"Auto-companion Template: {c_template_id}"
                )
                enqueue_smart_deploy_task(deployment_id=str(c_deployment.id), provider_id=str(provider.id))

            if companion_service_ids:
                try:
                    import json
                    EnvironmentVariable.objects.update_or_create(
                        service=service,
                        key='AI_ROUTER_SELECTED_SERVICE_IDS',
                        defaults={
                            'value': json.dumps(companion_service_ids),
                            'is_secret': False,
                        }
                    )
                except Exception:
                    pass

            # Regenerate the Caddyfile so the wildcard block includes
            # the freshly created companion services' public_domains.
            try:
                from .tasks_caddy import _regenerate_caddyfile
                _regenerate_caddyfile()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Failed to regenerate Caddyfile after companion provisioning: %s", exc)

    # ── Ollama model pull for standalone Ollama templates ──────────────
    # When deploying a standalone Ollama model (e.g. deepseek-r1) and
    # shared Ollama CPP is handling it, schedule a model pull.
    if template and shared_ollama_id and shared_ollama_url:
        docker_img = str(template.get('docker_image') or '').lower()
        if docker_img.startswith('ollama/'):
            env_vars = template.get('env_vars') or []
            ollama_model = ""
            for item in (env_vars or []):
                if isinstance(item, dict) and str(item.get('key') or '').upper() == 'OLLAMA_MODEL':
                    ollama_model = render_value(item.get('value', ''))
                    break
            if ollama_model:
                _pull_ollama_models_into_shared(shared_ollama_id, [ollama_model])

    # Trigger deploy for the main template
    if provider:
        deployment = Deployment.objects.create(
            service=service,
            status='QUEUED',
            commit_hash='template',
            commit_message=f"Template: {template_id}"
        )
        enqueue_smart_deploy_task(deployment_id=str(deployment.id), provider_id=str(provider.id))

        # Post-deploy hook: if prisma migrate requested, annotate deployment for follow-up
        if any(ev.key == "RUN_PRISMA_MIGRATE" and ev.value.lower() in {"1", "true", "yes"} for ev in service.env_vars.all()):
            append_log(deployment, "\nℹ️ Prisma migration will run post-deploy for this template.\n")
