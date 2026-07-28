import logging
import os
import re

from apps.deployments.models import EnvironmentVariable, PlatformConfig
from apps.deployments.utils import (
    append_log,
    estimate_resources_from_deps,
    get_default_env_value,
    log_exhaustive_env_diagnostics,
    parse_ai_resource_recommendation,
    update_stage,
)
from apps.intelligence.services.env_intelligence import EnvironmentIntelligenceService


logger = logging.getLogger(__name__)


class AnalysisMixin:
    def _run_ai_analysis(self):
        """Step 1.5: AI Analysis → structured resource + exhaustive AI Senate env filling."""
        try:
            from apps.intelligence.scanner import RepoScanner
            scanner = RepoScanner(self.source_dir)

            # Step A: Perform aggressive scan
            scan_result = scanner.scan()
            ai_context = scanner.build_ai_context()

            # Step B: Consult the AI Senate for resource recommendations and diagnosis
            # (Keeping the resource logic as it was but optimizing the prompt)
            prompt = (
                f"Analyze this repo for deployment on Grid (Docker-based PaaS).\n"
                f"Service: {self.service.name}\n"
                f"Stack Context:\n{ai_context}\n\n"
                f"Return ONLY a JSON object:\n"
                f'{{\n'
                f'  "resources": {{"cpu_cores": <float>, "memory_mb": <int>}},\n'
                f'  "issues": ["..."],\n'
                f'  "diagnosis": "..."\n'
                f'}}\n'
            )

            from apps.intelligence.providers import ask_with_fallback
            response, provider = ask_with_fallback(prompt, mode="code_review")

            self.deployment.ai_diagnosis = (response or "").replace('\x00', '')
            self.deployment.save(update_fields=['ai_diagnosis'])

            append_log(self.deployment, f"\n🤖 AI Senate Analysis ({provider}) complete.\n")

            # Step C: Apply resource recommendations
            recommendation = parse_ai_resource_recommendation(response)
            if recommendation:
                self._apply_resource_recommendations(recommendation.get('resources', {}))
                for issue in recommendation.get('issues', []):
                    append_log(self.deployment, f"  ⚠️ {issue}\n")

            # Step D: GROUNDED env resolution from actual repo files
            # Replaces the AI Senate approach which hallucinated vars
            append_log(self.deployment, "📋 Resolving environment from repo manifest files...\n")
            try:
                from .manifest_env_resolver import ManifestEnvResolver
                resolver = ManifestEnvResolver(
                    source_dir=self.source_dir,
                    service_name=self.service.name,
                )
                resolved_env = resolver.resolve_all()
                injected = self._inject_manifest_env_vars(resolved_env, resolver)

                if resolver.unresolved_vars:
                    # Persist so review_summary exposes them to the auto-fill API action.
                    _rs = self.deployment.review_summary or {}
                    _rs['unresolved_external_vars'] = resolver.unresolved_vars
                    self.deployment.review_summary = _rs
                    append_log(
                        self.deployment,
                        f"  ⚠️ {len(resolver.unresolved_vars)} unresolved required var(s): "
                        f"{', '.join(resolver.unresolved_vars[:10])}\n"
                    )

                if resolver.is_frontend:
                    append_log(
                        self.deployment,
                        f"  ℹ️ Detected as frontend-only service — only {injected} frontend-friendly vars injected.\n"
                    )
                elif injected:
                    append_log(
                        self.deployment,
                        f"  ✅ Manifest resolver auto-filled {injected} variables.\n"
                    )
                else:
                    append_log(self.deployment, "  ℹ️ All detected variables are already configured.\n")

                # Send ALL detected env vars through the AI Senate.
                # The Senate is the primary resolver; heuristic defaults
                # are only used as a last-resort fallback when the Senate
                # is unavailable or returns no value for a particular var.
                if not resolver.is_frontend:
                    # Inject heuristic/unresolved vars from manifest resolver
                    # into scan_result so the Senate sees everything the
                    # resolver detected but couldn't give real values for.
                    _ctx = scan_result.setdefault('env_vars_context', {})
                    for _k in getattr(resolver, 'heuristic_vars', []):
                        _ctx.setdefault(_k, [f"Heuristic default from manifest resolver for {self.service.name}"])
                    for _k in getattr(resolver, 'unresolved_vars', []):
                        _ctx.setdefault(_k, [f"Unresolved required var from manifest resolver for {self.service.name}"])

                    total = len(_ctx) + sum(
                        1 for _ev in self.service.env_vars.all()
                        if not str(getattr(_ev, 'value', '') or '').strip()
                        or str(getattr(_ev, 'value', '') or '').strip()
                           in ("", "{{GENERATE}}", "{{FILL_ME}}", "CHANGEME", "TODO")
                    )
                    append_log(self.deployment, f"  🧠 AI Senate resolving {total} variables...\n")
                    try:
                        _sugg, _inj = EnvironmentIntelligenceService.apply_intelligence_to_service(
                            self.service, scan_result, source_dir=None
                        )
                        if _inj:
                            append_log(
                                self.deployment,
                                f"  ✅ AI Senate auto-filled {len(_inj)} variables: {', '.join(_inj[:10])}...\n",
                            )
                        else:
                            append_log(
                                self.deployment,
                                "  ℹ️ AI Senate: all detected variables already have production values.\n",
                            )
                        # Update unresolved list with vars the Senate
                        # couldn't fill.
                        if resolver.unresolved_vars:
                            resolver.unresolved_vars = [
                                k for k in resolver.unresolved_vars if k not in (_inj or [])
                            ]
                            _rs = self.deployment.review_summary or {}
                            if resolver.unresolved_vars:
                                _rs["unresolved_external_vars"] = resolver.unresolved_vars
                            else:
                                _rs.pop("unresolved_external_vars", None)
                            self.deployment.review_summary = _rs
                    except Exception as _senate_err:
                        logger.warning("AI Senate enrichment failed: %s", _senate_err)
            except Exception as e:
                logger.warning("Manifest env resolution failed: %s", e)
                append_log(self.deployment, f"\n⚠️ Manifest env resolution failed: {e!s}. Falling back to AI Senate.\n")
                _suggestions, injected = EnvironmentIntelligenceService.apply_intelligence_to_service(
                    self.service, scan_result
                )
                if injected:
                    append_log(self.deployment, f"  ✅ AI Senate auto-filled {len(injected)} variables: {', '.join(injected[:10])}...\n")

        except Exception as e:
            logger.warning("AI analysis failed: %s", e)
            append_log(self.deployment, f"\n🤖 AI analysis encountered an error: {e!s}. Falling back to heuristics.\n")

        # Heuristic fallback for resources
        if self.source_dir:
            self._apply_heuristic_resources()



    def _apply_resource_recommendations(self, resources: dict):
        """Apply AI resource recommendations (only increase, never decrease)."""
        if not resources:
            return

        updated_fields = []
        cpu = resources.get('cpu_cores')
        mem = resources.get('memory_mb')

        if cpu and float(cpu) > float(self.service.cpu_cores):
            old = self.service.cpu_cores
            self.service.cpu_cores = cpu
            updated_fields.append('cpu_cores')
            append_log(
                self.deployment,
                f"  📈 CPU: {old} → {cpu} cores (AI recommendation)\n"
            )

        if mem and int(mem) > self.service.memory_mb:
            old = self.service.memory_mb
            self.service.memory_mb = mem
            updated_fields.append('memory_mb')
            append_log(
                self.deployment,
                f"  📈 RAM: {old}MB → {mem}MB (AI recommendation)\n"
            )

        if updated_fields:
            self.service.save(update_fields=updated_fields)



    def _apply_heuristic_resources(self):
        """Fast fallback: scan deps for heavy packages and boost resources."""
        heuristic = estimate_resources_from_deps(self.source_dir)
        if not heuristic:
            return

        updated_fields = []
        cpu = heuristic.get('cpu_cores', 0)
        mem = heuristic.get('memory_mb', 0)

        if cpu > float(self.service.cpu_cores):
            self.service.cpu_cores = cpu
            updated_fields.append('cpu_cores')

        if mem > self.service.memory_mb:
            self.service.memory_mb = mem
            updated_fields.append('memory_mb')

        if updated_fields:
            self.service.save(update_fields=updated_fields)
            append_log(
                self.deployment,
                f"  🔧 Resources auto-adjusted from dependency scan: "
                f"{self.service.cpu_cores} CPU, "
                f"{self.service.memory_mb}MB RAM\n"
            )



    def _inject_ai_env_vars(self, ai_env_vars: dict):
        """Inject env vars recommended by AI analysis."""
        import secrets as _secrets

        # Keys where we MUST generate a real random value, never use AI's
        SECRET_PATTERNS = re.compile(
            r'(SECRET_KEY|JWT_SECRET|SESSION_SECRET|COOKIE_SECRET|'
            r'CSRF_SECRET|SIGNING_KEY|HASH_SALT)',
            re.IGNORECASE,
        )
        PASSWORD_PATTERNS = re.compile(
            r'(PASSWORD|PASSWD|DB_PASS)',
            re.IGNORECASE,
        )

        # Vars that will be auto-resolved at deploy time by _build_runtime_env.
        # Don't warn about these — they're platform-managed.
        DEPLOY_TIME_VARS = {
            # Domain-aware (resolved from service.public_domain)
            'PUBLIC_DOMAIN', 'ALLOWED_HOSTS', 'DJANGO_ALLOWED_HOSTS',
            'MARKETER_ALLOWED_HOSTS', 'API_INTERNAL_URL',
            # Database (derived from DATABASE_URL after addon provisioning)
            'DB_HOST', 'DB_PORT', 'DB_USER', 'DB_NAME', 'DB_PASSWORD',
            'DB_URL', 'MARKETER_DB_PASSWORD', 'SQL_HOST', 'DATABASE',
            'POSTGRES_HOST', 'POSTGRES_PORT', 'POSTGRES_USER',
            'POSTGRES_DB', 'POSTGRES_PASSWORD',
            # Redis (derived from REDIS_URL after addon provisioning)
            'CELERY_BROKER_URL', 'CELERY_RESULT_BACKEND', 'CACHE_URL',
            # Core platform vars
            'DATABASE_URL', 'REDIS_URL', 'PORT', 'HOSTNAME',
            # Cross-service discovery (resolved by ecosystem linker)
            'SMSLY_BACKEND_URL', 'BACKEND_URL',
            'IDENTITY_SERVICE_URL', 'PLATFORM_API_URL',
            'AUDIT_SERVICE_URL', 'TRANSACTION_CHAIN_URL',
            'SECURITY_GATEWAY_URL', 'POLICY_SERVICE_URL',
            'RATE_LIMIT_SERVICE_URL', 'VIDEO_SERVICE_URL',
            'VOICE_SERVICE_URL', 'HOSTING_SERVICE_URL',
            'NEXT_PUBLIC_API_URL',
            # Shared infra (ecosystem linker or addon provisioning)
            'RABBITMQ_URL', 'RABBITMQ_DEFAULT_USER', 'RABBITMQ_DEFAULT_PASS',
            'S3_ENDPOINT_URL', 'S3_ACCESS_KEY', 'S3_SECRET_KEY',
            'S3_BUCKET_NAME', 'AWS_STORAGE_BUCKET_NAME',
            # Propagated secrets (from sibling services)
            'INTERNAL_API_SECRET', 'GATEWAY_SECRET', 'JWT_SECRET',
        }

        injected = 0
        warned = 0
        deferred = 0

        for key, default_val in ai_env_vars.items():
            key = key.strip().upper()
            if not key:
                continue

            # Skip if already set by the user
            if EnvironmentVariable.objects.filter(
                service=self.service, key=key
            ).exists():
                continue

            # Skip platform-managed vars — they'll be injected at deploy time
            if key in DEPLOY_TIME_VARS:
                deferred += 1
                continue

            # Skip config vars that look like secrets but aren't (e.g. AI_MAX_TOKENS, SD_x_TTL_DAYS)
            _SKIP_CONFIG = {"TTL", "TIMEOUT", "SECONDS", "DAYS", "HOURS", "MINUTES",
                            "MAX_", "MIN_", "LIMIT", "PORT", "COUNT", "COOLDOWN",
                            "CACHE_TTL", "ROTATION_", "INTERVAL", "RETRIES"}
            if any(p in key for p in _SKIP_CONFIG):
                injected += 1
                continue

            # For secret keys: ALWAYS generate a real random value
            if SECRET_PATTERNS.search(key):
                real_secret = _secrets.token_urlsafe(50)
                EnvironmentVariable.objects.create(
                    service=self.service,
                    key=key,
                    value=real_secret,
                    is_secret=True,
                )
                injected += 1
                append_log(
                    self.deployment,
                    f"  🔐 Auto-generated {key} (secure random)\n"
                )
                continue

            # For password keys: generate a strong password
            if PASSWORD_PATTERNS.search(key):
                real_pass = _secrets.token_urlsafe(48)
                EnvironmentVariable.objects.create(
                    service=self.service,
                    key=key,
                    value=real_pass,
                    is_secret=True,
                )
                injected += 1
                append_log(
                    self.deployment,
                    f"  🔐 Auto-generated {key} (secure random)\n"
                )
                continue

            if default_val and str(default_val).strip():
                # Sanitize for PostgreSQL
                safe_val = str(default_val).strip().replace('\x00', '')

                # Has a sensible default → inject it
                EnvironmentVariable.objects.create(
                    service=self.service,
                    key=key,
                    value=safe_val,
                    is_secret=False
                )
                injected += 1
                append_log(
                    self.deployment,
                    f"  🔧 Auto-set {key}={safe_val[:50]}\n"
                )
            else:
                # Empty value = secret the user must provide
                warned += 1
                append_log(
                    self.deployment,
                    f"  ⚠️ Missing required env var: {key} "
                    f"(set this in Service → Settings)\n"
                )

        if injected:
            append_log(
                self.deployment,
                f"\n  ✅ Auto-injected {injected} env var(s) from AI analysis.\n"
            )
        if deferred:
            append_log(
                self.deployment,
                f"  🔄 {deferred} env var(s) will be auto-resolved at deploy time "
                f"(domain, database, redis).\n"
            )
        if warned:
            append_log(
                self.deployment,
                f"  🔴 {warned} env var(s) need manual setup!\n"
            )



    def _inject_manifest_env_vars(
        self,
        resolved_env: dict[str, str],
        resolver: "ManifestEnvResolver",
    ) -> int:
        """Inject env vars from ManifestEnvResolver into the database.

        Respects user-set vars (get_or_create), detects secret vs. non-secret,
        and logs everything clearly.
        """
        # pylint: disable=import-outside-toplevel
        from apps.deployments.models import EnvironmentVariable

        # Security patterns for auto-detecting secret vars
        _SECRET_PATTERNS = re.compile(
            r"(SECRET|TOKEN|PASSWORD|PRIVATE_KEY|API_KEY|DSN|CREDENTIAL|"
            r"ENCRYPTION_KEY|SIGNING_KEY)",
            re.IGNORECASE,
        )

        injected = 0
        skipped = 0
        deferred = 0

        for key, value in resolved_env.items():
            key_upper = key.strip().upper()
            if not key_upper:
                continue

            # Skip if already set by the user via UI/API
            if EnvironmentVariable.objects.filter(
                service=self.service, key=key_upper
            ).exists():
                skipped += 1
                continue

            # Skip vars with placeholder patterns (will be resolved at deploy time)
            if value.startswith("{{") and value.endswith("}}"):
                deferred += 1
                continue

            # Skip empty values (unresolved required vars)
            if not value:
                continue

            # Sanitize for PostgreSQL
            safe_val = value.replace("\x00", "")

            is_secret = bool(_SECRET_PATTERNS.search(key_upper))
            EnvironmentVariable.objects.create(
                service=self.service,
                key=key_upper,
                value=safe_val,
                is_secret=is_secret,
            )
            injected += 1

            display_val = "********" if is_secret else safe_val[:60]
            append_log(
                self.deployment,
                f"  📋 {key_upper}={display_val}\n",
            )

        if injected:
            append_log(
                self.deployment,
                f"\n✅ Manifest resolver: {injected} injected, "
                f"{skipped} already set, {deferred} deploy-time.\n",
            )
        return injected



    def _inject_env_vars(self):
        """Step 1.6: Auto-inject env vars from code scanning."""
        try:
            # pylint: disable=import-outside-toplevel
            from apps.intelligence.scanner import RepoScanner
            scanner = RepoScanner(self.source_dir)
            scan_result = scanner.scan()

            detected = scan_result.get('env_vars', [])
            injected_count = 0

            for key in detected:
                default_val, should_inject = get_default_env_value(
                    key, scan_result, self.service.name
                )
                if should_inject:
                    key_upper = str(key or "").strip().upper()
                    is_secret = bool(
                        re.search(
                            r"(SECRET|TOKEN|PASSWORD|DSN|PRIVATE_KEY|API_KEY)",
                            key_upper,
                        )
                    )
                    _, created = EnvironmentVariable.objects.get_or_create(
                        service=self.service,
                        key=key_upper,
                        defaults={'value': default_val, 'is_secret': is_secret},
                    )
                    if created:
                        injected_count += 1

            if injected_count:
                append_log(self.deployment, f"\n🔧 Auto-injected {injected_count} env vars.\n")

            self._inject_proxy_runtime_defaults(scan_result)
            log_exhaustive_env_diagnostics(self.deployment, self.service, "Auto-Scan / Manifest")

        except Exception as e: # pylint: disable=broad-exception-caught
            logger.warning("Env injection failed: %s", e)



    def _inject_proxy_runtime_defaults(self, scan_result: dict):
        """
        Inject runtime defaults for proxied TLS deployments.

        In the default production topology (Caddy -> Traefik -> app), some
        Django apps enable SECURE_SSL_REDIRECT but do not trust forwarded
        headers, causing HTTPS redirect loops.
        """
        try:
            platform_cfg = PlatformConfig.load()
            if not platform_cfg.use_ssl:
                return

            if str(os.getenv("TRAEFIK_ENABLE_WEBSECURE", "false")).strip().lower() in {
                "1", "true", "yes", "on"
            }:
                return

            stack = str((scan_result or {}).get("stack", "")).lower()
            if "django" not in stack:
                return

            _, created = EnvironmentVariable.objects.get_or_create(
                service=self.service,
                key="SECURE_SSL_REDIRECT",
                defaults={"value": "false", "is_secret": False},
            )
            if created:
                append_log(
                    self.deployment,
                    "  🔧 Set SECURE_SSL_REDIRECT=false for proxied TLS runtime\n",
                )

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Proxy runtime defaults injection failed: %s", exc)

