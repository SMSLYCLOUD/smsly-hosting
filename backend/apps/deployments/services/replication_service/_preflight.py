import logging

logger = logging.getLogger(__name__)


class PreflightMixin:

    @classmethod
    def preflight_check(cls, mesh, target_wg_address):
        import subprocess

        from apps.deployments.services.wireguard_service import WireGuardService

        target_peer = mesh.peers.filter(wg_address=target_wg_address, is_active=True).first()
        if not target_peer:
            raise ValueError(f"Target peer {target_wg_address} not found or inactive")

        try:
            subprocess.run(
                ["ping", "-c", "1", "-W", "3", target_wg_address],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            raise RuntimeError(f"Network check failed: Cannot ping WireGuard IP {target_wg_address}")

        if not target_peer.is_local and target_peer.server:
            try:
                script = """
                free -m | awk '/^Mem:/ {if ($2 < 1000) exit 1}';
                df -m /opt | awk 'NR==2 {if ($4 < 2000) exit 1}';
                command -v docker >/dev/null 2>&1 || exit 1;
                ss -tulpn | grep :55432 >/dev/null 2>&1 && exit 2 || exit 0;
                """
                WireGuardService._ssh_run(target_peer.server, script, timeout=10)
            except Exception as e:
                if "exit 2" in str(e):
                    raise RuntimeError(f"Port conflict detected: Port 55432 is already in use on {target_wg_address}. Patroni requires this port to be free.")
                raise RuntimeError(f"System requirement check failed: Ensure target has >1GB RAM, >2GB Disk, and Docker installed. ({e})")

        try:
            cls.generate_patroni_compose(mesh, "dummy", "dummy", "dummy")
            cls.generate_haproxy_compose(mesh)
        except Exception as e:
            raise RuntimeError(f"Config generation failed: {e}")

        return {"status": "ok", "message": "Pre-flight checks passed."}

    @classmethod
    def connect_replica(cls, mesh, target_wg_address, db_password, admin_password, replication_password="repl_pass"):
        target_peer = mesh.peers.filter(wg_address=target_wg_address, is_active=True).first()
        if not target_peer:
            raise ValueError(f"Target peer {target_wg_address} not found or inactive")

        configs = cls.generate_patroni_compose(
            mesh, db_password, admin_password, replication_password,
            is_fresh=False,
        )
        compose_content = configs.get(target_wg_address)
        if not compose_content:
            raise ValueError(f"No config generated for {target_wg_address}")

        haproxy_compose, haproxy_cfg = cls.generate_haproxy_compose(mesh)

        if target_peer.is_local:
            cls._deploy_patroni_local(compose_content)
        elif target_peer.server:
            cls._deploy_patroni_remote(target_peer.server, compose_content)
        else:
            raise ValueError(f"Target peer {target_wg_address} has no server assigned")

        try:
            cls._deploy_haproxy_local(haproxy_compose, haproxy_cfg)
        except Exception as e:
            logger.warning("HAProxy redeploy failed during scale-out: %s", e)

        return {"status": "Replica started", "wg_address": target_wg_address}
