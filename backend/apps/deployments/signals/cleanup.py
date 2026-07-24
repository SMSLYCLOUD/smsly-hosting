import logging
import os

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from ..models import ManagedServer, Service
from ..models.addons import Addon
from ..models.bundles import Bundle
from ..models.storage import Volume

log = logging.getLogger(__name__)


@receiver(pre_delete, sender=Service)
def remove_service_docker_volumes_on_delete(_sender, instance, **kwargs):
    volumes = list(instance.volumes.all())
    if not volumes:
        return
    try:
        import docker
        client = docker.from_env()
    except Exception as exc:
        log.warning("Docker SDK unavailable on service delete: %s", exc)
        return
    for vol in volumes:
        try:
            docker_vol = client.volumes.get(vol.name)
            docker_vol.remove(force=True)
            log.info("Removed docker volume %s for service %s", vol.name, instance.name)
        except Exception as exc:
            log.debug("Failed to remove docker volume %s: %s", vol.name, exc)


@receiver(pre_delete, sender=Addon)
def deprovision_addon_on_delete(_sender, instance, **kwargs):
    log = logging.getLogger(__name__)
    try:
        from ..tasks import deprovision_addon_task
        try:
            deprovision_addon_task.delay(str(instance.id))
        except Exception:
            deprovision_addon_task(str(instance.id))
        log.info(
            "Dispatched deprovision_addon_task for addon %s on pre_delete",
            instance.id,
        )
    except Exception as exc:
        log.warning(
            "Failed to dispatch deprovision_addon_task for addon %s: %s",
            instance.id, exc,
        )
    try:
        volume_name = (
            f"smsly-addon-{instance.addon_type.lower()}-{instance.id}"
        )
        import docker
        client = docker.from_env()
        docker_vol = client.volumes.get(volume_name)
        docker_vol.remove(force=True)
        log.info("Removed addon docker volume %s on pre_delete", volume_name)
    except Exception:
        pass


@receiver(pre_delete, sender=Bundle)
def deprovision_bundle_on_delete(_sender, instance, **kwargs):
    log = logging.getLogger(__name__)
    try:
        from ..tasks.deployment.tasks_bundles import deprovision_bundle_task
        bundle_name = instance.name
        service_id = str(instance.service_id)
        network_name = instance.network or ''
        try:
            deprovision_bundle_task.delay(
                str(instance.id),
                bundle_name=bundle_name,
                service_id=service_id,
                network_name=network_name,
            )
        except Exception:
            deprovision_bundle_task(
                str(instance.id),
                bundle_name=bundle_name,
                service_id=service_id,
                network_name=network_name,
            )
        log.info(
            "Dispatched deprovision_bundle_task for bundle %s on pre_delete",
            instance.id,
        )
    except Exception as exc:
        log.warning(
            "Failed to dispatch deprovision_bundle_task for bundle %s: %s",
            instance.id, exc,
        )


@receiver(pre_delete, sender=ManagedServer)
def cleanup_managed_server_artifacts(_sender, instance, **kwargs):
    log = logging.getLogger(__name__)
    log.info("Cleaning up provision artifacts for server %s (%s)", instance.name, instance.host)

    try:
        from ..services.wireguard_service import WireGuardService
        for peer in list(instance.wg_peers.all()):
            WireGuardService.remove_peer_from_mesh(peer)
    except Exception as exc:
        log.warning("WG peer cleanup failed for server %s: %s", instance.id, exc)

    if instance.host:
        _cleanup_server_firewall_rules(instance)

    if getattr(instance, "is_lite_agent", False):
        _drop_server_db_user(instance)

    if not getattr(instance, "is_lite_agent", False) and not getattr(instance, "is_primary", False):
        _cleanup_server_dns_record(instance)

    log.info("Cleanup finished for server %s", instance.id)


def _cleanup_server_firewall_rules(server):
    import contextlib
    import subprocess
    try:
        import ipaddress
        validated_ip = str(ipaddress.ip_address(server.host))
    except ValueError:
        return
    if getattr(server, "is_lite_agent", False):
        for port in ("5432", "6379", "5672"):
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["ufw", "delete", "allow", "from", validated_ip,
                     "to", "any", "port", port, "proto", "tcp"],
                    capture_output=True, timeout=5,
                )
    with contextlib.suppress(Exception):
        subprocess.run(
            ["iptables", "-D", "DOCKER-USER",
             "-s", validated_ip, "-p", "tcp", "--dport", "5000",
             "-j", "ACCEPT"],
            capture_output=True, timeout=5,
        )


def _drop_server_db_user(server):
    node_db_user = (server.provider_metadata or {}).get("node_db_user")
    if not node_db_user:
        return
    master_db_url = os.environ.get("DATABASE_URL")
    if not master_db_url:
        return
    try:
        import psycopg2
        from psycopg2 import sql
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        with psycopg2.connect(master_db_url) as conn:
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with conn.cursor() as cur:
                cur.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(node_db_user)))
                cur.execute(sql.SQL("DROP USER IF EXISTS {}").format(sql.Identifier(node_db_user)))
        log = logging.getLogger(__name__)
        log.info("Dropped DB user %s for server %s", node_db_user, server.id)
    except Exception as exc:
        log = logging.getLogger(__name__)
        log.warning("Failed to drop DB user %s: %s", node_db_user, exc)


def _cleanup_server_dns_record(server):
    from ..models.core import PlatformConfig
    from ..services.dns import delete_dns_record
    log = logging.getLogger(__name__)
    config = PlatformConfig.load()
    cf_token = config.cloudflare_api_token
    root_domain = config.domain
    if not cf_token or not root_domain:
        return
    node_slug = str(server.id).split("-")[0]
    node_domain = f"node-{node_slug}.{root_domain}"
    try:
        if delete_dns_record(node_domain, cf_token):
            log.info("Deleted DNS record %s for server %s", node_domain, server.id)
        else:
            log.warning("Failed to delete DNS record %s for server %s", node_domain, server.id)
    except Exception as exc:
        log.warning("DNS cleanup error for %s: %s", node_domain, exc)
