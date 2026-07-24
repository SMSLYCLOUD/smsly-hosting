"""
Runtime patching for Django settings.
Allows dynamic domain whitelisting and SITE_URL updates from PlatformConfig.
"""
import logging
import re
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

_patching_lock = threading.Lock()
_patching_in_progress = False

def patch_runtime_settings():
    """
    Sync PlatformConfig values (domain, use_ssl) to Django settings.
    Updates ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, and SITE_URL.
    Uses a re-entrancy guard to prevent infinite recursion when the
    post_save signal on PlatformConfig fires during initial load.
    """
    global _patching_in_progress
    if _patching_in_progress:
        logger.debug("[patch] Re-entrancy guard active, skipping.")
        return

    _patching_in_progress = True
    try:
        logger.debug("[patch] Attempting to sync settings from PlatformConfig...")
        import warnings

        from django.contrib.sites.models import Site

        from apps.deployments.models import PlatformConfig

        # Suppress Django's "Accessing the database during app initialization"
        # warning — this DB access is intentional and required so ALLOWED_HOSTS
        # is populated before the first request arrives.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Accessing the database during app initialization")
            pc = PlatformConfig.load()

        # 1. Determine Effective Domain/Protocol
        effective_domain = pc.domain or getattr(settings, 'DOMAIN', 'localhost')
        effective_use_ssl = pc.use_ssl if pc.domain else (not getattr(settings, 'DEBUG', False))

        logger.debug("[patch] Effective domain: %s (SSL: %s)", effective_domain, effective_use_ssl)

        # 2. Patch ALLOWED_HOSTS
        if effective_domain and effective_domain not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS.append(effective_domain)
            logger.info("[patch] Added %s to ALLOWED_HOSTS", effective_domain)

        # 3. Patch Security Origins
        is_ip = bool(re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', effective_domain))
        scheme = 'http' if is_ip else ('https' if (effective_use_ssl and not getattr(settings, 'DEBUG', False)) else 'http')
        origin = f'{scheme}://{effective_domain}'

        if origin not in settings.CSRF_TRUSTED_ORIGINS:
            settings.CSRF_TRUSTED_ORIGINS.append(origin)
        if origin not in settings.CORS_ALLOWED_ORIGINS:
            settings.CORS_ALLOWED_ORIGINS.append(origin)

        # 4. Patch SITE_URL and allauth protocol
        settings.SITE_URL = origin
        settings.ACCOUNT_DEFAULT_HTTP_PROTOCOL = scheme

        # 5. Patch GRAFANA_EXTERNAL_URL for observability embeds
        grafana_base = f'{scheme}://{effective_domain}'
        settings.GRAFANA_EXTERNAL_URL = f'{grafana_base}/grafana'

        # 6. Sync Django Site table (required for allauth)
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Accessing the database during app initialization")
                site = Site.objects.get(id=settings.SITE_ID)
                if site.domain != effective_domain:
                    site.domain = effective_domain
                    site.name = f'Grid ({effective_domain})'
                    site.save()
        except Site.DoesNotExist:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Accessing the database during app initialization")
                Site.objects.create(
                    id=settings.SITE_ID,
                    domain=effective_domain,
                    name=f'Grid ({effective_domain})'
                )
        except Exception as site_exc:
            # django_site table may not exist on first boot (before
            # django.contrib.sites migrations run).  Skip silently —
            # the in-memory patches above are still applied.
            logger.debug("[patch] Skipped django_site sync (table may not exist yet): %s", site_exc)
        logger.debug("[patch] Runtime settings synchronized successfully.")

        # Write initial Prometheus target files for docker-labels
        try:
            from apps.deployments.services.prometheus_targets import (
                write_docker_labels_targets,
            )
            write_docker_labels_targets()
        except Exception as exc:
            logger.debug("[patch] Prometheus target init skipped: %s", exc)
    except Exception as exc:
        logger.warning("[patch] Runtime patching skipped or failed: %s", exc)
    finally:
        _patching_in_progress = False

def is_valid_host(host_str: str) -> bool:
    """
    Checks if an incoming HTTP host is explicitly authorized in the database.
    Used by middleware to dynamically append to ALLOWED_HOSTS.
    """
    if not host_str:
        return False

    domain = host_str.strip().lower()

    from django.conf import settings
    if domain in [h.strip().lower() for h in settings.ALLOWED_HOSTS]:
        return True

    # Allow IP address whitelist (public node IP or private/mesh IPs)
    import ipaddress
    import os
    try:
        ip = ipaddress.ip_address(domain)
        # Allow private / loopback IPs (e.g. WireGuard mesh, local docker networks)
        if ip.is_private or ip.is_loopback:
            return True
        # Allow if it matches SMSLY_NODE_HOST
        node_host = os.environ.get('SMSLY_NODE_HOST')
        if node_host and domain == node_host.strip().lower():
            return True
    except ValueError:
        pass

    # 1. PlatformConfig primary domain (and First-Run bypass)
    cfg = None
    try:
        from apps.deployments.models import PlatformConfig
        cfg = PlatformConfig.load()
        # Allow if it matches the server's public IP stored in PlatformConfig
        if cfg.server_ip and domain == cfg.server_ip.strip().lower():
            return True
        if not cfg.domain:
            # Chicken-and-egg fix: If the database is completely empty (no domain set),
            # we must trust the incoming host (which Caddy already allowed) so the user
            # can access the UI to run the initial setup.
            return True
        if cfg.domain and domain == cfg.domain.strip().lower():
            return True
    except Exception:
        pass

    # 2. Managed Servers (Nodes)
    try:
        from django.db.models import Q

        from apps.deployments.models_core import ManagedServer
        if ManagedServer.objects.filter(Q(host=domain) | Q(private_ip=domain)).exists():
            return True
    except Exception:
        pass

    # 3. Services (Public Domain)
    try:
        from apps.deployments.models import Service
        if Service.objects.filter(public_domain=domain).exists():
            return True
    except Exception:
        pass

    # 4. Verified Custom Domains
    try:
        from django.db.models import Q

        from apps.domains.models import Domain, DomainStatus
        routable = Domain.objects.filter(
            domain_name=domain,
            status__in=[DomainStatus.ACTIVE, DomainStatus.DNS_VERIFIED, DomainStatus.SSL_PROVISIONING]
        ).filter(Q(verified=True) | Q(status=DomainStatus.ACTIVE)).exists()
        if routable:
            return True
    except Exception:
        pass

    # 5. Addons
    try:
        from apps.deployments.models_addons import Addon
        if Addon.objects.filter(public_domain=domain).exists():
            return True
    except Exception:
        pass

    return False
