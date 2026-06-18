import logging
logger = logging.getLogger(__name__)
from .views_system import _redact_caddyfile_preview
from .views_service import _check_tier_gates_disabled
from .views_auth import EmptySerializer
import os
import posixpath
import hmac
import re
from rest_framework import viewsets, permissions, status, parsers, serializers, authentication
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from django.utils import timezone
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.db.models import Prefetch
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, transaction, models
from django.db.models import Q, Count, Avg, F, ExpressionWrapper, DurationField
from django.utils.http import content_disposition_header
from django.core import signing
from apps.deployments.services.github_webhooks import setup_github_webhook
from apps.deployments.services.gitlab_webhooks import setup_gitlab_webhook
from apps.deployments.services.bitbucket_webhooks import setup_bitbucket_webhook
import threading
from .ai_router import DEFAULT_AI_ROUTER_API_BASE, DEFAULT_AI_ROUTER_UI_BASE, DEFAULT_BRAID_ALIAS, is_ai_router_service, persist_ai_router_config, serialize_ai_router_config
from .models import Service, Deployment, EnvironmentVariable, PlatformConfig
from .serializers import ServiceSerializer, DeploymentSerializer, DeploymentTriggerSerializer, EnvVarSerializer, DeploymentTimelineSerializer, InstantRollbackSerializer, AuditLogSerializer, DeploymentApproveSerializer, ServiceBackupSerializer, ServerBackupSerializer, BackupScheduleSerializer
from .models_audit import AuditLog
from .models_backup import ServiceBackup, ServerBackup, BackupSchedule
from .tasks import smart_deploy_task, resume_deploy_task, create_service_backup_task, create_server_backup_task, restore_service_backup_task, enqueue_smart_deploy_task
from .rate_limiting import BurstRateThrottle, DeploymentRateThrottle
from .domain_utils import normalize_domain
from .services.server_guard import ServerGuard
from apps.cloud.models import CloudProvider
import uuid
import logging
import re
from celery.result import AsyncResult
from apps.cloud.docker_client import get_docker_client
from .utils import validate_and_sanitize_path
from apps.deployments.utils import resolve_running_container
from apps.teams.permissions import get_team_q_filter, assert_can_write, assert_can_delete, user_can_read
from .views_audit import AuditLogViewSet
from .views_auth import SessionTokenView
from .views_route_status import RouteStatusView
from .views_transfer import ServerTransferViewSet


def _normalize_request_domain(raw_domain: str):
    """Normalize and validate user-provided domains."""
    try:
        return normalize_domain(raw_domain), None
    except ValueError as exc:
        return None, str(exc)


def _rewrite_public_domain(current_domain: str, old_base_domain: str, new_base_domain: str) -> str | None:
    """Rewrite a service public domain from one Grid platform base domain to another."""
    current = str(current_domain or "").strip().lower().rstrip(".")
    old_base = str(old_base_domain or "").strip().lower().rstrip(".")
    new_base = str(new_base_domain or "").strip().lower().rstrip(".")
    if not current or not old_base or not new_base or old_base == new_base:
        return None

    if current == old_base:
        return new_base

    suffix = f".{old_base}"
    if not current.endswith(suffix):
        return None

    prefix = current[:-len(suffix)].rstrip(".")
    if not prefix:
        return new_base
    return f"{prefix}.{new_base}"


def _service_for_domain(domain: str):
    """Find service routed by this public/custom domain."""
    direct = Service.objects.filter(public_domain=domain, public_domain_hidden=False).first()
    if direct:
        return direct
    try:
        return Service.objects.filter(custom_domains__contains=[domain]).first()
    except Exception:
        pass
    for service in Service.objects.only("id", "custom_domains")[:500]:
        values = [
            str(value or "").strip().lower()
            for value in (service.custom_domains or [])
            if str(value or "").strip()
        ]
        if domain in values:
            return service
    return None


