"""Resource tracking and rollback for provisioning."""

import io
import logging
import os
import subprocess

import paramiko

from apps.deployments.models.servers import ManagedServer

from .helpers import _append_log

logger = logging.getLogger(__name__)


class _ProvisioningResources:
    def __init__(self, server: ManagedServer):
        self.server = server
        self._db_users: list[str] = []
        self._firewall_rules: list[tuple[str, str]] = []
        self._dns_domains: list[str] = []
        self._iptables_port5000_ips: list[str] = []
        self._ssh_key_added = False
        self._wg_peer_id: str | None = None

    def track_db_user(self, username: str):
        self._db_users.append(username)

    def track_firewall_rule(self, node_ip: str, port: str):
        self._firewall_rules.append((node_ip, port))

    def track_dns_domain(self, domain: str):
        self._dns_domains.append(domain)

    def track_iptables_port5000(self, ip: str):
        self._iptables_port5000_ips.append(ip)

    def track_ssh_key_added(self):
        self._ssh_key_added = True

    def track_wg_peer(self, peer_id: str):
        self._wg_peer_id = peer_id

    def rollback(self):
        for username in self._db_users:
            self._drop_db_user(username)
        for node_ip, port in self._firewall_rules:
            self._remove_firewall_rule(node_ip, port)
        for ip in self._iptables_port5000_ips:
            self._remove_iptables_port5000(ip)
        for domain in self._dns_domains:
            self._remove_dns_record(domain)
        if self._ssh_key_added:
            self._remove_ssh_key()
        if self._wg_peer_id:
            self._remove_wg_peer()
        # Clear sensitive fields from the server model
        try:
            update_fields = []
            if self.server.ssh_key:
                self.server.ssh_key = ""
                update_fields.append("ssh_key")
            if getattr(self.server, "node_db_password", None):
                self.server.node_db_password = ""
                update_fields.append("node_db_password")
            if self.server.gateway_secret:
                self.server.gateway_secret = ""
                update_fields.append("gateway_secret")
            if update_fields:
                update_fields.append("updated_at")
                self.server.save(update_fields=update_fields)
        except Exception as exc:
            logger.warning("Rollback: failed to clear sensitive server fields: %s", exc)

    def _drop_db_user(self, username: str):
        master_db_url = os.environ.get("DATABASE_URL")
        if not master_db_url:
            return
        try:
            import psycopg2
            from psycopg2 import sql
            from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
            conn = psycopg2.connect(master_db_url)
            try:
                conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                with conn.cursor() as cur:
                    cur.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(username)))
                    cur.execute(sql.SQL("DROP USER IF EXISTS {}").format(sql.Identifier(username)))
            finally:
                conn.close()
            _append_log(self.server, f"🧹 Rolled back DB user: {username}")
        except Exception as exc:
            logger.warning("Rollback: failed to drop DB user %s: %s", username, exc)

    def _remove_iptables_port5000(self, ip: str):
        try:
            subprocess.run(
                ["iptables", "-D", "DOCKER-USER",
                 "-s", ip, "-p", "tcp", "--dport", "5000",
                 "-j", "ACCEPT"],
                capture_output=True, timeout=5,
            )
            _append_log(self.server, f"🧹 Rolled back iptables port 5000 rule: {ip}")
        except Exception as exc:
            logger.warning("Rollback: failed to remove iptables rule for %s:5000: %s", ip, exc)

    def _remove_dns_record(self, domain: str):
        try:
            from apps.deployments.models.core import PlatformConfig
            from apps.domains.services.dns import delete_dns_record
            config = PlatformConfig.load()
            cf_token = config.cloudflare_api_token
            if not cf_token:
                return
            delete_dns_record(domain, cf_token)
            _append_log(self.server, f"🧹 Rolled back DNS record: {domain}")
        except Exception as exc:
            logger.warning("Rollback: failed to clean DNS record %s: %s", domain, exc)

    def _remove_firewall_rule(self, node_ip: str, port: str):
        try:
            subprocess.run(
                ["ufw", "delete", "allow", "from", node_ip,
                 "to", "any", "port", port, "proto", "tcp"],
                capture_output=True, timeout=5,
            )
            subprocess.run(
                ["iptables", "-D", "DOCKER-USER",
                 "-s", node_ip, "-p", "tcp", "--dport", "5000",
                 "-j", "ACCEPT"],
                capture_output=True, timeout=5,
            )
            _append_log(self.server, f"🧹 Rolled back firewall rule: {node_ip}:{port}")
        except Exception as exc:
            logger.warning("Rollback: failed to remove firewall rule %s:%s: %s", node_ip, port, exc)

    def _remove_ssh_key(self):
        if not self.server.ssh_key:
            return
        try:
            key_file = io.StringIO(self.server.ssh_key)
            try:
                pkey = paramiko.Ed25519Key.from_private_key(key_file)
            except Exception:
                key_file.seek(0)
                pkey = paramiko.RSAKey.from_private_key(key_file)
            client = paramiko.SSHClient()
            from apps.deployments.services.ssh_client import _get_tofu_policy
            client.set_missing_host_key_policy(_get_tofu_policy(self.server.host, self.server.ssh_port))
            connect_kwargs = {
                "hostname": self.server.host,
                "port": self.server.ssh_port,
                "username": self.server.ssh_user,
                "pkey": pkey,
                "timeout": 10,
            }
            if self.server.ssh_password:
                try:
                    client.connect(**connect_kwargs)
                except paramiko.AuthenticationException:
                    connect_kwargs.pop("pkey")
                    connect_kwargs["password"] = self.server.ssh_password
                    client.connect(**connect_kwargs)
            else:
                client.connect(**connect_kwargs)
            client.exec_command('sed -i "/smsly-self-heal/d" ~/.ssh/authorized_keys')
            client.close()
            _append_log(self.server, "🧹 Rolled back SSH key from remote node")
        except Exception as exc:
            logger.warning("Rollback: failed to remove SSH key: %s", exc)

    def _remove_wg_peer(self):
        try:
            from apps.deployments.models.mesh import WireGuardPeer
            from apps.deployments.services.wireguard_service import WireGuardService
            peer_obj = WireGuardPeer.objects.filter(id=self._wg_peer_id).first()
            if peer_obj:
                WireGuardService.remove_peer_from_mesh(peer_obj)
            _append_log(self.server, f"🧹 Rolled back WireGuard peer: {self._wg_peer_id}")
        except Exception as exc:
            logger.warning("Rollback: failed to remove WG peer %s: %s", self._wg_peer_id, exc)
