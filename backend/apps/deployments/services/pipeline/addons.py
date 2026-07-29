import json
import logging
import os
import re
from urllib.parse import urlparse as parse_url

import yaml

from apps.deployments.models import EnvironmentVariable
from apps.deployments.models.addons import Addon
from apps.deployments.utils import (
    append_log,
    log_exhaustive_addon_provisioning_diagnostics,
    update_stage,
)
from .exceptions import PipelineError


logger = logging.getLogger(__name__)


class AddonMixin:
    # Dependency package → addon type mapping
    _REQUIREMENTS_ADDON_MAP = {
        # PostgreSQL
        'psycopg2': 'POSTGRES', 'psycopg2-binary': 'POSTGRES',
        'asyncpg': 'POSTGRES', 'django': 'POSTGRES',
        'dj-database-url': 'POSTGRES', 'sqlalchemy': 'POSTGRES',
        # Redis
        'redis': 'REDIS', 'celery': 'REDIS', 'django-redis': 'REDIS',
        'aioredis': 'REDIS', 'rq': 'REDIS',
        # MongoDB
        'pymongo': 'MONGODB', 'motor': 'MONGODB', 'mongoengine': 'MONGODB',
        # Qdrant
        'qdrant-client': 'QDRANT',
        # MySQL
        'mysqlclient': 'MYSQL', 'pymysql': 'MYSQL', 'aiomysql': 'MYSQL',
    }

    # Docker image prefix → addon type mapping
    _COMPOSE_ADDON_MAP = {
        'postgres': 'POSTGRES', 'redis': 'REDIS', 'mongo': 'MONGODB',
        'mysql': 'MYSQL', 'mariadb': 'MYSQL', 'qdrant': 'QDRANT',
        'elasticsearch': 'ELASTICSEARCH', 'rabbitmq': 'RABBITMQ',
        'memcached': 'MEMCACHED', 'clickhouse': 'CLICKHOUSE',
        'minio': 'MINIO',
    }

    def _provision_from_grid_addons(self):
        """Detect and process ``grid.addons`` manifest if present.

        When the manifest exists, this method handles ALL addon and bundle
        provisioning — standard addons via :class:`AddonProvisioner` and
        custom bundles via :class:`BundleProvisioner`.

        Returns:
            ``True`` if grid.addons was found and processed, ``None``
            otherwise (signalling the caller to fall through to heuristic
            auto-detection).
        """
        from apps.addons.services.grid_addons_parser import (
            find_grid_addons_file,
            load_grid_addons,
        )

        if not find_grid_addons_file(self.source_dir):
            return None

        try:
            manifest = load_grid_addons(self.source_dir)
        except Exception as exc:
            append_log(
                self.deployment,
                f"⚠️ Failed to parse grid.addons: {exc}\n"
                f"  Falling back to auto-detection.\n",
            )
            logger.warning("grid.addons parse failed: %s", exc)
            return None

        if manifest is None:
            return None

        append_log(
            self.deployment,
            f"\n📋 Found grid.addons (service_type={manifest.service_type or 'auto'})\n"
            f"   Standard addons: {', '.join(sorted(manifest.standard_addon_types)) or '(none)'}\n"
            f"   Bundles: {', '.join(b.name for b in manifest.bundles) or '(none)'}\n"
        )

        # ── Phase 1: Provision standard addons ──
        from apps.addons.services.addon_provisioner import addon_provisioner

        from apps.deployments.models import EnvironmentVariable

        addon_urls: dict[str, str] = {}
        failed_addons: list[str] = []

        if manifest.addons:
            existing_addons = {
                a.addon_type: a
                for a in Addon.objects.filter(
                    service=self.service,
                    status__in=['ACTIVE', 'PROVISIONING', 'FAILED'],
                )
            }

            for addon_decl in manifest.addons:
                addon_type = addon_decl.addon_type
                if addon_type in existing_addons:
                    addon = existing_addons[addon_type]
                    if addon.status == Addon.Status.ACTIVE and addon.connection_url:
                        addon_urls[addon_decl.name] = addon.connection_url
                        addon_urls[addon_type.lower()] = addon.connection_url
                        append_log(
                            self.deployment,
                            f"  ✅ {addon_type} already active\n",
                        )
                        continue
                    elif addon.status == Addon.Status.PROVISIONING:
                        append_log(
                            self.deployment,
                            f"  ⏳ {addon_type} still provisioning, skipping\n",
                        )
                        continue
                    # FAILED — retry by re-provisioning the existing record
                    append_log(
                        self.deployment,
                        f"  🔄 {addon_type} was failed, retrying...\n",
                    )

                # Create or reuse failed addon record
                if addon_type in existing_addons:
                    addon = existing_addons[addon_type]
                    addon.status = Addon.Status.PROVISIONING
                    addon.save(update_fields=['status'])
                else:
                    addon = Addon.objects.create(
                        service=self.service,
                        name=f"{addon_decl.name}-{self.service.name}"[:255],
                        addon_type=addon_type,
                        status=Addon.Status.PROVISIONING,
                    )
                try:
                    _, url = addon_provisioner.provision_dispatch(addon)
                    if not url:
                        raise ValueError(
                            f"{addon_type} provisioned but returned empty URL"
                        )
                    addon.connection_url = url
                    addon.status = Addon.Status.ACTIVE
                    addon.save()

                    addon_urls[addon_decl.name] = url
                    addon_urls[addon_type.lower()] = url

                    env_key = addon_provisioner.ENV_KEY_MAP.get(
                        addon_type, f"{addon_type}_URL",
                    )
                    EnvironmentVariable.objects.update_or_create(
                        service=self.service, key=env_key,
                        defaults={
                            'value': url,
                            'is_secret': True,
                            'source': 'ADDON',
                        },
                    )

                    append_log(
                        self.deployment,
                        f"  ✅ {addon_type} provisioned → {env_key}\n",
                    )
                except Exception as exc:
                    addon.status = Addon.Status.FAILED
                    addon.save(update_fields=['status'])
                    failed_addons.append(addon_type)
                    append_log(
                        self.deployment,
                        f"  ⚠️ {addon_type} provisioning failed: {exc}\n",
                    )
                    logger.warning(
                        "grid.addons: provision %s failed: %s", addon_type, exc,
                    )

        # ── Phase 2: Provision custom bundles ──
        if manifest.bundles:
            import hashlib

            from apps.addons.services.bundle_provisioner import bundle_provisioner

            from apps.deployments.models.bundles import Bundle, BundleComponent

            with open(os.path.join(self.source_dir, 'grid.addons'), 'rb') as fh:
                grid_addons_hash = hashlib.sha256(fh.read()).hexdigest()[:16]

            for bundle_decl in manifest.bundles:
                # Check if bundle already exists and is up-to-date
                existing_bundle = Bundle.objects.filter(
                    service=self.service, name=bundle_decl.name,
                ).first()

                if (
                    existing_bundle
                    and existing_bundle.status == Bundle.Status.ACTIVE
                    and existing_bundle.grid_addons_hash == grid_addons_hash
                ):
                    append_log(
                        self.deployment,
                        f"  ✅ Bundle '{bundle_decl.name}' already active\n",
                    )
                    # Collect existing component URLs for env injection
                    for comp in existing_bundle.components.filter(status='ACTIVE'):
                        if comp.connection_url:
                            addon_urls[comp.name] = comp.connection_url
                    continue

                # Create or update bundle record
                if existing_bundle:
                    bundle = existing_bundle
                    bundle.status = Bundle.Status.PROVISIONING
                    bundle.grid_addons_hash = grid_addons_hash
                    bundle.save(update_fields=['status', 'grid_addons_hash'])
                else:
                    bundle = Bundle.objects.create(
                        service=self.service,
                        name=bundle_decl.name,
                        status=Bundle.Status.PROVISIONING,
                        grid_addons_hash=grid_addons_hash,
                    )

                append_log(
                    self.deployment,
                    f"\n🔧 Provisioning bundle '{bundle_decl.name}' "
                    f"({len(bundle_decl.services)} components)...\n",
                )

                try:
                    component_urls = bundle_provisioner.provision(
                        bundle=bundle_decl,
                        service_id=str(self.service.id),
                        service_name=self.service.name,
                        addon_urls=addon_urls,
                        build_dir=self.source_dir,
                    )

                    # Post-provision DB writes — rollback Docker on failure
                    try:
                        bundle.status = Bundle.Status.ACTIVE
                        bundle.network = bundle_provisioner._network_name(
                            bundle_decl, str(self.service.id),
                        )
                        bundle.save(update_fields=['status', 'network'])

                        # Create/update BundleComponent records
                        for svc_decl in bundle_decl.services:
                            url = component_urls.get(svc_decl.name, "")
                            container_name = bundle_provisioner._container_name(
                                bundle.name, str(self.service.id), svc_decl.name,
                            )
                            BundleComponent.objects.update_or_create(
                                bundle=bundle,
                                name=svc_decl.name,
                                defaults={
                                    'source_type': (
                                        BundleComponent.SourceType.REPO
                                        if svc_decl.repo
                                        else BundleComponent.SourceType.IMAGE
                                    ),
                                    'image': svc_decl.image or '',
                                    'repo': svc_decl.repo or '',
                                    'branch': svc_decl.branch or 'main',
                                    'build_type': svc_decl.build or '',
                                    'status': BundleComponent.Status.ACTIVE,
                                    'container_name': container_name,
                                    'connection_url': url,
                                    'ports': svc_decl.ports,
                                },
                            )

                            # Inject component env vars
                            if url:
                                slug = svc_decl.name.upper().replace('-', '_')
                                from urllib.parse import urlparse as _urlparse
                                parsed_url = _urlparse(url)
                                EnvironmentVariable.objects.update_or_create(
                                    service=self.service,
                                    key=f"{slug}_URL",
                                    defaults={'value': url, 'is_secret': True, 'source': 'ADDON'},
                                )
                                EnvironmentVariable.objects.update_or_create(
                                    service=self.service,
                                    key=f"{slug}_HOST",
                                    defaults={
                                        'value': parsed_url.hostname or url,
                                        'is_secret': False,
                                        'source': 'ADDON',
                                    },
                                )
                                addon_urls[svc_decl.name] = url

                    except Exception as db_exc:
                        # DB writes failed — tear down Docker containers
                        logger.warning(
                            "grid.addons: DB writes failed for bundle '%s', "
                            "rolling back Docker: %s",
                            bundle_decl.name, db_exc,
                        )
                        try:
                            bundle_provisioner.deprovision(
                                bundle_decl.name,
                                str(self.service.id),
                                network_name=bundle.network or None,
                            )
                        except Exception:
                            logger.error(
                                "grid.addons: rollback deprovision failed for '%s'",
                                bundle_decl.name,
                            )
                        raise db_exc

                    append_log(
                        self.deployment,
                        f"  ✅ Bundle '{bundle_decl.name}' provisioned "
                        f"({len(component_urls)} components)\n",
                    )

                except Exception as exc:
                    bundle.status = Bundle.Status.FAILED
                    bundle.deletion_error = str(exc)[:500]
                    bundle.save(update_fields=['status', 'deletion_error'])
                    failed_addons.append(f"bundle:{bundle_decl.name}")
                    append_log(
                        self.deployment,
                        f"  ⚠️ Bundle '{bundle_decl.name}' failed: {exc}\n",
                    )
                    logger.warning(
                        "grid.addons: bundle '%s' failed: %s",
                        bundle_decl.name, exc,
                    )

        if failed_addons:
            raise PipelineError(
                f"grid.addons provisioning failed for: {', '.join(failed_addons)}. "
                f"Aborting deployment to prevent starting without required infrastructure."
            )

        return True  # signal that grid.addons was processed



    def _auto_provision_addons(self):
        """Step 1.7: Auto-detect and provision required addons."""
        try:
            # ── Strategy -1: grid.addons manifest (highest priority) ──
            # When a grid.addons file is present, it is the authoritative
            # source of addon + bundle requirements.  We handle it here
            # and skip all heuristic auto-detection.
            grid_addons_result = self._provision_from_grid_addons()
            if grid_addons_result is not None:
                return  # grid.addons handled everything

            detected_types = set()

            # --- Strategy 0: infer from existing env vars (highest confidence) ---
            try:
                env_map = {
                    'REDIS': {'REDIS_URL', 'REDIS_URI', 'REDIS_HOST'},
                    'RABBITMQ': {'CELERY_BROKER_URL', 'AMQP_URL', 'RABBITMQ_URL'},
                    'POSTGRES': {'DATABASE_URL', 'PG_URL', 'POSTGRES_URL'},
                    'QDRANT': {'QDRANT_URL'},
                    'MONGODB': {'MONGODB_URI', 'MONGODB_URL'},
                }
                service_env = {
                    ev.key: ev.value
                    for ev in EnvironmentVariable.objects.filter(service=self.service)
                }
                for addon_type, keys in env_map.items():
                    if any(k in service_env for k in keys):
                        detected_types.add(addon_type)
            except Exception as exc:
                logger.debug("Failed to scan service env vars for addon detection: %s", exc)

            # --- Strategy 0.5: infer from internal port hints (best-effort) ---
            port_map = {
                5432: 'POSTGRES',
                6379: 'REDIS',
                5672: 'RABBITMQ',
                27017: 'MONGODB',
                9200: 'ELASTICSEARCH',
                6333: 'QDRANT',
            }
            hinted = port_map.get(int(self.service.internal_port or 0))
            if hinted:
                detected_types.add(hinted)

            # --- Strategy A: scan requirements.txt / Pipfile ---
            req_candidates = [
                'requirements.txt', 'requirements/base.txt',
                'requirements/production.txt',
            ]
            # Monorepo support: also check 1-level-deep subdirectories
            try:
                for subdir in os.listdir(self.source_dir):
                    subpath = os.path.join(self.source_dir, subdir)
                    if os.path.isdir(subpath) and not subdir.startswith('.'):
                        req_candidates.append(os.path.join(subdir, 'requirements.txt'))
            except OSError:
                pass

            for name in req_candidates:
                req_path = os.path.join(self.source_dir, name)
                if os.path.isfile(req_path):
                    with open(req_path, encoding='utf-8',
                              errors='ignore') as f:
                        for line in f:
                            pkg = line.strip().split('==')[0].split('>=')[0] \
                                .split('<=')[0].split('[')[0].split('#')[0] \
                                .strip().lower()
                            addon = self._REQUIREMENTS_ADDON_MAP.get(pkg)
                            if addon:
                                detected_types.add(addon)

            # Also check pyproject.toml dependencies (root + subdirs)
            pyproject_candidates = [os.path.join(self.source_dir, 'pyproject.toml')]
            try:
                for subdir in os.listdir(self.source_dir):
                    subpath = os.path.join(self.source_dir, subdir)
                    if os.path.isdir(subpath) and not subdir.startswith('.'):
                        candidate = os.path.join(subpath, 'pyproject.toml')
                        if os.path.isfile(candidate):
                            pyproject_candidates.append(candidate)
            except OSError:
                pass

            for pyproject in pyproject_candidates:
                if os.path.isfile(pyproject):
                    with open(pyproject, encoding='utf-8',
                              errors='ignore') as f:
                        content = f.read()
                        for pkg, addon in self._REQUIREMENTS_ADDON_MAP.items():
                            if pkg in content:
                                detected_types.add(addon)

            # Check package.json for Node.js apps (root + subdirs)
            import json
            pkg_json_paths = [os.path.join(self.source_dir, 'package.json')]
            try:
                for subdir in os.listdir(self.source_dir):
                    subpath = os.path.join(self.source_dir, subdir)
                    if os.path.isdir(subpath) and not subdir.startswith('.'):
                        candidate = os.path.join(subpath, 'package.json')
                        if os.path.isfile(candidate):
                            pkg_json_paths.append(candidate)
            except OSError:
                pass

            node_map = {
                'pg': 'POSTGRES', 'sequelize': 'POSTGRES',
                'typeorm': 'POSTGRES', 'prisma': 'POSTGRES',
                'redis': 'REDIS', 'ioredis': 'REDIS',
                'bullmq': 'REDIS', 'bull': 'REDIS',
                'mongoose': 'MONGODB', 'mongodb': 'MONGODB',
                'mysql2': 'MYSQL',
                '@qdrant/js-client-rest': 'QDRANT',
            }
            for pkg_json in pkg_json_paths:
                if not os.path.isfile(pkg_json):
                    continue
                try:
                    with open(pkg_json, encoding='utf-8') as f:
                        pkg_data = json.load(f)
                    all_deps = {}
                    all_deps.update(pkg_data.get('dependencies', {}))
                    all_deps.update(pkg_data.get('devDependencies', {}))
                    for dep in all_deps:
                        addon = node_map.get(dep)
                        if addon:
                            detected_types.add(addon)
                except (json.JSONDecodeError, KeyError):
                    pass

            # --- Strategy B: scan docker-compose.yml (all common variants) ---
            # Priority order: prod variants first, then generic
            COMPOSE_NAMES = (
                'docker-compose.prod.yml', 'docker-compose.prod.yaml',
                'docker-compose.production.yml',
                'docker-compose.production.yaml',
                'compose.prod.yml', 'compose.prod.yaml',
                'docker-compose.yml', 'docker-compose.yaml',
                'compose.yml', 'compose.yaml',
            )
            detected_compose_file = None
            for name in COMPOSE_NAMES:
                compose_path = os.path.join(self.source_dir, name)
                if os.path.isfile(compose_path):
                    with open(compose_path, encoding='utf-8',
                              errors='ignore') as f:
                        content = f.read()
                    # Match image: lines for addon detection
                    for match in re.findall(
                        r'image:\s*[\'"]?([^\s\'"]+)', content
                    ):
                        img = match.lower().split('/')[  # handle org/image
                            -1].split(':')[0]  # strip tag
                        addon = self._COMPOSE_ADDON_MAP.get(img)
                        if addon:
                            detected_types.add(addon)

                    # Use the first (highest priority) compose file found
                    if not detected_compose_file:
                        detected_compose_file = name

            # --- Deploy mode is user-controlled ---
            # Never auto-switch to COMPOSE. The user's deploy_mode selection
            # in the UI is always respected. We only log what we found.
            if self.service.deploy_mode == 'COMPOSE':
                append_log(
                    self.deployment,
                    f"\n🐳 Compose mode: {self.service.compose_file}\n"
                    f"   Main service: {self.service.compose_main_service or '(user must select)'}\n"
                )
            elif detected_compose_file:
                append_log(
                    self.deployment,
                    f"\n📦 Single container mode (compose file '{detected_compose_file}' detected but not used)\n"
                )

            if not detected_types:
                return

            # --- Provision missing addons ---
            # pylint: disable=import-outside-toplevel
            from apps.addons.services.addon_provisioner import addon_provisioner

            from apps.deployments.models.addons import Addon

            supported_addons = set(addon_provisioner.ADDON_IMAGES.keys())
            unsupported = detected_types - supported_addons
            if unsupported:
                append_log(
                    self.deployment,
                    f"  ℹ️ Detected unsupported addons (skipped): {', '.join(sorted(unsupported))}\n"
                )

            detected_types = detected_types & supported_addons
            if not detected_types:
                return

            existing = set(
                Addon.objects.filter(
                    service=self.service,
                    status__in=['ACTIVE', 'PROVISIONING']
                ).values_list('addon_type', flat=True)
            )

            # If a previous attempt failed, retry provisioning those types too.
            failed = set(
                Addon.objects.filter(
                    service=self.service,
                    status=Addon.Status.FAILED
                ).values_list('addon_type', flat=True)
            )

            to_provision = (detected_types | failed) - existing

            # Re-provision/verify existing addons to ensure they are running and connected
            # to the network before deployment resumes.
            existing_addons = Addon.objects.filter(
                service=self.service,
                addon_type__in=existing,
                status__in=['ACTIVE', 'PROVISIONING']
            )
            for addon in existing_addons:
                try:
                    _, url = addon_provisioner.provision_dispatch(addon)
                    if url and addon.connection_url != url:
                        addon.connection_url = url
                        addon.status = Addon.Status.ACTIVE
                        addon.save()
                        # Re-inject updated URL
                        env_key = addon_provisioner.ENV_KEY_MAP.get(
                            addon.addon_type, f"{addon.addon_type}_URL"
                        )
                        from apps.deployments.models import EnvironmentVariable
                        EnvironmentVariable.objects.update_or_create(
                            service=self.service, key=env_key,
                            defaults={'value': url, 'is_secret': True}
                        )
                except Exception as e:
                    logger.warning(f"Failed to verify existing addon {addon.addon_type}: {e}")
                    append_log(self.deployment, f"  ⚠️ Could not verify existing addon {addon.addon_type}: {e}\n")

            if not to_provision:
                append_log(
                    self.deployment,
                    f"\n✅ All {len(detected_types)} detected addons "
                    f"already provisioned.\n"
                )
                log_exhaustive_addon_provisioning_diagnostics(self.deployment, sorted(detected_types))
                return

            append_log(
                self.deployment,
                f"\n🔍 Auto-detected addons: "
                f"{', '.join(sorted(detected_types))}\n"
                f"📦 Provisioning {len(to_provision)} new: "
                f"{', '.join(sorted(to_provision))}\n"
            )

            for addon_type in to_provision:
                addon = Addon.objects.create(
                    service=self.service,
                    name=f"{addon_type.lower()}-{self.service.name}"[:255],
                    addon_type=addon_type,
                    status=Addon.Status.PROVISIONING,
                )
                try:
                    _, url = addon_provisioner.provision_dispatch(addon)
                    addon.connection_url = url
                    addon.status = Addon.Status.ACTIVE
                    addon.save()

                    from apps.deployments.models import EnvironmentVariable

                    # Inject connection URL as env var
                    env_key = addon_provisioner.ENV_KEY_MAP.get(
                        addon_type, f"{addon_type}_URL"
                    )
                    EnvironmentVariable.objects.update_or_create(
                        service=self.service, key=env_key,
                        defaults={'value': url, 'is_secret': True}
                    )

                    # RabbitMQ: also fill common broker aliases for celery/worker stacks
                    if addon_type == 'RABBITMQ':
                        for extra_key in ("CELERY_BROKER_URL", "AMQP_URL"):
                            EnvironmentVariable.objects.update_or_create(
                                service=self.service, key=extra_key,
                                defaults={'value': url, 'is_secret': True}
                            )

                    # Qdrant: also set QDRANT_HOST/QDRANT_PORT
                    if addon_type == 'QDRANT':
                        parsed = parse_url(url)
                        EnvironmentVariable.objects.update_or_create(
                            service=self.service, key='QDRANT_HOST',
                            defaults={
                                'value': parsed.hostname or 'localhost',
                                'is_secret': False
                            }
                        )
                        EnvironmentVariable.objects.update_or_create(
                            service=self.service, key='QDRANT_PORT',
                            defaults={
                                'value': str(parsed.port or 6333),
                                'is_secret': False
                            }
                        )

                    append_log(
                        self.deployment,
                        f"  ✅ {addon_type} provisioned → {env_key}\n"
                    )

                except Exception as e:  # pylint: disable=broad-exception-caught
                    addon.status = Addon.Status.FAILED
                    addon.save()
                    append_log(
                        self.deployment,
                        f"  ⚠️ {addon_type} provisioning failed: {e}\n"
                    )
                    logger.warning(
                        "Auto-provision %s failed: %s", addon_type, e
                    )

            log_exhaustive_addon_provisioning_diagnostics(self.deployment, sorted(detected_types))
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Auto-addon provisioning failed: %s", e)