def _parse_bool(value):
    """Safely parse booleans from JSON or form-encoded payloads."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


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
                from services.caddy_manager import generate_caddyfile, apply_caddyfile
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
                        from apps.deployments.services.dns import ensure_dns_records
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


class ServiceDomainActionsMixin:
    @action(detail=True, methods=['post'], url_path='verify-domain')
    def verify_domain(self, request, pk=None):
        """
        Verify that a custom domain's DNS points to this service's server.
        POST /api/v1/services/{id}/verify-domain/
        Body: { "domain": "myapp.com" }
        """
        service = self.get_object()
        assert_can_write(self.request.user, service)
        domain, domain_error = _normalize_request_domain(
            request.data.get('domain', '')
        )
        if domain_error:
            return Response(
                {'error': f'Invalid domain: {domain_error}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Per-apex daily cert issuance cap ────────────────────────
        # Mirrors the cap on check_domain so a single apex cannot exhaust
        # Let's Encrypt's rate-limit through repeated verifications.
        raw_domain_for_cap = domain.strip().lower()
        apex = (
            raw_domain_for_cap.split('.', 1)[-1]
            if '.' in raw_domain_for_cap
            else raw_domain_for_cap
        )
        if apex:
            cap_key = f"certs_issued:{apex}:{timezone.now().strftime('%Y%m%d')}"
            cap_value = cache.get(cap_key, 0)
            cap_limit = int(getattr(settings, 'CADDY_DAILY_CERT_CAP', 20))
            if cap_value >= cap_limit:
                logger.warning(
                    "verify_domain: daily cert cap reached for apex %s (%d)",
                    apex, cap_value,
                )
                return Response(
                    {
                        'error': (
                            f"Daily cert issuance cap reached for {apex}. "
                            "Try again tomorrow."
                        )
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            if cache.get(cap_key) is not None:
                try:
                    cache.incr(cap_key, 1)
                except ValueError:
                    cache.set(cap_key, cap_value + 1, timeout=86400)
            else:
                cache.set(cap_key, 1, timeout=86400)

        # Compare against the service's own public domain which already
        # resolves to the correct server IP. No hardcoded CNAME needed.
        cname_target = service.public_domain or ''
        if not cname_target:
            return Response({
                'domain': domain,
                'verified': False,
                'cname_target': '',
                'message': 'Service has no public domain assigned yet.',
            })

        from apps.domains.models import Domain, DomainStatus
        from apps.domains.verification import verify_custom_domain_dns

        domain_obj = Domain.objects.filter(service=service, domain_name=domain).first()
        transient_domain = domain_obj or Domain(domain_name=domain, service=service)
        old_status = domain_obj.status if domain_obj else DomainStatus.PENDING
        result = verify_custom_domain_dns(transient_domain, PlatformConfig.load())
        is_valid = result.verified

        if domain_obj:
            domain_obj.dns_expected = result.expected
            domain_obj.dns_actual = result.actual
            domain_obj.verified = is_valid
            domain_obj.last_error = None if is_valid else result.error
            if is_valid:
                domain_obj.status = (
                    old_status
                    if old_status in [DomainStatus.ACTIVE, DomainStatus.SSL_PROVISIONING]
                    else DomainStatus.DNS_VERIFIED
                )
            else:
                domain_obj.status = DomainStatus.DNS_PENDING
                domain_obj.ssl_active = False
            domain_obj.save(update_fields=[
                'status',
                'dns_expected',
                'dns_actual',
                'verified',
                'last_error',
                'ssl_active',
            ])
            if is_valid and old_status not in [
                DomainStatus.DNS_VERIFIED,
                DomainStatus.SSL_PROVISIONING,
                DomainStatus.ACTIVE,
            ]:
                self._sync_caddy()

        # ── Persist the verification result on the Service model ──
        # This is the critical step that was missing. Without this, the
        # domain_verified field stays False forever and the frontend badge
        # never updates.
        if is_valid and not service.domain_verified:
            service.domain_verified = True
            service.save(update_fields=['domain_verified'])
        elif not is_valid and service.domain_verified:
            service.domain_verified = False
            service.save(update_fields=['domain_verified'])

        from .utils import log_event
        log_event(
            actor=getattr(request.user, 'username', None) or 'system',
            action='DOMAIN_VERIFY',
            target=f'Service: {service.name}',
            metadata={
                'service_id': str(service.id),
                'domain': domain,
                'result': 'success' if is_valid else 'fail',
            },
        )

        return Response({
            'domain': domain,
            'verified': is_valid,
            'cname_target': cname_target,
            'dns_expected': result.expected,
            'dns_actual': result.actual,
            'message': (
                'DNS verified! Domain points to Grid.'
                if is_valid
                else (
                    f'DNS not configured. Add {result.expected}. '
                    'Use DNS-only records so direct SSL can be issued.'
                )
            ),
        })


    @action(detail=False, methods=['get'], url_path='check-domain')
    def check_domain(self, request):
        """
        Endpoint for Caddy's on_demand_tls 'ask' directive.
        GET /api/v1/services/check-domain/?domain=myapp.com
        Returns 200 OK if the domain is authorized, 404 otherwise.

        Authentication: requires ``X-Caddy-Secret`` header matching
        ``settings.CADDY_ASK_SECRET`` (machine-to-machine Caddy) OR an
        authenticated admin user. Rate-limited per IP via the ``caddy_ask``
        scope to prevent trivial DoS of Let's Encrypt.
        """
        # ── Per-apex daily cert issuance cap ────────────────────────
        # Limit blast radius if DNS verification is bypassed: a single
        # apex may not consume more than CADDY_DAILY_CERT_CAP (default 20)
        # hostnames per UTC day.
        raw_domain_for_cap = request.query_params.get('domain', '').strip().lower()
        apex = (
            raw_domain_for_cap.split('.', 1)[-1]
            if '.' in raw_domain_for_cap
            else raw_domain_for_cap
        )
        if apex:
            cap_key = f"certs_issued:{apex}:{timezone.now().strftime('%Y%m%d')}"
            cap_value = cache.get(cap_key, 0)
            cap_limit = int(getattr(settings, 'CADDY_DAILY_CERT_CAP', 20))
            if cap_value >= cap_limit:
                logger.warning(
                    "check_domain: daily cert cap reached for apex %s (%d)",
                    apex, cap_value,
                )
                return Response(
                    {
                        'error': (
                            f"Daily cert issuance cap reached for {apex}. "
                            "Try again tomorrow."
                        )
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            if cache.get(cap_key) is not None:
                try:
                    cache.incr(cap_key, 1)
                except ValueError:
                    cache.set(cap_key, cap_value + 1, timeout=86400)
            else:
                cache.set(cap_key, 1, timeout=86400)

        raw_domain = raw_domain_for_cap
        if not raw_domain:
            return Response(status=status.HTTP_404_NOT_FOUND)
        import ipaddress
        is_ip = False
        try:
            ipaddress.ip_address(raw_domain)
            is_ip = True
            domain = raw_domain
        except ValueError:
            try:
                domain = normalize_domain(raw_domain)
            except ValueError:
                return Response(status=status.HTTP_404_NOT_FOUND)

        # 1. Check against PlatformConfig primary domain
        try:
            cfg = PlatformConfig.load()
            if cfg.domain and domain == cfg.domain.strip().lower():
                return Response(status=status.HTTP_200_OK)
        except Exception as exc:
            logger.debug("check_domain: PlatformConfig check failed: %s", exc)

        # 2. Check against Managed Servers (allow inter-node control traffic)
        from .models_core import ManagedServer
        query = Q(host=domain)
        if is_ip:
            query |= Q(private_ip=domain)

        if ManagedServer.objects.filter(query).exists():
            return Response(status=status.HTTP_200_OK)

        # 3. Check against Services (Public Domain)
        if Service.objects.filter(public_domain=domain).exists():
            return Response(status=status.HTTP_200_OK)

        # 3. Check verified custom domains. Pending JSONField entries are
        # intentionally not authorized, otherwise Caddy may attempt ACME before
        # the customer has pointed DNS at this server.
        from apps.domains.models import Domain, DomainStatus
        routable_custom_domain = (
            Domain.objects
            .filter(
                domain_name=domain,
                status__in=[
                    DomainStatus.ACTIVE,
                    DomainStatus.DNS_VERIFIED,
                    DomainStatus.SSL_PROVISIONING,
                ],
            )
            .filter(Q(verified=True) | Q(status=DomainStatus.ACTIVE))
            .exists()
        )
        if routable_custom_domain:
            return Response(status=status.HTTP_200_OK)

        # 4. Check against Addons
        from .models_addons import Addon
        if Addon.objects.filter(public_domain=domain).exists():
            return Response(status=status.HTTP_200_OK)

        logger.warning("check_domain: unauthorized domain attempt: %s", domain)
        return Response(status=status.HTTP_404_NOT_FOUND)


    @action(detail=True, methods=['post'], url_path='retry-domain')
    def retry_domain(self, request, pk=None):
        """Retry domain verification"""
        service = self.get_object()
        domain_name = request.data.get('domain', '').strip().lower()
        if not domain_name:
            return Response({'error': 'Domain required'}, status=status.HTTP_400_BAD_REQUEST)

        from apps.domains.models import Domain
        domain_obj = Domain.objects.filter(service=service, domain_name=domain_name).first()
        if not domain_obj:
            return Response({'error': 'Domain not found'}, status=status.HTTP_404_NOT_FOUND)

        from apps.domains.tasks import verify_dns_and_provision_ssl_task
        verify_dns_and_provision_ssl_task.delay(domain_obj.id)

        return Response({'message': 'Verification retried', 'status': domain_obj.status})


    @action(detail=True, methods=['post'], url_path='add-domain')
    def add_domain(self, request, pk=None):
        """
        Add a custom domain to the service.
        POST /api/v1/services/{id}/add-domain/
        Body: { "domain": "myapp.com" }
        """
        service = self.get_object()
        assert_can_write(self.request.user, service)
        domain, domain_error = _normalize_request_domain(
            request.data.get('domain', '')
        )
        if domain_error:
            return Response(
                {'error': f'Invalid domain: {domain_error}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        domains = [
            d for d in (service.custom_domains or [])
            if isinstance(d, str) and d.strip()
        ]
        if domain in domains:
            return Response({'error': 'Domain already added'},
                            status=status.HTTP_400_BAD_REQUEST)

        conflict = self._find_domain_conflict(service, domain)
        if conflict:
            return Response(
                {
                    'error': (
                        f'Domain already assigned to service "{conflict.name}". '
                        'A domain can only be attached to one service.'
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        domains = list(dict.fromkeys([*domains, domain]))
        quota_response = self._enforce_custom_domain_quota(service, len(domains))
        if quota_response is not None:
            return quota_response

        service.custom_domains = domains
        service.save(update_fields=['custom_domains'])

        from apps.domains.models import Domain, DomainStatus
        # Clean up old domains
        Domain.objects.filter(service=service).exclude(domain_name__in=domains).delete()

        # Add new domains
        for d in domains:
            domain_obj, created = Domain.objects.get_or_create(
                domain_name=d,
                defaults={'service': service, 'status': DomainStatus.PENDING}
            )
            if created:
                from apps.domains.tasks import verify_dns_and_provision_ssl_task
                verify_dns_and_provision_ssl_task.delay(domain_obj.id)


        cfg = PlatformConfig.load()
        cname_target = service.public_domain or cfg.domain or ''
        server_ip = str(cfg.server_ip or '')

        # Auto-sync Caddyfile so SSL + routing are provisioned immediately.
        # No service redeploy is required.
        caddy_result = self._sync_caddy()
        caddy_ok = bool(caddy_result.get("ok"))
        caddy_message = caddy_result.get("message") or "Routing sync failed."

        from .utils import log_event
        log_event(
            actor=getattr(request.user, 'username', None) or 'system',
            action='DOMAIN_ADD',
            target=f'Service: {service.name}',
            metadata={
                'service_id': str(service.id),
                'domain': domain,
                'caddy_synced': caddy_ok,
            },
        )

        if not caddy_ok:
            logger.warning(
                "add_domain: domain saved but routing sync failed for %s (%s): %s",
                service.id,
                domain,
                caddy_message,
            )
            return Response(
                {
                    'domain': domain,
                    'domains': domains,
                    'cname_target': cname_target,
                    'server_ip': server_ip,
                    'message': (
                        f'{domain} was saved, but automatic routing sync failed. '
                        'Routing may not activate until Caddy reload succeeds.'
                    ),
                    'warning': caddy_message,
                    'caddy_synced': False,
                    'requires_redeploy': False,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        return Response({
            'domain': domain,
            'domains': domains,
            'cname_target': cname_target,
            'server_ip': server_ip,
            'caddy_synced': caddy_ok,
            'routing_sync_deployment_id': None,
            'requires_redeploy': False,
            'dns_synced': False,
            'message': (
                f'{domain} added. Point DNS to the shown CNAME or server IP; '
                'SSL will be issued directly after verification. No redeploy required.'
            ),
        }, status=status.HTTP_201_CREATED)


    @action(detail=True, methods=['post'], url_path='delete-domain')
    def delete_domain(self, request, pk=None):
        """
        Remove a custom domain from the service.
        POST /api/v1/services/{id}/delete-domain/
        Body: { "domain": "myapp.com" }
        """
        service = self.get_object()
        assert_can_write(self.request.user, service)
        domain, domain_error = _normalize_request_domain(
            request.data.get('domain', '')
        )
        if domain_error:
            return Response(
                {'error': f'Invalid domain: {domain_error}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        domains = [
            d for d in (service.custom_domains or [])
            if isinstance(d, str) and d.strip()
        ]
        if domain not in domains:
            return Response({'error': 'Domain not found'},
                            status=status.HTTP_404_NOT_FOUND)

        domains = [d for d in domains if d != domain]
        service.custom_domains = domains
        service.save(update_fields=['custom_domains'])

        from apps.domains.models import Domain
        Domain.objects.filter(domain_name=domain, service=service).delete()

        from apps.domains.models import Domain, DomainStatus
        # Clean up old domains
        Domain.objects.filter(service=service).exclude(domain_name__in=domains).delete()

        # Add new domains
        for d in domains:
            domain_obj, created = Domain.objects.get_or_create(
                domain_name=d,
                defaults={'service': service, 'status': DomainStatus.PENDING}
            )
            if created:
                from apps.domains.tasks import verify_dns_and_provision_ssl_task
                verify_dns_and_provision_ssl_task.delay(domain_obj.id)


        # Auto-sync Caddyfile so stale domain entry is removed immediately.
        caddy_result = self._sync_caddy()
        caddy_ok = bool(caddy_result.get("ok"))
        caddy_message = caddy_result.get("message") or "Routing sync failed."

        from .utils import log_event
        log_event(
            actor=getattr(request.user, 'username', None) or 'system',
            action='DOMAIN_DELETE',
            target=f'Service: {service.name}',
            metadata={
                'service_id': str(service.id),
                'domain': domain,
                'caddy_synced': caddy_ok,
            },
        )

        if not caddy_ok:
            logger.warning(
                "delete_domain: domain removed but routing sync failed for %s (%s): %s",
                service.id,
                domain,
                caddy_message,
            )
            return Response(
                {
                    'domain': domain,
                    'domains': domains,
                    'message': (
                        f'{domain} was removed, but automatic routing sync failed. '
                        'Old routing entries may persist until Caddy reload succeeds.'
                    ),
                    'warning': caddy_message,
                    'caddy_synced': False,
                    'requires_redeploy': False,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        return Response({
            'domains': domains,
            'caddy_synced': caddy_ok,
            'routing_sync_deployment_id': None,
            'requires_redeploy': False,
            'message': f'{domain} removed. No redeploy required.',
        })


    def _find_domain_conflict(self, service: Service, domain: str):
        """Return conflicting service if domain is already assigned globally."""
        public_conflict = (
            Service.objects
            .exclude(id=service.id)
            .filter(public_domain=domain)
            .only("id", "name", "public_domain")
            .first()
        )
        if public_conflict:
            return public_conflict

        from apps.domains.models import Domain
        domain_obj = Domain.objects.filter(domain_name=domain).exclude(service=service).first()
        if domain_obj:
            return domain_obj.service
        return None


    def _enforce_custom_domain_quota(self, service: Service, new_total: int):
        """
        Enforce billing plan limit for custom domains.
        (Disabled for self-hosted instances).
        """
        if _check_tier_gates_disabled():
            return None
        try:
            from apps.billing.models import UserSubscription
            sub = UserSubscription.objects.filter(user=service.owner, status='ACTIVE').first()
            limit = sub.plan.max_custom_domains if sub and sub.plan else 1
            if new_total > limit:
                return Response(
                    {'error': f'Custom domain limit reached ({limit}). Please upgrade your plan.'},
                    status=status.HTTP_403_FORBIDDEN
                )
        except ImportError:
            pass
        return None
