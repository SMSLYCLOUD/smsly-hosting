"""domain mixin."""
import logging

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from ...models import PlatformConfig
from .._helpers import _check_tier_gates_disabled, _normalize_request_domain, _parse_bool
from apps.teams.permissions import assert_can_write

logger = logging.getLogger(__name__)


from ...models import Service
from apps.domains.utils import normalize_domain


class DomainActionsMixin:
    """DomainActions actions for the viewset."""


    @action(detail=True, methods=["post"], url_path="hide-public-domain")
    def hide_public_domain(self, request, pk=None):
        service = self.get_object()
        if not service.public_domain:
            return Response({"error": "No public domain assigned."}, status=status.HTTP_400_BAD_REQUEST)
        service.public_domain_hidden = True
        service.save(update_fields=["public_domain_hidden"])
        # Sync routing to remove public domain block
        _ = self._sync_caddy()
        return Response({"message": "Public domain hidden", "public_domain_hidden": True})


    @action(detail=True, methods=["post"], url_path="unhide-public-domain")
    def unhide_public_domain(self, request, pk=None):
        service = self.get_object()
        if not service.public_domain:
            return Response({"error": "No public domain assigned."}, status=status.HTTP_400_BAD_REQUEST)
        service.public_domain_hidden = False
        service.save(update_fields=["public_domain_hidden"])
        _ = self._sync_caddy()
        return Response({"message": "Public domain unhidden", "public_domain_hidden": False})

    # --- Nested Resources: Deployments ---


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

        from ...utils import log_event
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
        domain = request.query_params.get('domain', '')
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
        from ...models.core import ManagedServer
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
        from ...models.addons import Addon
        if Addon.objects.filter(public_domain=domain).exists():
            return Response(status=status.HTTP_200_OK)

        # 5. Check against STAGED deployment staging URLs
        from ...models import Deployment
        if Deployment.objects.filter(
            status=Deployment.Status.STAGED,
            staging_url__icontains=domain,
        ).exists():
            return Response(status=status.HTTP_200_OK)

        # 6. Check against auto-generated staging domains (staging-{slug}-{hash}.{base})
        # Match pattern: staging-*.{base_domain}
        try:
            base_domain = Service.default_public_base_domain()
            if domain.endswith(f".{base_domain}"):
                prefix = domain.rsplit(f".{base_domain}", 1)[0]
                if prefix.startswith("staging-"):
                    return Response(status=status.HTTP_200_OK)
        except Exception:
            pass

        logger.warning("check_domain: unauthorized domain attempt: %s", domain)
        return Response(status=status.HTTP_404_NOT_FOUND)

    # ---------------------------------------------------------------------
    # Dependency graph endpoint – returns a list of services this service
    # depends on (by repo key) and a list of dependents.  The frontend can use
    # this to render a DAG.
    # ---------------------------------------------------------------------


    @action(detail=True, methods=['post'], url_path='retry-domain')
    def retry_domain(self, request, pk=None):
        """Retry domain verification"""
        service = self.get_object()
        assert_can_write(request.user, service, action='retry domain verification')
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

        from ...utils import log_event
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

        from ...utils import log_event
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


    def _sync_caddy(self):
        """Regenerate Caddyfile with all custom domains and trigger reload."""
        try:
            from apps.deployments.services.caddy_manager import apply_caddyfile, generate_caddyfile

            from ...models import PlatformConfig
            from ...utils import log_event
            config = PlatformConfig.load()
            content = generate_caddyfile(config)
            cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
            result = apply_caddyfile(content, cloudflare_token=cf_token)
            if result['ok']:
                logger.info("Caddy synced after domain change")
                log_event(
                    action='CADDY_RELOAD',
                    actor='system',
                    target='caddy',
                    metadata={'ok': True, 'message': str(result.get('message', ''))[:200]},
                )
            else:
                logger.error("Caddy sync failed: %s", result['message'])
                log_event(
                    action='CADDY_RELOAD',
                    actor='system',
                    target='caddy',
                    metadata={'ok': False, 'message': str(result.get('message', ''))[:200]},
                )
            return {
                "ok": bool(result.get("ok")),
                "message": str(result.get("message", "")).strip(),
            }
        except Exception as e:
            logger.error("Caddy sync error: %s", e)
            return {
                "ok": False,
                "message": str(e),
            }


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
