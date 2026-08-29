"""domain views."""
import logging
import os
import re

logger = logging.getLogger(__name__)



from django.db import transaction
from rest_framework import permissions, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from apps.deployments.models import EnvironmentVariable, Service
from apps.deployments.models.core import PlatformConfig
from ._helpers import EmptySerializer, _normalize_request_domain, _parse_bool, _rewrite_public_domain
class DomainConfigView(GenericAPIView):
    """
    Manage platform domain & SSL configuration.
    GET  /api/v1/system/domain-config/ → current config
    PUT  /api/v1/system/domain-config/ → update + apply Caddyfile
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        config = PlatformConfig.load()
        return Response({
            'domain': config.domain,
            'use_ssl': config.use_ssl,
            'wildcard_subdomains': config.wildcard_subdomains,
            'cloudflare_api_token_set': bool(config.cloudflare_api_token),
            'github_webhook_secret_set': bool(config.github_webhook_secret) or bool(os.environ.get('GITHUB_WEBHOOK_SECRET', '')),
            'gitlab_webhook_secret_set': bool(config.gitlab_webhook_secret) or bool(os.environ.get('GITLAB_WEBHOOK_SECRET', '')),
            'bitbucket_webhook_secret_set': bool(config.bitbucket_webhook_secret) or bool(os.environ.get('BITBUCKET_WEBHOOK_SECRET', '')),
            'server_ip': config.server_ip or '',
            'caddy_status': config.caddy_status,
            'max_concurrent_builds': config.max_concurrent_builds,
            'ecosystem_max_concurrent_builds': config.ecosystem_max_concurrent_builds,
            'ecosystem_build_stagger_seconds': config.ecosystem_build_stagger_seconds,
            'ecosystem_default_wave_size': config.ecosystem_default_wave_size,
            'ecosystem_wave_recheck_seconds': config.ecosystem_wave_recheck_seconds,
            # Billing
            'billing_currency': config.billing_currency,
            'billing_pro_amount': config.billing_pro_amount,
            'billing_pro_period_days': config.billing_pro_period_days,
            # SMSLY Platform
            'smsly_sms_api_url': config.smsly_sms_api_url,
            'smsly_voice_api_url': config.smsly_voice_api_url,
            'smsly_platform_api_url': config.smsly_platform_api_url,
            'smsly_internal_api_key_set': bool(config.smsly_internal_api_key),
            # Alerting
            'alert_phone_number': config.alert_phone_number,
            'critical_alert_phone': config.critical_alert_phone,
            'notify_on_success': config.notify_on_success,
            # Container Registry
            'container_registry_url': config.container_registry_url,
            'registry_user': config.registry_user,
            'registry_password_set': bool(config.registry_password),
            # Observability
            'sentry_dsn_set': bool(config.sentry_dsn),
            'sentry_traces_sample_rate': config.sentry_traces_sample_rate,
            'sentry_profiles_sample_rate': config.sentry_profiles_sample_rate,
            'sentry_environment': config.sentry_environment,
            # Deploy Pipeline
            'auto_review_hours': config.auto_review_hours,
            'auto_promote_hours': config.auto_promote_hours,
            # Feature Flags
            'smsly_disable_tier_gates': config.smsly_disable_tier_gates,
            'enable_legacy_tunnel_api': config.enable_legacy_tunnel_api,
            'smsly_strict_ssh_host_key_check': config.smsly_strict_ssh_host_key_check,
            'enable_crowdsec_waf': config.enable_crowdsec_waf,
            'trivy_enabled': config.trivy_enabled,
            'trivy_fail_on_severity': config.trivy_fail_on_severity,
            'cosign_enabled': config.cosign_enabled,
            'cosign_require_verification': config.cosign_require_verification,
            'backup_require_encryption': config.backup_require_encryption,
            'enforce_device_trust': config.enforce_device_trust,
            # Traffic Geo
            'traffic_geo_enabled': config.traffic_geo_enabled,
            'mapbox_token_set': bool(config.mapbox_token),
            # Database HA
            'db_ha_enabled': config.db_ha_enabled,
            # CrowdSec
            'crowdsec_bouncer_key_set': bool(config.crowdsec_bouncer_key),
            'crowdsec_enroll_key_set': bool(config.crowdsec_enroll_key),
            # SMTP
            'smtp_host': config.smtp_host,
            'smtp_port': config.smtp_port,
            'smtp_username': config.smtp_username,
            'smtp_password_set': bool(config.smtp_password),
            'smtp_use_tls': config.smtp_use_tls,
            'smtp_from_email': config.smtp_from_email,
            'smtp_from_name': config.smtp_from_name,
            'updated_at': config.updated_at,
        })

    @staticmethod
    def _rewrite_service_public_domains(old_base_domain: str, new_base_domain: str) -> int:
        """
        Rewrite generated service public domains onto the new platform base domain.

        Only domains currently using the previous platform base are rewritten.
        Custom domains stay untouched.
        """
        updated = 0
        host_keys = ("ALLOWED_HOSTS", "DJANGO_ALLOWED_HOSTS", "MARKETER_ALLOWED_HOSTS")
        for service in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain="").iterator():
            current_domain = str(service.public_domain or "").strip().lower().rstrip(".")
            next_domain = _rewrite_public_domain(current_domain, old_base_domain, new_base_domain)
            if not next_domain or next_domain == current_domain:
                continue

            if Service.objects.exclude(pk=service.pk).filter(public_domain=next_domain).exists():
                logger.warning(
                    "Skipping public domain rewrite for service=%s due to conflict on %s",
                    service.id,
                    next_domain,
                )
                continue

            service.public_domain = next_domain
            service.save(update_fields=["public_domain"])

            EnvironmentVariable.objects.filter(
                service=service,
                key="PUBLIC_DOMAIN",
            ).update(value=next_domain)

            for env_var in EnvironmentVariable.objects.filter(service=service, key__in=host_keys):
                value = str(env_var.value or "")
                if current_domain in value and next_domain not in value:
                    env_var.value = value.replace(current_domain, next_domain)
                    env_var.save(update_fields=["value"])

            updated += 1

        return updated

    def put(self, request):
        # SECURITY (Issue 22): wrap the whole update under a row
        # lock on the PlatformConfig singleton so two concurrent
        # admins cannot race the Caddyfile/DNS apply. The
        # Caddyfile is applied first; if the subsequent DNS apply
        # raises, the transaction rolls back the DB writes
        # (caddy_status, updated_at) and the caller sees a 5xx
        # with no partial DB state.
        with transaction.atomic():
            config = PlatformConfig.objects.select_for_update().get(pk=1)
            data = request.data
            previous_base_domain = Service.default_public_base_domain()
            original_domain = (config.domain or "").strip().lower().rstrip(".")

            # Update fields
            if 'domain' in data:
                raw_domain = str(data.get('domain') or '').strip()
                if raw_domain:
                    domain, domain_error = _normalize_request_domain(raw_domain)
                    if domain_error:
                        return Response(
                            {'error': f'Invalid domain: {domain_error}'},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    config.domain = domain
                else:
                    config.domain = ''
            if 'use_ssl' in data:
                config.use_ssl = _parse_bool(data.get('use_ssl'))
            if 'wildcard_subdomains' in data:
                config.wildcard_subdomains = _parse_bool(data.get('wildcard_subdomains'))
            if 'enable_crowdsec_waf' in data:
                config.enable_crowdsec_waf = _parse_bool(data.get('enable_crowdsec_waf'))
            if 'cloudflare_api_token' in data:
                # Allow explicit clear by sending an empty string.
                config.cloudflare_api_token = str(
                    data.get('cloudflare_api_token') or ''
                ).strip()
            clearing_token = 'cloudflare_api_token' in data and not config.cloudflare_api_token
            for _secret_field in ('github_webhook_secret', 'gitlab_webhook_secret', 'bitbucket_webhook_secret'):
                if _secret_field in data:
                    val = str(data.get(_secret_field) or '').strip()
                    setattr(config, _secret_field, val)
            if 'server_ip' in data:
                config.server_ip = str(data.get('server_ip') or '').strip() or None
            if 'max_concurrent_builds' in data:
                try:
                    config.max_concurrent_builds = max(1, min(10, int(data['max_concurrent_builds'])))
                except (TypeError, ValueError):
                    pass
            if 'ecosystem_max_concurrent_builds' in data:
                try:
                    config.ecosystem_max_concurrent_builds = max(1, min(10, int(data['ecosystem_max_concurrent_builds'])))
                except (TypeError, ValueError):
                    pass
            if 'ecosystem_build_stagger_seconds' in data:
                try:
                    config.ecosystem_build_stagger_seconds = max(0, min(300, int(data['ecosystem_build_stagger_seconds'])))
                except (TypeError, ValueError):
                    pass
            if 'ecosystem_default_wave_size' in data:
                try:
                    config.ecosystem_default_wave_size = max(1, min(5, int(data['ecosystem_default_wave_size'])))
                except (TypeError, ValueError):
                    pass
            if 'ecosystem_wave_recheck_seconds' in data:
                try:
                    config.ecosystem_wave_recheck_seconds = max(5, min(300, int(data['ecosystem_wave_recheck_seconds'])))
                except (TypeError, ValueError):
                    pass
            # Billing
            if 'billing_currency' in data:
                config.billing_currency = str(data.get('billing_currency') or 'USD').strip()[:10]
            if 'billing_pro_amount' in data:
                config.billing_pro_amount = str(data.get('billing_pro_amount') or '29.00').strip()[:20]
            if 'billing_pro_period_days' in data:
                try:
                    config.billing_pro_period_days = max(1, int(data['billing_pro_period_days']))
                except (TypeError, ValueError):
                    pass
            # SMSLY Platform
            for _field in ('smsly_sms_api_url', 'smsly_voice_api_url', 'smsly_platform_api_url'):
                if _field in data:
                    setattr(config, _field, str(data.get(_field) or '').strip()[:300])
            if 'smsly_internal_api_key' in data:
                config.smsly_internal_api_key = str(data.get('smsly_internal_api_key') or '').strip()
            # Alerting
            for _field in ('alert_phone_number', 'critical_alert_phone'):
                if _field in data:
                    setattr(config, _field, str(data.get(_field) or '').strip()[:20])
            if 'notify_on_success' in data:
                config.notify_on_success = _parse_bool(data.get('notify_on_success'))
            # Container Registry
            if 'container_registry_url' in data:
                config.container_registry_url = str(data.get('container_registry_url') or '').strip()[:255]
            if 'registry_user' in data:
                config.registry_user = str(data.get('registry_user') or '').strip()[:255]
            if 'registry_password' in data:
                config.registry_password = str(data.get('registry_password') or '').strip()
            # Observability
            if 'sentry_dsn' in data:
                config.sentry_dsn = str(data.get('sentry_dsn') or '').strip()[:300]
            if 'sentry_traces_sample_rate' in data:
                try:
                    config.sentry_traces_sample_rate = max(0.0, min(1.0, float(data['sentry_traces_sample_rate'])))
                except (TypeError, ValueError):
                    pass
            if 'sentry_profiles_sample_rate' in data:
                try:
                    config.sentry_profiles_sample_rate = max(0.0, min(1.0, float(data['sentry_profiles_sample_rate'])))
                except (TypeError, ValueError):
                    pass
            if 'sentry_environment' in data:
                config.sentry_environment = str(data.get('sentry_environment') or 'production').strip()[:50]
            # Traffic Geo
            if 'traffic_geo_enabled' in data:
                config.traffic_geo_enabled = _parse_bool(data.get('traffic_geo_enabled'))
            # Database HA
            if 'db_ha_enabled' in data:
                config.db_ha_enabled = _parse_bool(data.get('db_ha_enabled'))
            if 'mapbox_token' in data:
                config.mapbox_token = str(data.get('mapbox_token') or '').strip()
            # CrowdSec
            if 'crowdsec_bouncer_key' in data:
                config.crowdsec_bouncer_key = str(data.get('crowdsec_bouncer_key') or '').strip()
            if 'crowdsec_enroll_key' in data:
                config.crowdsec_enroll_key = str(data.get('crowdsec_enroll_key') or '').strip()
            # Deploy Pipeline
            if 'auto_review_hours' in data:
                try:
                    config.auto_review_hours = max(0, min(72, int(data['auto_review_hours'])))
                except (TypeError, ValueError):
                    pass
            if 'auto_promote_hours' in data:
                try:
                    config.auto_promote_hours = max(0, min(168, int(data['auto_promote_hours'])))
                except (TypeError, ValueError):
                    pass
            # Feature Flags
            for _field in ('smsly_disable_tier_gates', 'enable_legacy_tunnel_api', 'smsly_strict_ssh_host_key_check'):
                if _field in data:
                    setattr(config, _field, _parse_bool(data.get(_field)))
            # Security Scanning
            if 'trivy_enabled' in data:
                config.trivy_enabled = _parse_bool(data.get('trivy_enabled'))
            if 'trivy_fail_on_severity' in data:
                val = str(data.get('trivy_fail_on_severity') or 'CRITICAL').strip().upper()
                if val in ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL'):
                    config.trivy_fail_on_severity = val
            # Cosign Image Signing
            if 'cosign_enabled' in data:
                config.cosign_enabled = _parse_bool(data.get('cosign_enabled'))
            if 'cosign_require_verification' in data:
                config.cosign_require_verification = _parse_bool(data.get('cosign_require_verification'))
            # Backup Encryption
            if 'backup_require_encryption' in data:
                config.backup_require_encryption = _parse_bool(data.get('backup_require_encryption'))
            # Device Trust (Beta)
            if 'enforce_device_trust' in data:
                config.enforce_device_trust = _parse_bool(data.get('enforce_device_trust'))
            # SMTP / Email
            for _field in ('smtp_host', 'smtp_username', 'smtp_from_email', 'smtp_from_name'):
                if _field in data:
                    setattr(config, _field, str(data.get(_field) or '').strip()[:255])
            if 'smtp_port' in data:
                try:
                    config.smtp_port = max(1, min(65535, int(data['smtp_port'])))
                except (TypeError, ValueError):
                    pass
            if 'smtp_password' in data:
                config.smtp_password = str(data.get('smtp_password') or '').strip()
            if 'smtp_use_tls' in data:
                config.smtp_use_tls = _parse_bool(data.get('smtp_use_tls'))

            # Validate: wildcard requires Cloudflare token
            if config.wildcard_subdomains and config.use_ssl and not config.cloudflare_api_token:
                return Response(
                    {'error': 'Wildcard subdomains require a Cloudflare API Token.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            config.save()

            updated_service_domains = 0
            new_domain = (config.domain or "").strip().lower().rstrip(".")
            if new_domain and new_domain != previous_base_domain:
                updated_service_domains = self._rewrite_service_public_domains(
                    previous_base_domain,
                    new_domain,
                )
                if updated_service_domains:
                    logger.info(
                        "Rewrote %s service public domains from %s to %s",
                        updated_service_domains,
                        previous_base_domain,
                        new_domain,
                    )
            elif original_domain and not new_domain:
                logger.info(
                    "Platform domain cleared from %s; existing service public domains were left unchanged",
                    original_domain,
                )

            # Generate and apply Caddyfile
            try:
                from apps.deployments.services.caddy_manager import apply_caddyfile, generate_caddyfile
                caddyfile_content = generate_caddyfile(config)
                cf_token = (config.cloudflare_api_token or "").strip()
                result = apply_caddyfile(
                    caddyfile_content,
                    cloudflare_token=cf_token,
                    preserve_existing_token=not clearing_token,
                )
                config.caddy_status = 'applied' if result['ok'] else 'error'
                config.save(update_fields=['caddy_status'])
                if not result.get('ok'):
                    return Response(
                        {
                            'error': f"Config saved but Caddyfile apply failed: {result.get('message', 'unknown error')}",
                            'caddy_status': config.caddy_status,
                        },
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )

                # Auto-create DNS records on Cloudflare when possible.
                # If this raises, the surrounding transaction rolls
                # back the Caddyfile-apply status update, the
                # PlatformConfig changes, and the service-domain
                # rewrites — i.e. nothing is half-applied.
                if config.cloudflare_api_token and config.server_ip and config.domain:
                    try:
                        from apps.domains.services.dns import ensure_dns_records
                        domains = [config.domain]
                        if config.wildcard_subdomains:
                            domains.append(f"*.{config.domain}")
                        dns_result = ensure_dns_records(domains, config.server_ip, config.cloudflare_api_token)
                        if not dns_result.get("ok"):
                            logger.warning("DNS sync issues: %s", dns_result.get("errors"))
                    except Exception as dns_exc:  # pylint: disable=broad-exception-caught
                        logger.warning("DNS sync skipped: %s", dns_exc)
            except Exception as e:
                config.caddy_status = 'error'
                config.save(update_fields=['caddy_status'])
                return Response(
                    {'error': f'Config saved but Caddyfile apply failed: {e}'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            return Response({
                'message': 'Domain configuration updated and Caddyfile applied.',
                'caddy_status': config.caddy_status,
                'cloudflare_api_token_set': bool(config.cloudflare_api_token),
                'updated_service_domains': updated_service_domains,
                'redeploy_required': bool(updated_service_domains),
                'caddyfile_preview': _redact_caddyfile_preview(caddyfile_content),
            })


_CADDYFILE_REDACT_KEYWORDS = (
    'Strict-Transport-Security',
    'tls',
    'internal',
    'basicauth',
    'header Strict-Transport-Security',
)




def _redact_caddyfile_preview(text: str) -> str:
    """Strip any line in a Caddyfile preview that contains a secret or
    internal-only directive. The preview is returned to admins over
    the API, so we must not leak the actual TLS/internal/basicauth
    configuration, nor any ``${ENV_VAR}`` placeholders that may encode
    tokens. Each matching line is replaced wholesale with
    ``***REDACTED***``.

    A line that opens a ``basicauth`` or ``internal`` block also
    redacts all subsequent lines until the matching closing brace.
    """
    if not text:
        return text
    redacted_lines = []
    env_var_re = re.compile(r"\$\{[^}\s]+\}")
    block_open_keywords = ("basicauth", "internal")
    in_secret_block = 0
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            redacted_lines.append(line)
            continue
        lowered = stripped.lower()
        if not in_secret_block and any(
            kw.lower() in lowered for kw in _CADDYFILE_REDACT_KEYWORDS
        ):
            redacted_lines.append('***REDACTED***')
            if any(open_kw in lowered for open_kw in block_open_keywords) and "{" in stripped:
                in_secret_block = stripped.count("{") - stripped.count("}")
            continue
        if in_secret_block:
            redacted_lines.append('***REDACTED***')
            in_secret_block += stripped.count("{") - stripped.count("}")
            in_secret_block = max(in_secret_block, 0)
            continue
        if env_var_re.search(stripped):
            redacted_lines.append('***REDACTED***')
            continue
        redacted_lines.append(line)
    return "\n".join(redacted_lines)
