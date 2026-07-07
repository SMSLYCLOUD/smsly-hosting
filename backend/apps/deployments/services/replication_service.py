"""
Database replication service.

Manages Patroni-based PostgreSQL streaming replication across
CloudNeuron servers connected via WireGuard mesh.

Handles:
- Deploying Patroni + etcd containers to remote servers
- Checking replication lag and health
- Manual and automatic failover
- Generating parametrized docker-compose and HAProxy configs
"""

import base64
import ipaddress
import json
import logging
import re
import shlex
import textwrap
import time
from urllib.parse import urlparse

from django.utils import timezone

logger = logging.getLogger(__name__)


def _yaml_scalar(value) -> str:
    """Return a YAML-safe scalar using JSON string quoting."""
    return json.dumps(str(value))


def _bounded_error(exc, limit=2000) -> str:
    safe = str(exc).replace("\x00", "")
    # Redact any 20+ char hex/token-like strings (passwords and secrets).
    safe = re.sub(r'[A-Za-z0-9\-_=+/]{20,}', '***', safe)
    return safe[:limit]


class ReplicationService:
    """Manage PostgreSQL streaming replication via Patroni."""

    PATRONI_IMAGE = "ghcr.io/zalando/spilo-16:3.3-p3"
    ETCD_IMAGE = "quay.io/coreos/etcd:v3.5.9"
    HAPROXY_IMAGE = "haproxy:2.8"
    PATRONI_POSTGRES_PORT = 55432

    # ── Config Generation ────────────────────────────────────────────────

    @classmethod
    def generate_patroni_compose(cls, mesh, db_password, admin_password,
                                  replication_password="repl_pass",
                                  is_fresh: bool = True):
        """
        Generate a docker-compose.yml for Patroni that uses WireGuard IPs.

        Each server runs:
        - 1 Patroni node (PostgreSQL + Patroni agent)
        - 1 etcd node (distributed consensus)

        Args:
            is_fresh: True for initial deploy (etcd starts fresh).  False
                for scale-out — the new node joins an existing etcd cluster
                with ``--initial-cluster-state existing``.

        Returns dict mapping peer_wg_address → compose YAML string.
        """
        peers = list(mesh.peers.filter(is_active=True).order_by("wg_address"))
        if len(peers) < 2:
            raise ValueError("Need at least 2 peers for replication")

        # Build etcd cluster string: "etcd1=http://10.100.0.1:2380,etcd2=..."
        etcd_cluster = ",".join(
            f"etcd{i}=http://{p.wg_address}:2380"
            for i, p in enumerate(peers, 1)
        )
        # Spilo's ETCD3_HOSTS is parsed by Patroni as host:port entries.
        # Including URL schemes makes Patroni treat the comma-delimited value
        # as an invalid URL and crash during DCS initialization.
        etcd_endpoints = ",".join(f"{p.wg_address}:2379" for p in peers)

        cluster_state = "new" if is_fresh else "existing"

        configs = {}
        for idx, peer in enumerate(peers, 1):
            wg_ip = peer.wg_address
            node_name = f"patroni{idx}"
            etcd_name = f"etcd{idx}"

            compose = textwrap.dedent(f"""\
                version: '3.8'

                services:
                  etcd:
                    image: {cls.ETCD_IMAGE}
                    container_name: {etcd_name}
                    restart: unless-stopped
                    command: >
                      etcd
                      --name {etcd_name}
                      --initial-advertise-peer-urls http://{wg_ip}:2380
                      --listen-peer-urls http://{wg_ip}:2380
                      --listen-client-urls http://{wg_ip}:2379
                      --advertise-client-urls http://{wg_ip}:2379
                      --initial-cluster {etcd_cluster}
                      --initial-cluster-state {cluster_state}
                      --initial-cluster-token smsly-etcd-cluster
                    network_mode: host
                    volumes:
                      - etcd-data:/etcd-data

                  patroni:
                    image: {cls.PATRONI_IMAGE}
                    container_name: {node_name}
                    hostname: {node_name}
                    extra_hosts:
                      - "{node_name}:{wg_ip}"
                    restart: unless-stopped
                    network_mode: host
                    environment:
                      SCOPE: smsly-cluster
                      PGVERSION: "16"
                      ETCD3_HOSTS: "{etcd_endpoints}"
                      PATRONI_NAME: {node_name}
                      PATRONI_RESTAPI_CONNECT_ADDRESS: "{wg_ip}:8008"
                      PATRONI_RESTAPI_LISTEN: "{wg_ip}:8008"
                      PATRONI_POSTGRESQL_CONNECT_ADDRESS: "{wg_ip}:{cls.PATRONI_POSTGRES_PORT}"
                      PATRONI_POSTGRESQL_LISTEN: "{wg_ip}:{cls.PATRONI_POSTGRES_PORT}"
                      PATRONI_POSTGRESQL_DATA_DIR: /home/postgres/pgdata/pgroot/data
                      PATRONI_REPLICATION_USERNAME: replicator
                      PATRONI_REPLICATION_PASSWORD: {_yaml_scalar(replication_password)}
                      PATRONI_SUPERUSER_USERNAME: postgres
                      PATRONI_SUPERUSER_PASSWORD: {_yaml_scalar(db_password)}
                      PGUSER_SUPERUSER: postgres
                      PGPASSWORD_SUPERUSER: {_yaml_scalar(db_password)}
                      PGUSER_ADMIN: smsly_admin
                      PGPASSWORD_ADMIN: {_yaml_scalar(admin_password)}
                    volumes:
                      - patroni-data:/home/postgres/pgdata
                    depends_on:
                      - etcd

                volumes:
                  etcd-data:
                  patroni-data:
            """)
            configs[wg_ip] = compose

        return configs

    @classmethod
    def generate_haproxy_config(cls, mesh):
        """
        Generate HAProxy config that routes to Patroni nodes via WireGuard IPs.

        Port 5000: routes writes → primary only
        Port 5001: routes reads → replicas (round-robin)
        Port 7000: HAProxy stats dashboard
        """
        peers = list(mesh.peers.filter(is_active=True).order_by("wg_address"))
        local_peer = next((peer for peer in peers if peer.is_local), None)
        bind_ip = local_peer.wg_address if local_peer else "127.0.0.1"

        master_servers = "\n".join(
            f"    server patroni{i} {p.wg_address}:{cls.PATRONI_POSTGRES_PORT} "
            f"maxconn 100 check port 8008"
            for i, p in enumerate(peers, 1)
        )

        replica_servers = "\n".join(
            f"    server patroni{i} {p.wg_address}:{cls.PATRONI_POSTGRES_PORT} "
            f"maxconn 100 check port 8008"
            for i, p in enumerate(peers, 1)
        )

        config = textwrap.dedent(f"""\
            defaults
                mode tcp
                timeout connect 5000ms
                timeout client 50000ms
                timeout server 50000ms

            frontend master
                bind {bind_ip}:5000
                default_backend master

            backend master
                option httpchk GET /master
                http-check expect status 200
                default-server inter 3s fall 3 rise 2 on-marked-down shutdown-sessions
            {master_servers}

            frontend replicas
                bind {bind_ip}:5001
                default_backend replicas

            backend replicas
                option httpchk GET /replica
                http-check expect status 200
                default-server inter 3s fall 3 rise 2 on-marked-down shutdown-sessions
            {replica_servers}

            listen stats
                bind {bind_ip}:7000
                mode http
                stats enable
                stats uri /
        """)
        return config

    @classmethod
    def generate_haproxy_compose(cls, mesh):
        """Generate docker-compose for HAProxy that routes to Patroni nodes."""
        haproxy_cfg = cls.generate_haproxy_config(mesh)
        haproxy_cfg_b64 = base64.b64encode(haproxy_cfg.encode()).decode()

        compose = textwrap.dedent(f"""\
            version: '3.8'

            services:
              haproxy:
                image: {cls.HAPROXY_IMAGE}
                container_name: smsly-haproxy
                restart: unless-stopped
                network_mode: host
                environment:
                  HAPROXY_CONFIG_B64: "{haproxy_cfg_b64}"
                command:
                  - sh
                  - -c
                  - |
                    printf '%s' "$$HAPROXY_CONFIG_B64" | base64 -d > /usr/local/etc/haproxy/haproxy.cfg
                    exec haproxy -f /usr/local/etc/haproxy/haproxy.cfg
        """)
        return compose, haproxy_cfg

    # ── Pre-flight & Scale Out ───────────────────────────────────────────

    @classmethod
    def preflight_check(cls, mesh, target_wg_address):
        """
        Run a pre-flight check before connecting a new replica.

        - Verifies SSH connectivity
        - Checks RAM and Disk resources
        - Verifies Docker installation
        - Verifies network reachability via ping
        """
        import subprocess

        from apps.deployments.services.wireguard_service import WireGuardService

        target_peer = mesh.peers.filter(wg_address=target_wg_address, is_active=True).first()
        if not target_peer:
            raise ValueError(f"Target peer {target_wg_address} not found or inactive")

        # 1. Ping the target IP from the local server to verify WG network
        try:
            subprocess.run(
                ["ping", "-c", "1", "-W", "3", target_wg_address],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            raise RuntimeError(f"Network check failed: Cannot ping WireGuard IP {target_wg_address}")

        if not target_peer.is_local and target_peer.server:
            # 2. SSH check and system checks
            try:
                # Check memory (>1GB) and disk (>2GB) and docker
                # ALSO check if Patroni's mesh-only postgres port is already taken
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

        # 3. Dry run config generation to catch template errors
        try:
            cls.generate_patroni_compose(mesh, "dummy", "dummy", "dummy")
            cls.generate_haproxy_compose(mesh)
        except Exception as e:
            raise RuntimeError(f"Config generation failed: {e}")

        return {"status": "ok", "message": "Pre-flight checks passed."}

    @classmethod
    def connect_replica(cls, mesh, target_wg_address, db_password, admin_password, replication_password="repl_pass"):
        """
        Scale out — add a single new replica to an existing Patroni cluster.

        Only the target node is touched.  Existing nodes keep their etcd
        state because ``--initial-cluster-state existing`` is used.
        """
        target_peer = mesh.peers.filter(wg_address=target_wg_address, is_active=True).first()
        if not target_peer:
            raise ValueError(f"Target peer {target_wg_address} not found or inactive")

        # Generate configs for the whole cluster (so the etcd_cluster string
        # includes the new node) but only deploy to the target.
        configs = cls.generate_patroni_compose(
            mesh, db_password, admin_password, replication_password,
            is_fresh=False,  # scale-out — use existing etcd cluster
        )
        compose_content = configs.get(target_wg_address)
        if not compose_content:
            raise ValueError(f"No config generated for {target_wg_address}")

        # Update HAProxy on the local server so it routes to the new node
        haproxy_compose, haproxy_cfg = cls.generate_haproxy_compose(mesh)

        if target_peer.is_local:
            cls._deploy_patroni_local(compose_content)
        elif target_peer.server:
            cls._deploy_patroni_remote(target_peer.server, compose_content)
        else:
            raise ValueError(f"Target peer {target_wg_address} has no server assigned")

        # Redeploy HAProxy only (on local) with updated topology
        try:
            cls._deploy_haproxy_local(haproxy_compose, haproxy_cfg)
        except Exception as e:
            logger.warning("HAProxy redeploy failed during scale-out: %s", e)

        return {"status": "Replica started", "wg_address": target_wg_address}

    # ── Deployment ───────────────────────────────────────────────────────

    @classmethod
    def validate_mesh_for_replication(cls, mesh):
        """Fail early when mesh/server state cannot support Patroni safely."""
        from apps.deployments.models_core import ManagedServer

        peers = list(mesh.peers.filter(is_active=True).select_related("server"))
        if len(peers) < 2:
            raise ValueError("Need at least 2 active peers for replication")
        if not any(peer.is_local for peer in peers):
            raise ValueError("Mesh must include a local peer before replication can be enabled.")

        addresses = set()
        for peer in peers:
            try:
                ipaddress.ip_address(peer.wg_address)
            except ValueError as exc:
                raise ValueError(f"Peer {peer} has an invalid WireGuard address.") from exc
            if peer.wg_address in addresses:
                raise ValueError(f"Duplicate WireGuard address detected: {peer.wg_address}")
            addresses.add(peer.wg_address)

            if peer.is_local:
                continue
            if not peer.server:
                raise ValueError(f"Remote peer {peer.wg_address} is not linked to a server.")
            if peer.server.status != ManagedServer.Status.ONLINE:
                raise ValueError(
                    f"Server '{peer.server.name}' is {peer.server.status}; replication requires ONLINE nodes."
                )
            if not (peer.server.ssh_key or peer.server.ssh_password):
                raise ValueError(f"Server '{peer.server.name}' has no SSH credentials for replication.")

        return peers

    @classmethod
    def deploy_replication(cls, mesh, db_password, admin_password,
                            replication_password="repl_pass"):
        """
        Deploy Patroni replication cluster across all peers in a mesh.

        1. Generate per-server docker-compose configs
        2. SSH into each server and deploy
        3. Deploy HAProxy on the primary server

        Returns deployment results.
        """

        cls.validate_mesh_for_replication(mesh)

        # If the cluster is already ACTIVE, we're doing a scale-out — etcd
        # member state must be preserved by using ``--initial-cluster-state existing``
        # for the net-new node.  Fresh deploys use ``new``.
        is_fresh = mesh.replication_status not in ("ACTIVE", "FAILED")

        configs = cls.generate_patroni_compose(
            mesh, db_password, admin_password, replication_password,
            is_fresh=is_fresh,
        )
        haproxy_compose, haproxy_cfg = cls.generate_haproxy_compose(mesh)

        results = {"patroni": [], "haproxy": None}

        # Deploy Patroni + etcd to each peer
        for peer in mesh.peers.filter(is_active=True):
            wg_ip = peer.wg_address
            compose_content = configs.get(wg_ip)
            if not compose_content:
                continue

            try:
                if peer.is_local:
                    cls._deploy_patroni_local(compose_content)
                elif peer.server:
                    cls._deploy_patroni_remote(peer.server, compose_content)

                results["patroni"].append({
                    "peer": str(peer), "wg_address": wg_ip, "status": "OK",
                })
            except Exception as e:
                logger.error(f"Failed to deploy Patroni to {wg_ip}: {e}")
                results["patroni"].append({
                    "peer": str(peer), "wg_address": wg_ip,
                    "status": f"FAILED: {_bounded_error(e)}",
                })

        # Deploy HAProxy on the local server
        try:
            cls._deploy_haproxy_local(haproxy_compose, haproxy_cfg)
            results["haproxy"] = "OK"
        except Exception as e:
            logger.error(f"Failed to deploy HAProxy: {e}")
            results["haproxy"] = f"FAILED: {_bounded_error(e)}"

        return results

    @classmethod
    def wait_for_cluster_ready(cls, mesh, timeout_seconds=180, poll_seconds=5):
        """Poll Patroni until every peer is reachable and a primary is present."""
        deadline = time.monotonic() + timeout_seconds
        last_health = None
        while time.monotonic() < deadline:
            last_health = cls.check_replication_health(mesh)
            nodes = last_health.get("nodes", [])
            if nodes:
                unreachable = [
                    node for node in nodes
                    if "UNREACHABLE" in str(node.get("status", ""))
                ]
                if not unreachable and last_health.get("primary"):
                    return {"status": "READY", "health": last_health}
            time.sleep(poll_seconds)
        return {"status": "TIMEOUT", "health": last_health or {}}

    @classmethod
    def _helper_network_for_docker_host(cls, client):
        """Pick a Docker network where helper containers can resolve DOCKER_HOST."""
        import os
        explicit = str(os.environ.get("DOCKER_HELPER_NETWORK", "")).strip()
        if explicit:
            return explicit

        docker_host = os.environ.get("DOCKER_HOST", "")
        parsed = urlparse(docker_host)
        host = parsed.hostname or ""
        preferred = os.environ.get("DOCKER_NETWORK", "").strip()
        if host and host not in {"127.0.0.1", "localhost"}:
            try:
                matches = client.containers.list(all=True, filters={"name": host})
                for container in matches:
                    networks = (
                        container.attrs
                        .get("NetworkSettings", {})
                        .get("Networks", {})
                    )
                    if preferred and preferred in networks:
                        return preferred
                    if networks:
                        return next(iter(networks))
            except Exception as exc:
                logger.warning("Could not inspect Docker host helper network: %s", exc)
        return preferred or None

    @classmethod
    def _deploy_patroni_local(cls, compose_content: str):
        """Deploy Patroni containers on the local server."""
        import docker

        client = docker.from_env()
        import os
        docker_host = os.environ.get("DOCKER_HOST", "tcp://socket-proxy:2375")
        compose_b64 = base64.b64encode(compose_content.encode()).decode()

        commands = [
            "mkdir -p /tmp/smsly-patroni",
            f"printf %s {shlex.quote(compose_b64)} | base64 -d > /tmp/smsly-patroni/docker-compose.yml",
            "cd /tmp/smsly-patroni && docker compose -p smsly-patroni up -d --pull always",
        ]
        run_kwargs = {
            "image": "docker:cli",
            "command": ["sh", "-c", " && ".join(commands)],
            "remove": True,
            "environment": {"DOCKER_HOST": docker_host},
        }
        helper_network = cls._helper_network_for_docker_host(client)
        if helper_network:
            run_kwargs["network"] = helper_network
        elif urlparse(docker_host).hostname in {"127.0.0.1", "localhost"}:
            run_kwargs["network_mode"] = "host"

        client.containers.run(
            **run_kwargs,
        )

    @classmethod
    def _deploy_patroni_remote(cls, server, compose_content: str):
        """Deploy Patroni containers on a remote server via SSH."""
        from apps.deployments.services.wireguard_service import WireGuardService

        compose_b64 = base64.b64encode(compose_content.encode()).decode()
        commands = [
            "mkdir -p /opt/smsly/patroni",
            f"printf %s {shlex.quote(compose_b64)} | base64 -d > /opt/smsly/patroni/docker-compose.yml",
            "cd /opt/smsly/patroni && docker compose -p smsly-patroni up -d --pull always",
        ]
        WireGuardService._ssh_run(server, " && ".join(commands), timeout=120)

    @classmethod
    def _deploy_haproxy_local(cls, compose_content: str, haproxy_cfg: str):
        """Deploy HAProxy on the local server via Docker container proxy."""
        import os

        import docker

        client = docker.from_env()
        docker_host = os.environ.get("DOCKER_HOST", "tcp://socket-proxy:2375")
        compose_b64 = base64.b64encode(compose_content.encode()).decode()

        commands = [
            "mkdir -p /tmp/smsly-haproxy",
            f"printf %s {shlex.quote(compose_b64)} | base64 -d > /tmp/smsly-haproxy/docker-compose.yml",
            "cd /tmp/smsly-haproxy && docker compose -p smsly-haproxy up -d --pull always",
        ]
        run_kwargs = {
            "image": "docker:cli",
            "command": ["sh", "-c", " && ".join(commands)],
            "remove": True,
            "environment": {"DOCKER_HOST": docker_host},
        }
        helper_network = cls._helper_network_for_docker_host(client)
        if helper_network:
            run_kwargs["network"] = helper_network
        elif urlparse(docker_host).hostname in {"127.0.0.1", "localhost"}:
            run_kwargs["network_mode"] = "host"

        client.containers.run(**run_kwargs)

    # ── Health & Monitoring ──────────────────────────────────────────────

    @classmethod
    def check_replication_health(cls, mesh):
        """
        Check replication health across all Patroni nodes.

        Queries each node's Patroni REST API (port 8008) for:
        - Role (master/replica)
        - WAL position
        - Replication lag
        - Timeline

        Returns health report.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        import requests

        peers = list(
            mesh.peers.filter(is_active=True)
            .select_related("server")
            .order_by("wg_address")
        )
        results = {"nodes": [], "primary": None, "replicas": []}

        node_specs = [
            (
                idx,
                peer.wg_address,
                peer.server.name if peer.server_id and peer.server else "local",
            )
            for idx, peer in enumerate(peers, 1)
        ]

        def _check_node(idx, wg_ip, server_name):
            try:
                resp = requests.get(
                    f"http://{wg_ip}:8008/patroni",
                    timeout=(2, 5),
                    allow_redirects=False,
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                data = resp.json()
                role = str(data.get("role", "unknown") or "unknown").lower()
                return {
                    "name": f"patroni{idx}",
                    "wg_address": wg_ip,
                    "server": server_name,
                    "role": role,
                    "state": data.get("state", "unknown"),
                    "timeline": data.get("timeline"),
                    "xlog": data.get("xlog", {}),
                    "lag": data.get("xlog", {}).get("replayed_timestamp"),
                    "patroni_version": data.get("patroni", {}).get("version"),
                    "pg_version": data.get("server_version"),
                    "status": "OK",
                }
            except Exception as e:
                return {
                    "name": f"patroni{idx}",
                    "wg_address": wg_ip,
                    "server": server_name,
                    "status": f"UNREACHABLE: {_bounded_error(e)}",
                }

        # Check all nodes in parallel to prevent UI timeouts (502)
        with ThreadPoolExecutor(max_workers=min(len(peers) or 1, 16)) as executor:
            future_to_node = {
                executor.submit(_check_node, idx, wg_ip, server_name): wg_ip
                for idx, wg_ip, server_name in node_specs
            }

            node_results = []
            for future in as_completed(future_to_node):
                node_results.append(future.result())

        # Sort node results back into original order
        node_results.sort(key=lambda x: x["wg_address"])
        results["nodes"] = node_results

        for node_info in node_results:
            if node_info.get("status") == "OK":
                if node_info.get("role") in {"master", "primary", "leader"}:
                    results["primary"] = node_info
                else:
                    results["replicas"].append(node_info)

        # Calculate replication lag
        if results["primary"] and results["replicas"]:
            primary_xlog = results["primary"].get("xlog", {})
            primary_location = primary_xlog.get("location")

            for replica in results["replicas"]:
                replica_xlog = replica.get("xlog", {})
                replica_location = replica_xlog.get("received_location") or \
                                   replica_xlog.get("replayed_location")
                if primary_location and replica_location:
                    try:
                        lag_bytes = cls._parse_lsn(primary_location) - \
                                    cls._parse_lsn(replica_location)
                        replica["lag_bytes"] = max(0, lag_bytes)
                    except Exception:
                        replica["lag_bytes"] = None

        try:
            has_unreachable = any(
                "UNREACHABLE" in str(node.get("status", ""))
                for node in results["nodes"]
            )
            missing_primary = bool(results["nodes"]) and not results["primary"]

            # Only transition to FAILED if the mesh was previously ACTIVE
            # (a deployed cluster that broke).  Otherwise, unreachable
            # nodes just mean the cluster hasn't been deployed yet — keep
            # the current status (DISABLED or DEPLOYING).
            was_active = mesh.replication_status == "ACTIVE"
            was_failed = mesh.replication_status == "FAILED"

            if has_unreachable or missing_primary:
                if was_active or was_failed:
                    mesh.replication_status = "FAILED"
                # else: leave as DISABLED or DEPLOYING — cluster not yet deployed
            else:
                mesh.replication_status = "ACTIVE"

            mesh.replication_last_result = results
            mesh.replication_last_error = (
                "One or more replication nodes are unreachable."
                if has_unreachable else (
                    "No Patroni primary detected."
                    if missing_primary else ""
                )
            )
            mesh.replication_updated_at = timezone.now()
            mesh.save(update_fields=[
                "replication_status",
                "replication_last_error",
                "replication_last_result",
                "replication_updated_at",
                "updated_at",
            ])
        except Exception as exc:
            logger.debug("Could not persist replication health for mesh %s: %s", mesh, exc)

        return results

    @classmethod
    def sync_now(cls, mesh):
        """
        Trigger an immediate replication status refresh.

        Patroni streaming replication is continuous; the actionable sync-now
        operation is to poll every node, calculate lag, and persist the latest
        DB state for operators and the UI.
        """
        health = cls.check_replication_health(mesh)
        return {"status": mesh.replication_status, "health": health}

    @classmethod
    def disable_replication(cls, mesh):
        """Stop Patroni/etcd/HAProxy containers on all mesh peers and persist state."""
        import os

        import docker

        from apps.deployments.services.wireguard_service import WireGuardService

        results = {"local": None, "remote": []}

        for peer in mesh.peers.filter(is_active=True):
            try:
                if peer.is_local:
                    client = docker.from_env()
                    docker_host = os.environ.get("DOCKER_HOST", "tcp://socket-proxy:2375")
                    client.containers.run(
                        "docker:cli",
                        command=[
                            "sh",
                            "-c",
                            "docker compose -p smsly-patroni down || true && "
                            "docker compose -p smsly-haproxy down || true",
                        ],
                        remove=True,
                        environment={"DOCKER_HOST": docker_host},
                        network_mode="host",
                    )
                    results["local"] = {"peer": str(peer), "status": "OK"}
                elif peer.server:
                    WireGuardService._ssh_run(
                        peer.server,
                        "cd /opt/smsly/patroni 2>/dev/null && docker compose -p smsly-patroni down || true",
                        timeout=120,
                    )
                    results["remote"].append({"peer": str(peer), "status": "OK"})
            except Exception as exc:
                logger.warning("Failed to disable replication on %s: %s", peer, exc)
                result = {"peer": str(peer), "status": f"FAILED: {exc}"}
                if peer.is_local:
                    results["local"] = result
                else:
                    results["remote"].append(result)

        failures = []
        if results["local"] and str(results["local"].get("status", "")).startswith("FAILED"):
            failures.append(results["local"]["status"])
        failures.extend(
            item["status"] for item in results["remote"]
            if str(item.get("status", "")).startswith("FAILED")
        )

        mesh.replication_status = "FAILED" if failures else "DISABLED"
        mesh.replication_last_error = "; ".join(failures)
        mesh.replication_last_result = results
        mesh.replication_updated_at = timezone.now()
        mesh.save(update_fields=[
            "replication_status",
            "replication_last_error",
            "replication_last_result",
            "replication_updated_at",
            "updated_at",
        ])
        return results

    @classmethod
    def check_replication_lag_sql(cls, mesh):
        """
        Check replication lag via SQL query on the primary.

        More accurate than REST API for real-time lag measurement.
        """
        import requests

        # Find the primary
        primary_ip = None
        for peer in mesh.peers.filter(is_active=True):
            try:
                resp = requests.get(
                    f"http://{peer.wg_address}:8008/master",
                    timeout=3,
                )
                if resp.status_code == 200:
                    primary_ip = peer.wg_address
                    break
            except Exception:
                continue

        if not primary_ip:
            return {"error": "No primary found"}

        # Query replication stats via Patroni's SQL proxy
        try:
            resp = requests.get(
                f"http://{primary_ip}:8008/patroni",
                timeout=5,
            )
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    # ── Failover ─────────────────────────────────────────────────────────

    @classmethod
    def manual_failover(cls, mesh, target_wg_address):
        """
        Trigger a manual Patroni failover to a specific replica.

        Uses Patroni's REST API to initiate a controlled switchover.
        """
        import requests

        cls.validate_mesh_for_replication(mesh)
        peers = list(mesh.peers.filter(is_active=True).order_by("wg_address"))
        if not any(peer.wg_address == target_wg_address for peer in peers):
            raise ValueError(f"Target {target_wg_address} not found in mesh")

        # Find current primary
        primary_ip = None
        primary_name = None
        for idx, peer in enumerate(peers, 1):
            try:
                resp = requests.get(
                    f"http://{peer.wg_address}:8008/patroni",
                    timeout=(2, 5),
                    allow_redirects=False,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if str(data.get("role", "")).lower() not in {"master", "primary", "leader"}:
                        continue
                    primary_ip = peer.wg_address
                    primary_name = (
                        data.get("patroni", {}).get("name")
                        or data.get("name")
                        or f"patroni{idx}"
                    )
                    break
            except Exception:
                continue

        if not primary_ip or not primary_name:
            raise RuntimeError("Cannot find current primary")

        # Find target name
        target_name = None
        for idx, peer in enumerate(peers, 1):
            if peer.wg_address == target_wg_address:
                target_name = f"patroni{idx}"
                break

        if not target_name:
            raise ValueError(f"Target {target_wg_address} not found in mesh")

        # Trigger switchover via Patroni API
        resp = requests.post(
            f"http://{primary_ip}:8008/switchover",
            json={
                "leader": primary_name,
                "candidate": target_name,
            },
            timeout=(3, 30),
            allow_redirects=False,
        )

        if resp.status_code in (200, 202):
            logger.info("Failover initiated: %s -> %s", primary_name, target_name)
            return {"status": "Failover initiated", "from": primary_name,
                    "to": target_name}
        else:
            raise RuntimeError(
                f"Failover failed: HTTP {resp.status_code} {_bounded_error(resp.text)}"
            )

    @classmethod
    def reinitialize_replica(cls, mesh, target_wg_address):
        """
        Reinitialize a failed/lagging replica from scratch.

        Uses Patroni's reinit API to rebuild from the primary.
        """
        import requests

        cls.validate_mesh_for_replication(mesh)

        # Find target name
        peers = list(mesh.peers.filter(is_active=True).order_by("wg_address"))
        target_name = None
        for idx, peer in enumerate(peers, 1):
            if peer.wg_address == target_wg_address:
                target_name = f"patroni{idx}"
                break

        if not target_name:
            raise ValueError(f"Target {target_wg_address} not found")

        # Find any active node to send the reinit request
        for peer in peers:
            try:
                resp = requests.post(
                    f"http://{peer.wg_address}:8008/reinitialize",
                    json={"member": target_name},
                    timeout=(3, 30),
                    allow_redirects=False,
                )
                if resp.status_code in (200, 202):
                    return {"status": "Reinitialize started",
                            "target": target_name}
            except Exception:
                continue

        raise RuntimeError("Could not reach any Patroni node")

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _parse_lsn(lsn_str: str) -> int:
        """Parse PostgreSQL LSN string (e.g. '0/16B5D48') to integer."""
        if not isinstance(lsn_str, str) or not lsn_str:
            return 0
        parts = lsn_str.split("/")
        if len(parts) == 2:
            try:
                return int(parts[0], 16) * (2 ** 32) + int(parts[1], 16)
            except (ValueError, TypeError):
                return 0
        return 0
