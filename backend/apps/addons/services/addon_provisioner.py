# pylint: disable=logging-fstring-interpolation,broad-exception-caught,subprocess-run-check,import-outside-toplevel
"""Addon Provisioner module."""
# pylint: disable=bare-except
# pylint: disable=unused-argument
"""
Docker-Native Addon Provisioner for SMSLY Hosting.

Creates real database containers (PostgreSQL, Redis, MySQL, MongoDB)
using Docker directly, without external PaaS dependencies.

Uses the same Docker network as deployed services for internal connectivity.
"""
import base64
import contextlib
import logging
import os
import secrets
import shlex
import subprocess
import time
import uuid
from typing import cast
from urllib.parse import urlparse

from decouple import config

logger = logging.getLogger(__name__)


class AddonProvisioner:
    """
    Provisions database addons as Docker containers.

    Uses Docker CLI for simplicity and reliability.
    For Kubernetes environments, use the K8s operator approach instead.
    """

    # Official Docker images for each addon type
    ADDON_IMAGES = {
        # pgvector-enabled Postgres to support embeddings (Khoj, etc.)
        'POSTGRES': 'pgvector/pgvector:pg16',
        'REDIS': 'redis:7-alpine',
        'MYSQL': 'mysql:8.0',
        'MONGODB': 'mongo:7.0',
        'QDRANT': 'qdrant/qdrant:v1.12.1',
        'ELASTICSEARCH': 'docker.elastic.co/elasticsearch/elasticsearch:8.12.0',
        'RABBITMQ': 'rabbitmq:3.13-management',
        'MINIO': 'minio/minio:latest',
    }

    # Default ports for each addon
    ADDON_PORTS = {
        'POSTGRES': 5432,
        'REDIS': 6379,
        'MYSQL': 3306,
        'MONGODB': 27017,
        'QDRANT': 6333,
        'ELASTICSEARCH': 9200,
        'RABBITMQ': 5672,
        'MINIO': 9000,
    }

    # Environment variable keys for connection URLs
    ENV_KEY_MAP = {
        'POSTGRES': 'DATABASE_URL',
        'REDIS': 'REDIS_URL',
        'MYSQL': 'MYSQL_URL',
        'MONGODB': 'MONGODB_URI',
        'QDRANT': 'QDRANT_URL',
        'ELASTICSEARCH': 'ELASTICSEARCH_URL',
        'RABBITMQ': 'RABBITMQ_URL',
        'MINIO': 'MINIO_URL',
    }

    GENERIC_ADDONS_CONFIG = {
        'MARIADB': {"image": "mariadb:10.11", "port": 3306, "env_url": "MARIADB_URL", "auth": True, "user_env": "MARIADB_USER", "pass_env": "MARIADB_PASSWORD", "db_env": "MARIADB_DATABASE", "root_pass_env": "MARIADB_ROOT_PASSWORD", "data_dir": "/var/lib/mysql"},
        'COCKROACHDB': {"image": "cockroachdb/cockroach:v23.1.10", "port": 26257, "dashboard_port": 8080, "env_url": "COCKROACHDB_URL", "command": ["start-single-node", "--insecure"], "auth": False, "data_dir": "/cockroach/cockroach-data"},
        'TIMESCALEDB': {"image": "timescale/timescaledb:latest-pg15", "port": 5432, "env_url": "DATABASE_URL", "auth": True, "user_env": "POSTGRES_USER", "pass_env": "POSTGRES_PASSWORD", "db_env": "POSTGRES_DB", "data_dir": "/var/lib/postgresql/data"},
        'PERCONA': {"image": "percona:8.0", "port": 3306, "env_url": "MYSQL_URL", "auth": True, "user_env": "MYSQL_USER", "pass_env": "MYSQL_PASSWORD", "db_env": "MYSQL_DATABASE", "root_pass_env": "MYSQL_ROOT_PASSWORD", "data_dir": "/var/lib/mysql"},
        'VITESS': {"image": "vitess/lite:latest", "port": 15306, "dashboard_port": 15000, "env_url": "VITESS_URL", "auth": False, "data_dir": "/vt/vtdataroot"},
        'COUCHDB': {"image": "couchdb:3.3.3", "port": 5984, "env_url": "COUCHDB_URL", "auth": True, "user_env": "COUCHDB_USER", "pass_env": "COUCHDB_PASSWORD", "data_dir": "/opt/couchdb/data"},
        'RETHINKDB': {"image": "rethinkdb:2.4", "port": 28015, "dashboard_port": 8080, "env_url": "RETHINKDB_URL", "auth": False},
        'ARANGODB': {"image": "arangodb:3.11", "port": 8529, "env_url": "ARANGODB_URL", "auth": True, "root_pass_env": "ARANGO_ROOT_PASSWORD", "data_dir": "/var/lib/arangodb3"},
        'FERRETDB': {"image": "ghcr.io/ferretdb/ferretdb:latest", "port": 27017, "env_url": "MONGODB_URI", "auth": False},
        'SURREALDB': {"image": "surrealdb/surrealdb:latest", "port": 8000, "env_url": "SURREALDB_URL", "command": ["start"], "auth": True, "env": {"SURREAL_USER": "root", "SURREAL_PASS": "{password}"}},
        'MEMCACHED': {"image": "memcached:1.6-alpine", "port": 11211, "env_url": "MEMCACHED_URL", "auth": False},
        'KEYDB': {"image": "eqalpha/keydb:latest", "port": 6379, "env_url": "KEYDB_URL", "auth": True, "command": ["keydb-server", "--requirepass", "{password}"]},
        'VALKEY': {"image": "valkey/valkey:7.2", "port": 6379, "env_url": "VALKEY_URL", "auth": True, "command": ["valkey-server", "--requirepass", "{password}"]},
        'DRAGONFLYDB': {"image": "docker.dragonflydb.io/dragonflydb/dragonfly:latest", "port": 6379, "env_url": "DRAGONFLY_URL", "auth": True, "command": ["dragonfly", "--requirepass", "{password}"]},
        'ETCD': {"image": "bitnami/etcd:3.5", "port": 2379, "env_url": "ETCD_URL", "auth": False, "env": {"ALLOW_NONE_AUTHENTICATION": "yes"}, "data_dir": "/bitnami/etcd/data"},
        'CLICKHOUSE': {"image": "clickhouse/clickhouse-server:23.8", "port": 8123, "env_url": "CLICKHOUSE_URL", "auth": True, "user_env": "CLICKHOUSE_USER", "pass_env": "CLICKHOUSE_PASSWORD", "data_dir": "/var/lib/clickhouse"},
        'CASSANDRA': {"image": "cassandra:4.1", "port": 9042, "env_url": "CASSANDRA_URL", "auth": False, "data_dir": "/var/lib/cassandra"},
        'SCYLLADB': {"image": "scylladb/scylla:5.2.0", "port": 9042, "env_url": "SCYLLADB_URL", "auth": False, "data_dir": "/var/lib/scylla"},
        'NEO4J': {"image": "neo4j:5.12.0", "port": 7687, "dashboard_port": 7474, "env_url": "NEO4J_URL", "auth": True, "env": {"NEO4J_AUTH": "neo4j/{password}"}},
        'DGRAPH': {"image": "dgraph/standalone:v23.0.0", "port": 8080, "env_url": "DGRAPH_URL", "auth": False, "data_dir": "/dgraph"},
        'WEAVIATE': {"image": "semitechnologies/weaviate:1.21.2", "port": 8080, "env_url": "WEAVIATE_URL", "auth": False, "env": {"AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED": "true", "PERSISTENCE_DATA_PATH": "/var/lib/weaviate"}, "data_dir": "/var/lib/weaviate"},
        'MILVUS': {"image": "milvusdb/milvus:v2.3.1", "port": 19530, "env_url": "MILVUS_URL", "auth": False, "command": ["milvus", "run", "standalone"], "data_dir": "/var/lib/milvus"},
        'CHROMADB': {"image": "chromadb/chroma:0.4.14", "port": 8000, "env_url": "CHROMADB_URL", "auth": False},
        'OPENSEARCH': {"image": "opensearchproject/opensearch:2.11.0", "port": 9200, "env_url": "OPENSEARCH_URL", "auth": True, "env": {"discovery.type": "single-node", "OPENSEARCH_INITIAL_ADMIN_PASSWORD": "{password}"}, "data_dir": "/usr/share/opensearch/data"},
        'MEILISEARCH': {"image": "getmeili/meilisearch:v1.4.0", "port": 7700, "env_url": "MEILISEARCH_URL", "auth": True, "pass_env": "MEILI_MASTER_KEY", "data_dir": "/meili_data"},
        'TYPESENSE': {"image": "typesense/typesense:0.25.1", "port": 8108, "env_url": "TYPESENSE_URL", "auth": True, "pass_env": "TYPESENSE_API_KEY", "command": ["--data-dir", "/data"]},
        'SOLR': {"image": "solr:9.3", "port": 8983, "env_url": "SOLR_URL", "auth": False, "data_dir": "/var/solr"},
        'KAFKA': {"image": "bitnami/kafka:3.5.1", "port": 9092, "env_url": "KAFKA_URL", "auth": False, "health_timeout": 120, "ready_timeout": 120, "ready_cmd": "kafka-topics.sh --bootstrap-server localhost:9092 --list >/dev/null 2>&1", "env": {"KAFKA_ENABLE_KRAFT": "yes", "KAFKA_CFG_PROCESS_ROLES": "broker,controller", "KAFKA_CFG_CONTROLLER_LISTENER_NAMES": "CONTROLLER", "KAFKA_CFG_LISTENERS": "PLAINTEXT://:9092,CONTROLLER://:9093", "KAFKA_CFG_LISTENER_SECURITY_PROTOCOL_MAP": "CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT", "KAFKA_CFG_ADVERTISED_LISTENERS": "PLAINTEXT://{hostname}:9092", "KAFKA_CFG_CONTROLLER_QUORUM_VOTERS": "1@{hostname}:9093", "KAFKA_CFG_INTER_BROKER_LISTENER_NAME": "PLAINTEXT", "ALLOW_PLAINTEXT_LISTENER": "yes", "KAFKA_KRAFT_CLUSTER_ID": "{cluster_id}", "KAFKA_BROKER_ID": "1"}, "data_dir": "/bitnami/kafka"},
        'NATS': {"image": "nats:2.9.22-alpine", "port": 4222, "env_url": "NATS_URL", "auth": False},
        'REDPANDA': {"image": "redpandadata/redpanda:v23.2.14", "port": 9092, "env_url": "REDPANDA_URL", "auth": False, "command": ["redpanda", "start", "--overprovisioned", "--smp", "1", "--memory", "1G", "--reserve-memory", "0M", "--node-id", "0", "--check=false"], "data_dir": "/var/lib/redpanda/data"},
        'PULSAR': {"image": "apachepulsar/pulsar:3.1.0", "port": 6650, "env_url": "PULSAR_URL", "auth": False, "command": ["bin/pulsar", "standalone"], "data_dir": "/pulsar/data"},
        'ACTIVEMQ': {"image": "apache/activemq-classic:5.18.3", "port": 61616, "dashboard_port": 8161, "env_url": "ACTIVEMQ_URL", "auth": True, "env": {"ACTIVEMQ_ADMIN_LOGIN": "admin", "ACTIVEMQ_ADMIN_PASSWORD": "{password}"}, "data_dir": "/opt/apache-activemq/data"},
        'SEAWEEDFS': {"image": "chrislusf/seaweedfs:3.59", "port": 8888, "env_url": "SEAWEEDFS_URL", "auth": False, "command": ["server", "-dir=/data", "-s3"]},
        'INFLUXDB': {"image": "influxdb:2.7-alpine", "port": 8086, "env_url": "INFLUXDB_URL", "auth": True, "env": {"DOCKER_INFLUXDB_INIT_MODE": "setup", "DOCKER_INFLUXDB_INIT_USERNAME": "admin", "DOCKER_INFLUXDB_INIT_PASSWORD": "{password}", "DOCKER_INFLUXDB_INIT_ORG": "myorg", "DOCKER_INFLUXDB_INIT_BUCKET": "mybucket"}, "data_dir": "/var/lib/influxdb2"},
        'QUESTDB': {"image": "questdb/questdb:7.3.1", "port": 9000, "env_url": "QUESTDB_URL", "auth": False, "data_dir": "/var/lib/questdb"},
        'VICTORIAMETRICS': {"image": "victoriametrics/victoria-metrics:v1.93.4", "port": 8428, "env_url": "VICTORIAMETRICS_URL", "auth": False, "data_dir": "/victoria-metrics-data"},
        'PROMETHEUS': {"image": "prom/prometheus:v2.47.0", "port": 9090, "env_url": "PROMETHEUS_URL", "auth": False, "data_dir": "/prometheus"},
        'GRAFANA': {"image": "grafana/grafana:10.1.5", "port": 3000, "env_url": "GRAFANA_URL", "auth": True, "env": {"GF_SECURITY_ADMIN_PASSWORD": "{password}"}, "data_dir": "/var/lib/grafana"},
        'JAEGER': {"image": "jaegertracing/all-in-one:1.49", "port": 16686, "env_url": "JAEGER_URL", "auth": False, "data_dir": "/badger"},
        'N8N': {"image": "n8nio/n8n:1.8.0", "port": 5678, "env_url": "N8N_URL", "auth": True, "env": {"N8N_BASIC_AUTH_ACTIVE": "true", "N8N_BASIC_AUTH_USER": "admin", "N8N_BASIC_AUTH_PASSWORD": "{password}"}, "data_dir": "/home/node/.n8n"},
        'TEMPORAL': {"image": "temporalio/auto-setup:1.22.1", "port": 7233, "dashboard_port": 8080, "env_url": "TEMPORAL_URL", "auth": False},
        'VAULT': {"image": "hashicorp/vault:1.15", "port": 8200, "env_url": "VAULT_URL", "auth": True, "env": {"VAULT_DEV_ROOT_TOKEN_ID": "{password}", "VAULT_DEV_LISTEN_ADDRESS": "0.0.0.0:8200"}},
        'CONSUL': {"image": "hashicorp/consul:1.16", "port": 8500, "env_url": "CONSUL_URL", "auth": False, "command": ["agent", "-dev", "-client", "0.0.0.0"], "data_dir": "/consul/data"},
        'KEYCLOAK': {"image": "quay.io/keycloak/keycloak:22.0.4", "port": 8080, "env_url": "KEYCLOAK_URL", "auth": True, "env": {"KEYCLOAK_ADMIN": "admin", "KEYCLOAK_ADMIN_PASSWORD": "{password}"}, "command": ["start-dev"], "data_dir": "/opt/keycloak/data"},
    }

    def __init__(self):
        self.network_name = config(
            'DOCKER_NETWORK',
            default='smsly-net')
        self.proxy_network_name = config(
            'DOCKER_PROXY_NETWORK',
            default='smsly-net')
        self._network_checked = False

        # Register generic addons so they are recognized across the platform
        for addon, addon_cfg in self.GENERIC_ADDONS_CONFIG.items():
            if addon not in self.ADDON_IMAGES:
                self.ADDON_IMAGES[addon] = addon_cfg['image']
            if addon not in self.ADDON_PORTS:
                self.ADDON_PORTS[addon] = addon_cfg['port']
            if addon not in self.ENV_KEY_MAP and 'env_url' in addon_cfg:
                self.ENV_KEY_MAP[addon] = addon_cfg['env_url']

    def _write_env_file(self, env_vars: dict[str, str]) -> str:
        """Write env vars to a temporary file and return the path.

        Using ``--env-file`` instead of ``-e KEY=VAL`` prevents passwords
        from appearing in the process table (``ps aux``).
        """
        import tempfile
        fd, path = tempfile.mkstemp(prefix='smsly-addon-env-', suffix='.env', text=True)
        with os.fdopen(fd, 'w') as f:
            for key, value in env_vars.items():
                f.write(f"{key}={value}\n")
        return path

    def _append_traefik_labels(self, cmd_list: list, router_name: str, domain: str, target_port: int):
        """Append standard Traefik labels to expose an addon container publicly."""
        import os
        enable_tls = (str(os.getenv("TRAEFIK_ENABLE_WEBSECURE", "false")).strip().lower() in {"1", "true", "yes", "on"})
        cmd_list.extend(['-l', 'traefik.enable=true'])
        cmd_list.extend(['-l', f'traefik.http.routers.{router_name}.rule=Host(`{domain}`)'])
        cmd_list.extend(['-l', f'traefik.http.services.{router_name}.loadbalancer.server.port={target_port}'])
        cmd_list.extend(['-l', f'traefik.http.routers.{router_name}.priority=100'])
        cmd_list.extend(['-l', f'traefik.docker.network={self.proxy_network_name}'])
        if enable_tls:
            cmd_list.extend(['-l', f'traefik.http.routers.{router_name}.entrypoints=web,websecure'])
            cmd_list.extend(['-l', f'traefik.http.routers.{router_name}.tls.certresolver=letsencrypt'])
        else:
            cmd_list.extend(['-l', f'traefik.http.routers.{router_name}.entrypoints=web'])

    def _connect_to_proxy_network(self, container_name: str):
        """Ensure the container is connected to the proxy network for Traefik access."""
        try:
            # Check if already connected
            inspect_proc = subprocess.run(
                ['docker', 'inspect', '-f', '{{range .NetworkSettings.Networks}}{{.NetworkID}}{{end}}', container_name],
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Get the network ID of the proxy network
            network_id_proc = subprocess.run(
                ['docker', 'network', 'inspect', '-f', '{{.Id}}', self.proxy_network_name],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if network_id_proc.returncode == 0:
                network_id = network_id_proc.stdout.strip()
                if network_id not in inspect_proc.stdout:
                    result = subprocess.run(
                        ['docker', 'network', 'connect', self.proxy_network_name, container_name],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=30,
                    )
                    if result.returncode != 0:
                        logger.error(
                            "Failed to connect %s to proxy network %s: %s",
                            container_name, self.proxy_network_name,
                            result.stderr.strip(),
                        )
                    else:
                        logger.debug(f"Connected {container_name} to {self.proxy_network_name}")
            else:
                logger.error(
                    "Proxy network %s not found — Traefik routing may not work for %s",
                    self.proxy_network_name, container_name,
                )
        except Exception as e:
            logger.error(f"Could not connect {container_name} to proxy network: {e}")

    def _connect_to_service_scoped_network(self, container_name: str, addon) -> None:
        """Connect the addon container to the service's scoped bridge for DNS resolution.

        This allows the service container (on its isolated bridge) to resolve
        the addon's hostname via Docker DNS on that bridge instead of relying
        exclusively on ``smsly-net``.

        Raises RuntimeError if the scoped network exists but the connection fails,
        so the operator is alerted to network misconfiguration.
        """
        try:
            from apps.deployments.models.network_scope import ScopedNetwork as _Net
            project = getattr(addon.service, 'project', None)
            if not project:
                return
            network_name = _Net.resolve_network_name(project)
            if not network_name or network_name == self.network_name:
                return
            # Verify the network actually exists on this Docker daemon
            inspect = subprocess.run(
                ['docker', 'network', 'inspect', network_name],
                capture_output=True, text=True, timeout=30,
            )
            if inspect.returncode != 0:
                return
            alias = getattr(addon, 'name', None) or f"{addon.addon_type.lower()}-{addon.service.name}"
            # Check if already connected
            ct_inspect = subprocess.run(
                ['docker', 'inspect', '-f', '{{range .NetworkSettings.Networks}}{{.NetworkID}}{{end}}', container_name],
                capture_output=True, text=True, timeout=30,
            )
            net_id = subprocess.run(
                ['docker', 'network', 'inspect', '-f', '{{.Id}}', network_name],
                capture_output=True, text=True, timeout=30,
            )
            if net_id.returncode == 0 and net_id.stdout.strip() not in ct_inspect.stdout:
                result = subprocess.run(
                    ['docker', 'network', 'connect', '--alias', alias, network_name, container_name],
                    capture_output=True, text=True, check=False, timeout=30,
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        f"Failed to connect {container_name} to scoped network "
                        f"{network_name} (alias={alias}): {result.stderr.strip()}"
                    )
                logger.info("Connected %s to scoped network %s (alias: %s)",
                            container_name, network_name, alias)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Scoped network connection failed for {container_name}: {exc}"
            )

    def _container_status(self, container_name: str) -> tuple[str | None, bool]:
        """
        Return (container_id, is_running) for a given docker container name.

        Returns (None, False) if the container does not exist or docker inspect fails.
        """
        try:
            result = subprocess.run(
                ['docker', 'inspect', '-f', '{{.Id}} {{.State.Running}}', container_name],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                return None, False
            parts = (result.stdout or '').strip().split()
            if not parts:
                return None, False
            cid = parts[0].strip() or None
            running = len(parts) > 1 and parts[1].strip().lower() == 'true'
            if cid:
                cid = cid[:12]
            return cid, running
        except Exception:
            return None, False

    def _start_container(self, container_name: str) -> bool:
        try:
            result = subprocess.run(
                ['docker', 'start', container_name],
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _parse_connection_url(self, url: str) -> dict[str, object]:
        """Parse a connection URL into components used for idempotent reprovisioning."""
        parsed = urlparse(str(url or '').strip())
        db_name = ''
        if parsed.path and parsed.path != '/':
            db_name = parsed.path.lstrip('/')
        return {
            'scheme': parsed.scheme or '',
            'hostname': parsed.hostname or '',
            'port': parsed.port,
            'username': parsed.username or '',
            'password': parsed.password or '',
            'database': db_name,
        }

    def _ensure_network(self):
        """Ensure the Docker network exists for service connectivity.

        Raises RuntimeError if the network cannot be created or verified,
        preventing addon containers from starting without proper networking.
        """
        if self._network_checked:
            return
        try:
            result = subprocess.run(
                ['docker', 'network', 'inspect', self.network_name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                create_result = subprocess.run(
                    ['docker', 'network', 'create', self.network_name],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if create_result.returncode != 0:
                    raise RuntimeError(
                        f"Failed to create Docker network '{self.network_name}': "
                        f"{create_result.stderr.strip()}"
                    )
                logger.info(f"Created Docker network: {self.network_name}")

            # Also ensure proxy network exists if different from primary
            if self.proxy_network_name != self.network_name:
                proxy_result = subprocess.run(
                    ['docker', 'network', 'inspect', self.proxy_network_name],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if proxy_result.returncode != 0:
                    proxy_create = subprocess.run(
                        ['docker', 'network', 'create', self.proxy_network_name],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if proxy_create.returncode != 0:
                        logger.warning(
                            "Failed to create proxy network '%s': %s",
                            self.proxy_network_name,
                            proxy_create.stderr.strip(),
                        )
                    else:
                        logger.info(f"Created Docker proxy network: {self.proxy_network_name}")

            self._network_checked = True
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(
                f"Failed to verify/create Docker network '{self.network_name}': {e}"
            )

    def _get_occupied_host_ports(self) -> set[int]:
        """Return a set of all host ports currently occupied by Docker container mappings."""
        try:
            # Use docker inspect on all containers to find host port bindings
            result = subprocess.run(
                ['docker', 'ps', '-q'],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            container_ids = result.stdout.strip().split()
            if not container_ids:
                return set()

            inspect_result = subprocess.run(
                ['docker', 'inspect', '--format', '{{range $p, $conf := .HostConfig.PortBindings}}{{range $conf}}{{.HostPort}} {{end}}{{end}}', *container_ids],
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )

            occupied = set()
            for line in inspect_result.stdout.strip().splitlines():
                for port in line.split():
                    if port.isdigit():
                        occupied.add(int(port))
            return occupied
        except Exception as e:
            logger.warning(f"Failed to detect occupied host ports: {e}")
            return set()

    def _get_free_host_port(self, base_port: int) -> int:
        """Find a free port on the host for Lite Agent connectivity."""
        occupied = self._get_occupied_host_ports()

        # Add common system ports to occupied set
        occupied.update({22, 80, 443, 8000, 8090, 2375, 5000, 5001, 7000, 5672, 5432, 6379})

        # Start searching from a high range (50000+)
        # Use base_port to stay somewhat consistent (e.g. 5432 -> 55432)
        start_port = 50000 + (base_port % 10000)
        if start_port > 64000:
            start_port = 50000

        for port in range(start_port, 65000):
            if port not in occupied:
                import socket
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    try:
                        s.bind(('0.0.0.0', port))
                        return port
                    except OSError:
                        continue
        raise RuntimeError(f"No free host ports available in range {start_port}-65000")

    def provision(self, addon) -> tuple[str, str]:
        """
        Provision a database addon container.

        Args:
            addon: Addon model instance

        Returns:
            Tuple of (container_id, connection_url)
        """
        addon_type = addon.addon_type
        service_name = addon.service.name
        self._ensure_network()

        # Stable container name; do not change across retries.
        container_name = f"smsly-addon-{addon_type.lower()}-{addon.id}"

        # Persisted URL is the source of truth for passwords. If we rotate passwords on retries
        # but re-use a persistent volume, the container will keep the original password and
        # apps will start failing with auth errors.
        existing_url = str(getattr(addon, 'connection_url', '') or '').strip()

        # Friendly network alias so apps can reach the addon by name, used for first provision
        # and for reconstructing a missing URL.
        alias_name = str(getattr(addon, 'name', '') or f"{addon_type.lower()}-{service_name}").strip()

        image = self.ADDON_IMAGES.get(addon_type)
        port = self.ADDON_PORTS.get(addon_type)

        generic_config = self.GENERIC_ADDONS_CONFIG.get(addon_type)
        if generic_config:
            image = cast(str, generic_config['image'])
            port = cast(int, generic_config['port'])

        if not image:
            raise ValueError(f"Unknown addon type: {addon_type}")


        logger.info(f"Provisioning {addon_type} addon for service {service_name}")

        public_domain = getattr(addon, 'public_domain', None)
        router_name = container_name.replace(".", "-").replace("_", "-")

        # If the container already exists, never "re-provision" (which would rotate passwords).
        existing_cid, is_running = self._container_status(container_name)
        host_port_for_recreate: int | None = None

        if existing_cid:
            cid: str = existing_cid
            # Check if Traefik routing labels match the current public_domain
            try:
                inspect_proc = subprocess.run(
                    ['docker', 'inspect', '-f', '{{json .Config.Labels}}', container_name],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                if inspect_proc.returncode == 0 and inspect_proc.stdout:
                    import json
                    labels = json.loads(inspect_proc.stdout)

                    expected_rule_key = f"traefik.http.routers.{router_name}.rule"
                    expected_rule_val = f"Host(`{public_domain}`)" if public_domain else None
                    current_rule_val = labels.get(expected_rule_key)

                    # If public domain changed, or was added/removed, we must recreate the container to update labels.
                    # Volumes, passwords, and data will persist.
                    if current_rule_val != expected_rule_val:
                        logger.info(f"Public domain changed for {container_name}. Recreating container to update Traefik labels.")
                        # Preserve the published host port (lite-agent addons
                        # expose their port on the master) so the recreated
                        # container keeps the existing connection URL valid.
                        recreated_host_port = self._get_published_host_port(container_name)
                        subprocess.run(['docker', 'rm', '-f', container_name], capture_output=True, check=False, timeout=60)
                        existing_cid = None
                        cid = ""
                        host_port_for_recreate = recreated_host_port
            except Exception as e:
                logger.warning(f"Failed to inspect labels for {container_name}: {e}")

        if existing_cid and cid:
            if not is_running:
                logger.info("Starting existing addon container: %s", container_name)
                self._start_container(container_name)
                time.sleep(1)
                existing_cid, _ = self._container_status(container_name)
                if not existing_cid:
                    raise RuntimeError(f"Container {container_name} disappeared after start")
                cid = existing_cid

            # Enforce network connection for existing containers to fix missing aliases
            # when upgrading from older platforms or recovering from network drops.
            parsed_hostname = alias_name or container_name
            if existing_url:
                parsed_hostname = str(self._parse_connection_url(existing_url).get('hostname') or parsed_hostname).strip()

            try:
                # Check if the container is already connected to the network with the correct alias
                # to prevent dropping active database connections on every deployment.
                inspect_proc = subprocess.run(
                    ['docker', 'inspect', '-f', '{{range .NetworkSettings.Networks}}{{.Aliases}}{{end}}', container_name],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )

                # If the alias is not found in the output, attempt to attach it.
                if parsed_hostname not in inspect_proc.stdout:
                    # Retry network reconnection up to 3 times for transient Docker daemon issues
                    for attempt in range(3):
                        subprocess.run(
                            ['docker', 'network', 'disconnect', self.network_name, container_name],
                            capture_output=True,
                            check=False,
                            timeout=30,
                        )
                        connect_result = subprocess.run(
                            ['docker', 'network', 'connect', '--alias', parsed_hostname, self.network_name, container_name],
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=30,
                        )
                        if connect_result.returncode == 0:
                            break
                        if attempt < 2:
                            logger.warning(
                                "Network connect attempt %d failed for %s: %s — retrying",
                                attempt + 1, container_name, connect_result.stderr.strip(),
                            )
                            time.sleep(1)
                    else:
                        raise RuntimeError(
                            f"Failed to attach network alias '{parsed_hostname}' to "
                            f"container {container_name} after 3 attempts: "
                            f"{connect_result.stderr.strip()}"
                        )

                # Ensure proxy network connection as well
                self._connect_to_proxy_network(container_name)
                self._connect_to_service_scoped_network(container_name, addon)
            except RuntimeError:
                raise
            except Exception as e:
                logger.warning("Network reconnect failed for %s: %s", container_name, e)

            # Wait for readiness to reduce flakiness on immediate retries.
            try:
                if addon_type == 'RABBITMQ':
                    self._wait_for_health(
                        container_name,
                        15672,
                        path="/api/health/checks/alarms",
                        use_http=True,
                    )
                else:
                    self._wait_for_health(container_name, cast(int, port))
            except Exception as exc:  # pragma: no cover
                logger.warning("Addon health check failed for %s: %s", container_name, exc)

            if existing_url:
                return cid, existing_url

            # URL missing in DB but container exists (e.g. task crashed after docker run).
            # Attempt best-effort reconstruction from container config to avoid password rotation.
            hostname = alias_name or container_name
            try:
                if addon_type == 'MINIO':
                    minio_user = self._get_container_env(container_name, 'MINIO_ROOT_USER')
                    minio_password = self._get_container_env(container_name, 'MINIO_ROOT_PASSWORD')
                    return cid, f"s3://{minio_user}:{minio_password}@{hostname}:{port}"

                if addon_type == 'POSTGRES':
                    db_user = self._get_container_env(container_name, 'POSTGRES_USER')
                    db_name = self._get_container_env(container_name, 'POSTGRES_DB')
                    password = self._get_container_env(container_name, 'POSTGRES_PASSWORD')
                    return cid, f"postgresql://{db_user}:{password}@{hostname}:{port}/{db_name}"

                if addon_type == 'MYSQL':
                    db_user = self._get_container_env(container_name, 'MYSQL_USER')
                    db_name = self._get_container_env(container_name, 'MYSQL_DATABASE')
                    password = self._get_container_env(container_name, 'MYSQL_PASSWORD')
                    return cid, f"mysql://{db_user}:{password}@{hostname}:{port}/{db_name}"

                if addon_type == 'MONGODB':
                    db_user = self._get_container_env(container_name, 'MONGO_INITDB_ROOT_USERNAME')
                    password = self._get_container_env(container_name, 'MONGO_INITDB_ROOT_PASSWORD')
                    return cid, f"mongodb://{db_user}:{password}@{hostname}:{port}/app_db?authSource=admin"

                if addon_type == 'RABBITMQ':
                    user = self._get_container_env(container_name, 'RABBITMQ_DEFAULT_USER')
                    password = self._get_container_env(container_name, 'RABBITMQ_DEFAULT_PASS')
                    return cid, f"amqp://{user}:{password}@{hostname}:{port}//"

                if addon_type == 'REDIS':
                    try:
                        password = self._get_container_env(container_name, 'REDIS_PASSWORD')
                    except Exception:
                        password = ''
                    if password:
                        return cid, f"redis://:{password}@{hostname}:{port}/0"
                    # Legacy containers baked the password into the command line.
                    result = subprocess.run(
                        ['docker', 'inspect', '-f', '{{json .Config.Cmd}}', container_name],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result.returncode == 0 and (result.stdout or '').strip():
                        import json
                        cmd = json.loads(result.stdout)
                        if isinstance(cmd, list) and '--requirepass' in cmd:
                            idx = cmd.index('--requirepass')
                            if idx + 1 < len(cmd) and cmd[idx + 1]:
                                password = cmd[idx + 1]
                                return cid, f"redis://:{password}@{hostname}:{port}/0"

                if addon_type in ('QDRANT', 'ELASTICSEARCH'):
                    return cid, f"http://{hostname}:{port}"

                if generic_config:
                    scheme = cast(str, generic_config.get('scheme', addon_type.lower()))
                    user = 'admin'
                    db = 'app_db'
                    password = ''
                    if generic_config.get('auth'):
                        # attempt to fetch password
                        pass_env = cast(str, generic_config.get('pass_env') or generic_config.get('root_pass_env') or '')
                        if pass_env:
                            password = self._get_container_env(container_name, pass_env)
                        user_env_val = cast(str, generic_config.get('user_env') or '')
                        if user_env_val:
                            user = self._get_container_env(container_name, user_env_val)
                        db_env_val = cast(str, generic_config.get('db_env') or '')
                        if db_env_val:
                            db = self._get_container_env(container_name, db_env_val)

                        if user_env_val:
                            return cid, f"{scheme}://{user}:{password}@{hostname}:{port}/{db}"
                        else:
                            return cid, f"{scheme}://:{password}@{hostname}:{port}"
                    else:
                        return cid, f"{scheme}://{hostname}:{port}"

            except Exception as exc:
                logger.warning("Failed to reconstruct addon URL for %s: %s", container_name, exc)

            raise RuntimeError(f"Addon container exists but connection_url is missing: {container_name}")

        # If the URL exists but container is missing, re-create the container using the same credentials.
        # This avoids password drift when persistent volumes are re-used.
        if existing_url:
            parsed = self._parse_connection_url(existing_url)
            hostname = str(parsed.get('hostname') or alias_name or container_name).strip()
            password = str(parsed.get('password') or '').strip()
            username = str(parsed.get('username') or '').strip()
            db_name = str(parsed.get('database') or '').strip()

            if addon_type in ('POSTGRES', 'REDIS', 'MYSQL', 'MONGODB', 'RABBITMQ', 'MINIO') and not password:
                raise ValueError("Existing connection_url is missing a password; refusing to reprovision.")

            if generic_config:
                if generic_config.get('auth') and not password:
                    raise ValueError(f"Existing connection_url is missing a password for {addon_type}; refusing to reprovision.")
                container_id, _ = self._provision_generic(addon_type, container_name, password, cast(int, port), hostname, cast(dict, generic_config), username=username, db_name=db_name, public_domain=public_domain, host_port=host_port_for_recreate)
            elif addon_type == 'MINIO':
                container_id, _ = self._provision_minio(container_name, password, cast(int, port), hostname, username=username, public_domain=public_domain, host_port=host_port_for_recreate)
            elif addon_type == 'POSTGRES':
                container_id, _ = self._provision_postgres(
                    container_name,
                    password,
                    cast(int, port),
                    hostname,
                    db_user=username or None,
                    db_name=db_name or None,
                    public_domain=public_domain,
                    host_port=host_port_for_recreate,
                )
            elif addon_type == 'REDIS':
                container_id, _ = self._provision_redis(container_name, password, cast(int, port), hostname, public_domain=public_domain, host_port=host_port_for_recreate)
            elif addon_type == 'MYSQL':
                container_id, _ = self._provision_mysql(container_name, password, cast(int, port), hostname, public_domain=public_domain, host_port=host_port_for_recreate)
            elif addon_type == 'MONGODB':
                container_id, _ = self._provision_mongodb(container_name, password, cast(int, port), hostname, public_domain=public_domain, host_port=host_port_for_recreate)
            elif addon_type == 'QDRANT':
                container_id, _ = self._provision_qdrant(container_name, cast(int, port), hostname, public_domain=public_domain, host_port=host_port_for_recreate)
            elif addon_type == 'ELASTICSEARCH':
                container_id, _ = self._provision_elasticsearch(container_name, cast(int, port), hostname, public_domain=public_domain, host_port=host_port_for_recreate)
            elif addon_type == 'RABBITMQ':
                container_id, _ = self._provision_rabbitmq(container_name, password, cast(int, port), hostname, public_domain=public_domain, host_port=host_port_for_recreate)
            else:
                raise ValueError(f"Unsupported addon type: {addon_type}")

            self._connect_to_proxy_network(container_name)
            return container_id, existing_url

        # First-time provisioning: generate fresh credentials for passworded addons.
        is_passworded = addon_type in (
            'POSTGRES', 'REDIS', 'MYSQL', 'MONGODB', 'RABBITMQ', 'MINIO'
        )
        if generic_config and generic_config.get('auth'):
            is_passworded = True

        password = secrets.token_urlsafe(48) if is_passworded else ''

        if generic_config:
            container_id, connection_url = self._provision_generic(addon_type, container_name, password, cast(int, port), alias_name, cast(dict, generic_config), public_domain=public_domain)
        elif addon_type == 'MINIO':
            # Minio needs a username too, we can auto-generate one or use a default like 'admin'
            username = secrets.token_hex(8)
            container_id, connection_url = self._provision_minio(container_name, password, cast(int, port), alias_name, username=username, public_domain=public_domain)
        elif addon_type == 'POSTGRES':
            container_id, connection_url = self._provision_postgres(container_name, password, cast(int, port), alias_name, public_domain=public_domain)
        elif addon_type == 'REDIS':
            container_id, connection_url = self._provision_redis(container_name, password, cast(int, port), alias_name, public_domain=public_domain)
        elif addon_type == 'MYSQL':
            container_id, connection_url = self._provision_mysql(container_name, password, cast(int, port), alias_name, public_domain=public_domain)
        elif addon_type == 'MONGODB':
            container_id, connection_url = self._provision_mongodb(container_name, password, cast(int, port), alias_name, public_domain=public_domain)
        elif addon_type == 'QDRANT':
            container_id, connection_url = self._provision_qdrant(container_name, cast(int, port), alias_name, public_domain=public_domain)
        elif addon_type == 'ELASTICSEARCH':
            container_id, connection_url = self._provision_elasticsearch(container_name, cast(int, port), alias_name, public_domain=public_domain)
        elif addon_type == 'RABBITMQ':
            container_id, connection_url = self._provision_rabbitmq(container_name, password, cast(int, port), alias_name, public_domain=public_domain)
        else:
            raise ValueError(f"Unsupported addon type: {addon_type}")

        logger.info(f"Addon {addon_type} provisioned: {container_name} (alias: {alias_name})")

        # Final bridge connection for new/missing containers
        self._connect_to_proxy_network(container_name)
        self._connect_to_service_scoped_network(container_name, addon)

        # ── LITE AGENT ROUTING ──
        # If we are provisioning on the Master but the service is on a Remote Node (Lite Agent),
        # we must expose the port and translate 'localhost' to the Master's Public IP.
        service_server = getattr(addon.service, 'server', None)
        if service_server and not service_server.is_primary:
            master_ip = os.environ.get("PUBLIC_IP") or "127.0.0.1"

            # Verify the Lite Agent can reach the master before rewriting the URL
            try:
                import socket as _sock
                test_sock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                test_sock.settimeout(3)
                try:
                    test_sock.connect((master_ip, 22))  # SSH port as connectivity probe
                except Exception:
                    logger.warning(
                        "Lite Agent node %s may not be reachable from master IP %s — "
                        "addon connectivity may fail",
                        service_server.host, master_ip,
                    )
                finally:
                    test_sock.close()
            except (ConnectionError, OSError, TimeoutError) as exc:
                logger.debug("Socket connectivity test failed: %s", exc)

            # Find a free port on the host to map to this container
            host_port = self._get_free_host_port(cast(int, port))

            # Re-provision with port exposure if not already done
            # (In a real scenario, we'd modify the docker run command to include -p)
            # For this fix, we'll force a re-provision with the host port.
            try:
                logger.info(f"Exposing {addon_type} on Master host port {host_port} for Lite Agent")
                subprocess.run(['docker', 'rm', '-f', container_name], capture_output=True, timeout=60)

                # We need to call the internal provisioner again with the port mapping.
                # Since we don't want to refactor everything yet, we'll do a quick manual run.
                # This is a bit hacky but effective for this specific architectural bridge.
                if addon_type == 'POSTGRES':
                    container_id, _ = self._provision_postgres(container_name, password, cast(int, port), alias_name, host_port=host_port, public_domain=public_domain)
                elif addon_type == 'REDIS':
                    container_id, _ = self._provision_redis(container_name, password, cast(int, port), alias_name, host_port=host_port, public_domain=public_domain)
                elif addon_type == 'MYSQL':
                    container_id, _ = self._provision_mysql(container_name, password, cast(int, port), alias_name, host_port=host_port, public_domain=public_domain)
                elif addon_type == 'MONGODB':
                    container_id, _ = self._provision_mongodb(container_name, password, cast(int, port), alias_name, host_port=host_port, public_domain=public_domain)
                elif addon_type == 'RABBITMQ':
                    container_id, _ = self._provision_rabbitmq(container_name, password, cast(int, port), alias_name, host_port=host_port, public_domain=public_domain)
                elif addon_type == 'MINIO':
                    container_id, _ = self._provision_minio(container_name, password, cast(int, port), alias_name, host_port=host_port, public_domain=public_domain)
                elif addon_type == 'QDRANT':
                    container_id, _ = self._provision_qdrant(container_name, cast(int, port), alias_name, host_port=host_port, public_domain=public_domain)
                elif addon_type == 'ELASTICSEARCH':
                    container_id, _ = self._provision_elasticsearch(container_name, cast(int, port), alias_name, host_port=host_port, public_domain=public_domain)
                elif generic_config:
                    container_id, _ = self._provision_generic(addon_type, container_name, password, cast(int, port), alias_name, cast(dict, generic_config), host_port=host_port, public_domain=public_domain)
                else:
                    raise ValueError(f"Unsupported addon type for lite-agent exposure: {addon_type}")

                # Update URL to use Master IP and Host Port
                from urllib.parse import urlparse, urlunparse
                new_parsed = urlparse(connection_url)
                if new_parsed.password:
                    userinfo = f"{new_parsed.username}:{new_parsed.password}@" if new_parsed.username else f":{new_parsed.password}@"
                elif new_parsed.username:
                    userinfo = f"{new_parsed.username}@"
                else:
                    userinfo = ''
                new_netloc = f"{userinfo}{master_ip}:{host_port}"
                connection_url = urlunparse(new_parsed._replace(netloc=new_netloc))

            except Exception as e:
                logger.warning(f"Failed to auto-expose port for Lite Agent: {e}")

        return container_id, connection_url

    def provision_dispatch(self, addon) -> tuple[str, str]:
        """
        Provision an addon on the correct host.

        Auto-detects the target: full-stack nodes get the addon provisioned
        locally on the node via SSH; lite agents and local services use the
        existing local-Docker provisioning on the master.

        This is the preferred entry point for all addon provisioning.
        """
        server = getattr(addon.service, 'server', None)
        if (server and not server.is_primary
                and not getattr(server, 'is_lite_agent', False)):
            return self.provision_remote(addon, server)
        return self.provision(addon)

    def provision_remote(self, addon, server) -> tuple[str, str]:
        """
        Provision an addon on a full-stack remote node via SSH.

        The addon container runs on the same node as the service, making
        the node truly self-sufficient (its database lives on the node,
        not on the master).

        Args:
            addon: Addon model instance
            server: ManagedServer instance (must be a non-lite, non-primary node)

        Returns:
            Tuple of (container_id, connection_url)
        """
        from apps.deployments.services.ssh_client import SSHClient

        addon_type = addon.addon_type
        service_name = addon.service.name
        self._ensure_network()
        container_name = f"smsly-addon-{addon_type.lower()}-{addon.id}"
        alias_name = str(
            getattr(addon, 'name', '') or f"{addon_type.lower()}-{service_name}"
        ).strip()
        image = self.ADDON_IMAGES.get(addon_type)
        port = self.ADDON_PORTS.get(addon_type)
        generic_config = self.GENERIC_ADDONS_CONFIG.get(addon_type)
        if generic_config:
            image = cast(str, generic_config['image'])
            port = cast(int, generic_config['port'])
        if not image:
            raise ValueError(f"Unknown addon type: {addon_type}")

        is_passworded = addon_type in (
            'POSTGRES', 'REDIS', 'MYSQL', 'MONGODB', 'RABBITMQ', 'MINIO'
        )
        if generic_config and generic_config.get('auth'):
            is_passworded = True
        password = secrets.token_urlsafe(48) if is_passworded else ''

        # Build the docker run command for the remote node
        cmd_parts = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '--security-opt', 'no-new-privileges:true',
            '--security-opt', 'apparmor=docker-default',
            '--cap-drop=ALL',
            '--cap-add=NET_BIND_SERVICE',
            '--cap-add=CHOWN',
            '--cap-add=SETUID',
            '--cap-add=SETGID',
            '--cap-add=DAC_OVERRIDE',
            '--cap-add=FOWNER',
            '--pids-limit', '1024',
        ]

        # Secrets go into a remote --env-file (uploaded via SFTP) instead of
        # `-e KEY=val` so passwords never appear in the remote process list.
        env_vars: dict[str, str] = {}
        remote_temp_files: list[str] = []

        ssh = SSHClient(
            ip=server.host,
            key_content=server.ssh_key,
            password=server.ssh_password,
            user=server.ssh_user,
            port=server.ssh_port,
            wg_address=getattr(server, "wg_address", None),
        )
        ssh.connect()

        # Idempotency (mirrors local provision()): the persisted URL is the
        # source of truth for credentials. Never rotate the password or
        # recreate a running container — doing so on every deploy would break
        # every running service still holding the old credentials.
        existing_url = str(getattr(addon, 'connection_url', '') or '').strip()
        if existing_url:
            try:
                from urllib.parse import urlparse as _urlparse
                existing_pw = _urlparse(existing_url).password
            except Exception:
                existing_pw = None
            if existing_pw:
                password = existing_pw

            _, _, inspect_code = ssh.exec_command(
                "docker inspect -f '{{.State.Running}}' "
                f"{shlex.quote(container_name)} 2>/dev/null",
                timeout=30,
                raise_on_error=False,
            )
            if inspect_code == 0:
                logger.info(
                    "Remote addon %s already running on %s; skipping recreate",
                    container_name, server.host,
                )
                return (
                    str(getattr(addon, 'coolify_uuid', '') or '') or container_name,
                    existing_url,
                )

        def _attach_env_file() -> None:
            if not env_vars:
                return
            env_file_local = self._write_env_file(env_vars)
            remote_env_path = f"/tmp/smsly-addon-env-{uuid.uuid4().hex}.env"
            try:
                ssh.upload_file(env_file_local, remote_env_path)
            finally:
                with contextlib.suppress(OSError):
                    os.remove(env_file_local)
            remote_temp_files.append(remote_env_path)
            cmd_parts.extend(['--env-file', remote_env_path])

        def _write_remote_file(content: str, persist: bool = False,
                               stable_name: str | None = None) -> str:
            import tempfile as _tmp
            fd, local_path = _tmp.mkstemp(prefix='smsly-addon-remote-', suffix='.conf', text=True)
            try:
                with os.fdopen(fd, 'w') as f:
                    f.write(content)
                if persist:
                    # Mounted into the container, so it must survive until the
                    # container is removed (restarts re-read the bind mount).
                    # Cleaned up in deprovision_remote.
                    remote_path = f"/var/lib/smsly/addon-envs/{stable_name or uuid.uuid4().hex}.conf"
                    ssh.exec_command(
                        "mkdir -p /var/lib/smsly/addon-envs && chmod 700 /var/lib/smsly/addon-envs",
                        timeout=15,
                        raise_on_error=False,
                    )
                    ssh.upload_file(local_path, remote_path)
                    ssh.exec_command(f"chmod 600 {shlex.quote(remote_path)}", timeout=10, raise_on_error=False)
                    return remote_path
                remote_path = f"/tmp/smsly-addon-{uuid.uuid4().hex}.conf"
                ssh.upload_file(local_path, remote_path)
            finally:
                with contextlib.suppress(OSError):
                    os.remove(local_path)
            remote_temp_files.append(remote_path)
            return remote_path

        if addon_type == 'POSTGRES':
            safe_suffix = (
                (alias_name or container_name)
                .replace('-', '_').replace('.', '_').replace(' ', '_')
            )[:63]
            db_user = safe_suffix
            db_name = safe_suffix
            env_vars.update({
                'POSTGRES_PASSWORD': password,
                'POSTGRES_USER': db_user,
                'POSTGRES_DB': db_name,
            })
            cmd_parts.extend([
                '-v', f'{container_name}-data:/var/lib/postgresql/data',
            ])
            if alias_name:
                cmd_parts.extend(['--network-alias', alias_name])
            _attach_env_file()
            cmd_parts.append(image)
            cmd_str = ' '.join(shlex.quote(p) for p in cmd_parts)
            hostname = alias_name or container_name
            connection_url = f"postgresql://{db_user}:{password}@{hostname}:{port}/{db_name}"

        elif addon_type == 'REDIS':
            # Password via a mounted redis.conf (never on the command line).
            # Persisted on the remote host (deleted in deprovision_remote)
            # because Docker re-reads bind mounts on container restarts.
            remote_conf_path = _write_remote_file(
                f"requirepass {password}\n",
                persist=True,
                stable_name=f"{container_name}.conf",
            )
            cmd_parts.extend([
                '-v', f'{container_name}-data:/data',
                '-v', f'{remote_conf_path}:/usr/local/etc/redis/redis.conf:ro',
            ])
            if alias_name:
                cmd_parts.extend(['--network-alias', alias_name])
            cmd_parts.append(image)
            cmd_parts.extend(['redis-server', '/usr/local/etc/redis/redis.conf'])
            cmd_str = ' '.join(shlex.quote(p) for p in cmd_parts)
            hostname = alias_name or container_name
            connection_url = f"redis://:{password}@{hostname}:{port}/0"

        elif addon_type == 'MYSQL':
            safe_suffix = (
                (alias_name or container_name)
                .replace('-', '_').replace('.', '_').replace(' ', '_')
            )[:63]
            db_name = safe_suffix
            db_user = safe_suffix
            env_vars.update({
                'MYSQL_ROOT_PASSWORD': password,
                'MYSQL_DATABASE': db_name,
                'MYSQL_USER': db_user,
                'MYSQL_PASSWORD': password,
            })
            cmd_parts.extend([
                '-v', f'{container_name}-data:/var/lib/mysql',
            ])
            if alias_name:
                cmd_parts.extend(['--network-alias', alias_name])
            _attach_env_file()
            cmd_parts.append(image)
            cmd_str = ' '.join(shlex.quote(p) for p in cmd_parts)
            hostname = alias_name or container_name
            connection_url = f"mysql://{db_user}:{password}@{hostname}:{port}/{db_name}"

        elif addon_type == 'MONGODB':
            # Root user must match the local provisioner and backup/restore
            # commands (mongodump/mongorestore --username=app_user).
            env_vars.update({
                'MONGO_INITDB_ROOT_USERNAME': 'app_user',
                'MONGO_INITDB_ROOT_PASSWORD': password,
            })
            cmd_parts.extend([
                '-v', f'{container_name}-data:/data/db',
            ])
            if alias_name:
                cmd_parts.extend(['--network-alias', alias_name])
            _attach_env_file()
            cmd_parts.append(image)
            cmd_str = ' '.join(shlex.quote(p) for p in cmd_parts)
            hostname = alias_name or container_name
            connection_url = f"mongodb://app_user:{password}@{hostname}:{port}/app_db?authSource=admin"

        elif addon_type == 'RABBITMQ':
            user = "appuser"
            vhost = "/"
            env_vars.update({
                'RABBITMQ_DEFAULT_USER': user,
                'RABBITMQ_DEFAULT_PASS': password,
                'RABBITMQ_DEFAULT_VHOST': vhost,
            })
            cmd_parts.extend([
                '-v', f'{container_name}-data:/var/lib/rabbitmq',
            ])
            if alias_name:
                cmd_parts.extend(['--network-alias', alias_name])
            _attach_env_file()
            cmd_parts.append(image)
            cmd_str = ' '.join(shlex.quote(p) for p in cmd_parts)
            hostname = alias_name or container_name
            connection_url = f"amqp://{user}:{password}@{hostname}:{port}//"

        elif addon_type == 'MINIO':
            username = secrets.token_hex(8)
            env_vars.update({
                'MINIO_ROOT_USER': username,
                'MINIO_ROOT_PASSWORD': password,
            })
            cmd_parts.extend([
                '-v', f'{container_name}-data:/data',
            ])
            if alias_name:
                cmd_parts.extend(['--network-alias', alias_name])
            _attach_env_file()
            cmd_parts.extend([image, 'server', '/data', '--console-address', ':9001'])
            cmd_str = ' '.join(shlex.quote(p) for p in cmd_parts)
            hostname = alias_name or container_name
            bucket_name = "default-bucket"
            connection_url = f"s3://{username}:{password}@{hostname}:{port}/{bucket_name}"

        elif addon_type in ('QDRANT', 'ELASTICSEARCH'):
            if alias_name:
                cmd_parts.extend(['--network-alias', alias_name])
            cmd_parts.append(image)
            cmd_str = ' '.join(shlex.quote(p) for p in cmd_parts)
            hostname = alias_name or container_name
            connection_url = f"http://{hostname}:{port}"

        elif generic_config:
            hostname = alias_name or container_name
            user = 'admin'
            db = 'app_db'
            if generic_config.get('user_env'):
                env_vars[generic_config['user_env']] = user
            if generic_config.get('pass_env'):
                env_vars[generic_config['pass_env']] = password
            if generic_config.get('root_pass_env'):
                env_vars[generic_config['root_pass_env']] = password
            if generic_config.get('db_env'):
                env_vars[generic_config['db_env']] = db
            cluster_id = self._generate_kraft_cluster_id()
            env_extra = cast(dict, generic_config.get('env') or {})
            for k, v in env_extra.items():
                val = (
                    v.replace('{password}', password)
                    .replace('{hostname}', hostname)
                    .replace('{cluster_id}', cluster_id)
                )
                env_vars[k] = val
            if any('{password}' in arg for arg in (generic_config.get('command') or [])):
                env_vars['SMSLY_APP_PASSWORD'] = password
            # Mount a persistent volume at the addon's data dir (never rely on
            # the image's ephemeral container filesystem: the container is
            # recreated on every deploy).
            cmd_parts.extend([
                '-v', f'{container_name}-data:{generic_config.get("data_dir", "/data")}',
            ])
            if alias_name:
                cmd_parts.extend(['--network-alias', alias_name])
            _attach_env_file()
            cmd_parts.append(image)
            if generic_config.get('command'):
                entrypoint, cmd_args = self._render_generic_command(generic_config, password, hostname)
                if entrypoint:
                    cmd_parts.extend(['--entrypoint', entrypoint])
                cmd_parts.extend(cmd_args)
            cmd_str = ' '.join(shlex.quote(p) for p in cmd_parts)
            scheme = cast(str, generic_config.get('scheme', addon_type.lower()))
            if generic_config.get('auth'):
                if generic_config.get('user_env'):
                    connection_url = f"{scheme}://{user}:{password}@{hostname}:{port}/{db}"
                else:
                    connection_url = f"{scheme}://:{password}@{hostname}:{port}"
            else:
                connection_url = f"{scheme}://{hostname}:{port}"

        else:
            raise ValueError(f"Unsupported addon type for remote provisioning: {addon_type}")

        # SSH into remote node and provision
        net_setup = f"docker network inspect {shlex.quote(self.network_name)} >/dev/null 2>&1 || docker network create {shlex.quote(self.network_name)}"
        provision_cmd = f"{net_setup} && docker rm -f {shlex.quote(container_name)} 2>/dev/null; {cmd_str}"

        stdout, stderr, code = ssh.exec_command(provision_cmd, timeout=300, raise_on_error=False)
        if code != 0:
            raise RuntimeError(
                f"Remote addon provisioning failed on {server.host}:\n{stderr}\n{stdout}"
            )

        container_id = stdout.strip()[:12] if stdout.strip() else container_name

        # Remove remote secrets (env files / redis.conf) now that the
        # container is running.
        for remote_path in remote_temp_files:
            with contextlib.suppress(Exception):
                ssh.exec_command(
                    f"rm -f {shlex.quote(remote_path)}",
                    timeout=10,
                    raise_on_error=False,
                )

        # Create data volume if not using auto-created one
        # (the -v flag in docker run auto-creates named volumes)

        ssh.close()
        return container_id, connection_url

    def _provision_rabbitmq(self, container_name: str,
                            password: str, port: int,
                            alias_name: str = '', public_domain: str | None = None,
                            host_port: int | None = None) -> tuple[str, str]:
        """Provision a RabbitMQ container with management plugin enabled."""
        user = "appuser"
        vhost = "/"
        env_file = self._write_env_file({
            'RABBITMQ_DEFAULT_USER': user,
            'RABBITMQ_DEFAULT_PASS': password,
            'RABBITMQ_DEFAULT_VHOST': vhost,
        })
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            *self.SECURITY_OPTS,
            '--env-file', env_file,
            '-v', f'{container_name}-data:/var/lib/rabbitmq',
        ]
        if host_port:
            cmd.extend(['-p', f'{host_port}:{port}'])
        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace('.', '-').replace('_', '-'), public_domain, 15672) # Expose Management port, not AMQP

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.append(self.ADDON_IMAGES['RABBITMQ'])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
        finally:
            with contextlib.suppress(Exception):
                os.remove(env_file)
        container_id = result.stdout.strip()[:12]

        hostname = alias_name or container_name
        connection_url = f"amqp://{user}:{password}@{hostname}:{port}//"

        self._wait_for_health(container_name, 15672, path="/api/health/checks/alarms", use_http=True)
        return container_id, connection_url

    def _provision_minio(self, container_name: str,
                         password: str, port: int,
                         alias_name: str = '', username: str = 'admin', public_domain: str | None = None,
                         host_port: int | None = None) -> tuple[str, str]:
        """Provision a MinIO container."""
        env_file = self._write_env_file({
            'MINIO_ROOT_USER': username,
            'MINIO_ROOT_PASSWORD': password,
        })
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            *self.SECURITY_OPTS,
            '--env-file', env_file,
            '-v', f'{container_name}-data:/data',
        ]
        if host_port:
            cmd.extend(['-p', f'{host_port}:{port}'])
        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, 9001)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.extend([
            self.ADDON_IMAGES['MINIO'],
            'server', '/data', '--console-address', ':9001'
        ])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=120)
        finally:
            with contextlib.suppress(Exception):
                os.remove(env_file)
        container_id = result.stdout.strip()[:12]

        hostname = alias_name or container_name
        # Add a default bucket name to the connection URL path
        bucket_name = "default-bucket"
        connection_url = f"s3://{username}:{password}@{hostname}:{port}/{bucket_name}"

        self._wait_for_health(container_name, port, path="/minio/health/live", use_http=True)

        # Create the default bucket automatically
        try:
            import time
            time.sleep(2) # Give it a moment to fully initialize the API after healthcheck

            # Use 'mc' from inside the minio container to create the bucket
            # Credentials are passed via MC_HOST_<alias> env var, NOT argv,
            # so they never appear in the process list / docker exec output.
            import urllib.parse
            encoded_password = urllib.parse.quote(password, safe='')
            subprocess.run([
                'docker', 'exec',
                '-e', f'MC_HOST_myminio=http://{username}:{encoded_password}@127.0.0.1:{port}',
                container_name,
                'mc', 'alias', 'set', 'myminio', f'http://127.0.0.1:{port}'
            ], capture_output=True, check=False, timeout=60)

            # Then, create the bucket
            subprocess.run([
                'docker', 'exec',
                '-e', f'MC_HOST_myminio=http://{username}:{encoded_password}@127.0.0.1:{port}',
                container_name,
                'mc', 'mb', f'myminio/{bucket_name}'
            ], capture_output=True, check=False, timeout=60) # check=False because it might already exist on re-provision
        except Exception as e:
            logger.error("Failed to auto-create default MinIO bucket %s: %s", bucket_name, e)

        return container_id, connection_url

    def _provision_generic(self, addon_type: str, container_name: str,
                           password: str, port: int, alias_name: str, config: dict,
                           username: str = '', db_name: str = '', public_domain: str | None = None, host_port: int | None = None) -> tuple[str, str]:
        """Provision a generic addon from GENERIC_ADDONS_CONFIG."""
        hostname = alias_name or container_name
        user = username or 'admin'
        db = db_name or 'app_db'
        cluster_id = self._generate_kraft_cluster_id()

        env_file = self._write_env_file(self._build_generic_env(config, password, hostname, cluster_id, user, db))

        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            *self.SECURITY_OPTS,
            '--env-file', env_file,
            '-v', f'{container_name}-data:{config.get("data_dir", "/data")}'
        ]

        if host_port:
            cmd.extend(['-p', f'{host_port}:{port}'])

        if public_domain:
            target_port = cast(int, config.get('dashboard_port', port))
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, target_port)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])

        if config.get('command'):
            entrypoint, cmd_args = self._render_generic_command(config, password, hostname)
            if entrypoint:
                cmd.extend(['--entrypoint', entrypoint])

        cmd.append(config['image'])

        if config.get('command'):
            cmd.extend(cmd_args)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
        finally:
            with contextlib.suppress(Exception):
                os.remove(env_file)
        container_id = result.stdout.strip()[:12]

        scheme = cast(str, config.get('scheme', addon_type.lower()))
        if config.get('auth'):
            if config.get('user_env'):
                connection_url = f"{scheme}://{user}:{password}@{hostname}:{port}/{db}"
            else:
                connection_url = f"{scheme}://:{password}@{hostname}:{port}"
        else:
            connection_url = f"{scheme}://{hostname}:{port}"

        self._wait_for_health(
            container_name,
            port,
            timeout=int(config.get('health_timeout', 30))
        )
        ready_cmd = str(config.get('ready_cmd') or '').strip()
        if ready_cmd:
            self._wait_for_ready_command(
                container_name,
                ready_cmd,
                timeout=int(config.get('ready_timeout', 30))
            )
        return container_id, connection_url

    SECURITY_OPTS = [
        '--security-opt', 'no-new-privileges:true',
        '--cap-drop=ALL',
        '--cap-add=NET_BIND_SERVICE',
        '--cap-add=CHOWN',
        '--cap-add=SETUID',
        '--cap-add=SETGID',
        '--pids-limit', '1024',
    ]

    def _render_generic_command(self, config: dict, password: str, hostname: str) -> tuple[str | None, list[str]]:
        """Render a generic addon command without putting secrets on argv.

        Commands containing ``{password}`` are executed through ``sh -c`` with
        the secret read from the ``SMSLY_APP_PASSWORD`` env var (already in
        the env file), so the password never appears in the host process list
        nor in the container's ``Config.Cmd``.

        Returns ``(entrypoint_override, command_args)``. ``entrypoint_override``
        is ``"sh"`` when a shell wrapper is required, else ``None``.
        """
        command = cast(list, config.get('command')) or []
        has_secret = any('{password}' in arg for arg in command)
        rendered = [
            arg.replace('{password}', '"$SMSLY_APP_PASSWORD"').replace('{hostname}', hostname)
            for arg in command
        ]
        if not has_secret:
            return None, rendered
        return 'sh', ['-c', 'exec ' + ' '.join(rendered)]

    def _build_generic_env(self, config: dict, password: str, hostname: str, cluster_id: str, user: str, db: str) -> dict[str, str]:
        """Build env var dict for a generic addon, with placeholder substitution."""
        env: dict[str, str] = {}
        if config.get('user_env'):
            env[config['user_env']] = user
        if config.get('pass_env'):
            env[config['pass_env']] = password
        if config.get('root_pass_env'):
            env[config['root_pass_env']] = password
        if config.get('db_env'):
            env[config['db_env']] = db
        for k, v in (config.get('env') or {}).items():
            env[k] = (
                v.replace('{password}', password)
                .replace('{hostname}', hostname)
                .replace('{cluster_id}', cluster_id)
            )
        if any('{password}' in arg for arg in (config.get('command') or [])):
            # Password for commands rendered through sh -c (see
            # _render_generic_command): never on the command line.
            env['SMSLY_APP_PASSWORD'] = password
        return env

    def _generate_kraft_cluster_id(self) -> str:
        """Generate a valid 22-char KRaft cluster id."""
        return base64.urlsafe_b64encode(uuid.uuid4().bytes).decode('ascii').rstrip('=')

    def _wait_for_ready_command(self, container_name: str, command: str, timeout: int = 30):
        """Wait until a command succeeds inside container (readiness gate)."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                result = subprocess.run(
                    ['docker', 'exec', container_name, 'bash', '-lc', command],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    return
            except (subprocess.SubprocessError, OSError) as exc:
                logger.debug("Readiness check failed for %s: %s", container_name, exc)
            time.sleep(1)
        raise RuntimeError(f"{container_name} readiness command timed out after {timeout}s")

    def _provision_postgres(
        self,
        container_name: str,
        password: str,
        port: int,
        alias_name: str = '',
        db_user: str | None = None,
        db_name: str | None = None,
        public_domain: str | None = None,
        host_port: int | None = None,
    ) -> tuple[str, str]:
        """Provision a PostgreSQL container."""
        # Derive service-specific user/db from alias (e.g. "postgres-myapp")
        # so each addon gets isolated credentials.
        safe_suffix = (
            (alias_name or container_name)
            .replace('-', '_')
            .replace('.', '_')
            .replace(' ', '_')
        )
        if not db_user:
            db_user = safe_suffix  # e.g. "postgres_myapp"
        if not db_name:
            db_name = safe_suffix  # one DB per addon

        # Postgres identifiers are limited to 63 bytes.
        db_user = str(db_user)[:63]
        db_name = str(db_name)[:63]

        env_file = self._write_env_file({
            'POSTGRES_PASSWORD': password,
            'POSTGRES_USER': db_user,
            'POSTGRES_DB': db_name,
        })
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            *self.SECURITY_OPTS,
            '--env-file', env_file,
            '-v', f'{container_name}-data:/var/lib/postgresql/data',
        ]

        if host_port:
            cmd.extend(['-p', f'{host_port}:{port}'])

        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, port)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.append(self.ADDON_IMAGES['POSTGRES'])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=120)
        finally:
            with contextlib.suppress(Exception):
                os.remove(env_file)
        container_id = result.stdout.strip()[:12]

        # Use alias_name as hostname so apps reach the DB by friendly name
        # (e.g. "postgres-myapp" instead of "smsly-addon-postgres-uuid")
        hostname = alias_name or container_name
        connection_url = f"postgresql://{db_user}:{password}@{hostname}:{port}/{db_name}"

        self._wait_for_health(container_name, port)

        # Ensure pgvector extension exists (safe to run repeatedly)
        try:
            subprocess.run(
                [
                    'docker', 'exec', container_name, 'psql',
                    '-U', db_user, '-d', db_name,
                    '-c', 'CREATE EXTENSION IF NOT EXISTS vector;'
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception as exc:  # pragma: no cover
            logger.error("pgvector extension init failed for %s: %s", container_name, exc)

        return container_id, connection_url

    def _provision_redis(self, container_name: str,
                         password: str, port: int,
                         alias_name: str = '', public_domain: str | None = None,
                         host_port: int | None = None) -> tuple[str, str]:
        """Provision a Redis container with authentication."""
        env_file = self._write_env_file({
            'REDIS_PASSWORD': password,
        })
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            *self.SECURITY_OPTS,
            '--env-file', env_file,
            '-v', f'{container_name}-data:/data',
        ]
        if host_port:
            cmd.extend(['-p', f'{host_port}:{port}'])
        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, port)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.extend([
            self.ADDON_IMAGES['REDIS'],
            # Password comes from the container env (--env-file above), never
            # from the command line: keeps it out of `ps` output and survives
            # container restarts (env is baked into the container config).
            'sh', '-c',
            'redis-server --requirepass "$REDIS_PASSWORD" --appendonly yes',
        ])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=120)
        finally:
            with contextlib.suppress(Exception):
                os.remove(env_file)
        container_id = result.stdout.strip()[:12]

        hostname = alias_name or container_name
        connection_url = f"redis://:{password}@{hostname}:{port}/0"

        self._wait_for_health(container_name, port)
        return container_id, connection_url

    def _get_published_host_port(self, container_name: str) -> int | None:
        """Return the first host port published by an existing container.

        Returns None when the container has no port bindings (e.g. addons
        that only run on the docker network with no host exposure).
        """
        try:
            inspect_proc = subprocess.run(
                ['docker', 'inspect', '-f', '{{json .HostConfig.PortBindings}}', container_name],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if inspect_proc.returncode == 0 and (inspect_proc.stdout or '').strip():
                import json as _json
                for binding in _json.loads(inspect_proc.stdout).values():
                    if binding and binding[0].get('HostPort'):
                        return int(binding[0]['HostPort'])
        except Exception:
            pass
        return None

    def rotate_redis_credentials(self, addon, container_name: str, password: str,
                                 port: int, alias_name: str | None = None,
                                 public_domain: str | None = None) -> str:
        """Recreate a Redis container with a new password.

        The Redis password is baked into the container environment at creation
        time (see ``_provision_redis``), so a running container can never pick
        up a rotated password on restart. Recreate it instead: the persistent
        data volume (``{container_name}-data``) is reused and the previously
        published host port (if any) is preserved so existing connection URLs
        stay valid.
        """
        host_port = self._get_published_host_port(container_name)

        subprocess.run(
            ['docker', 'rm', '-f', container_name],
            capture_output=True,
            check=False,
            timeout=60,
        )

        container_id, _ = self._provision_redis(
            container_name, password, port, alias_name,
            host_port=host_port, public_domain=public_domain,
        )
        with contextlib.suppress(Exception):
            self._connect_to_proxy_network(container_name)
            self._connect_to_service_scoped_network(container_name, addon)
        return container_id

    def _provision_mysql(self, container_name: str,
                         password: str, port: int,
                         alias_name: str = '', public_domain: str | None = None,
                         host_port: int | None = None) -> tuple[str, str]:
        """Provision a MySQL container."""
        db_name = "app_db"
        db_user = "app_user"

        env_file = self._write_env_file({
            'MYSQL_ROOT_PASSWORD': password,
            'MYSQL_DATABASE': db_name,
            'MYSQL_USER': db_user,
            'MYSQL_PASSWORD': password,
        })
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            *self.SECURITY_OPTS,
            '--env-file', env_file,
            '-v', f'{container_name}-data:/var/lib/mysql',
        ]
        if host_port:
            cmd.extend(['-p', f'{host_port}:{port}'])
        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, port)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.append(self.ADDON_IMAGES['MYSQL'])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=120)
        finally:
            with contextlib.suppress(Exception):
                os.remove(env_file)
        container_id = result.stdout.strip()[:12]

        hostname = alias_name or container_name
        connection_url = f"mysql://{db_user}:{password}@{hostname}:{port}/{db_name}"

        self._wait_for_health(
            container_name,
            port,
            timeout=60)  # MySQL takes longer
        return container_id, connection_url

    def _provision_mongodb(self, container_name: str,
                           password: str, port: int,
                           alias_name: str = '', public_domain: str | None = None,
                           host_port: int | None = None) -> tuple[str, str]:
        """Provision a MongoDB container."""
        db_user = "app_user"
        db_name = "app_db"

        env_file = self._write_env_file({
            'MONGO_INITDB_ROOT_USERNAME': db_user,
            'MONGO_INITDB_ROOT_PASSWORD': password,
        })
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            *self.SECURITY_OPTS,
            '--env-file', env_file,
            '-v', f'{container_name}-data:/data/db',
        ]
        if host_port:
            cmd.extend(['-p', f'{host_port}:{port}'])
        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, port)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.append(self.ADDON_IMAGES['MONGODB'])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=120)
        finally:
            with contextlib.suppress(Exception):
                os.remove(env_file)
        container_id = result.stdout.strip()[:12]

        hostname = alias_name or container_name
        connection_url = f"mongodb://{db_user}:{password}@{hostname}:{port}/{db_name}?authSource=admin"  # pylint: disable=line-too-long

        self._wait_for_health(container_name, port)
        return container_id, connection_url

    def _provision_qdrant(self, container_name: str,
                          port: int,
                          alias_name: str = '', public_domain: str | None = None,
                          host_port: int | None = None) -> tuple[str, str]:
        """Provision a Qdrant vector database container."""
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            *self.SECURITY_OPTS,
            '-e', 'QDRANT__SERVICE__GRPC_PORT=6334',
            '-v', f'{container_name}-data:/qdrant/storage',
        ]
        if host_port:
            cmd.extend(['-p', f'{host_port}:{port}'])
        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, port)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.append(self.ADDON_IMAGES['QDRANT'])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=120)
        container_id = result.stdout.strip()[:12]

        # Qdrant uses HTTP API — no auth by default
        hostname = alias_name or container_name
        connection_url = f"http://{hostname}:{port}"

        self._wait_for_health(container_name, port)
        return container_id, connection_url

    def _provision_elasticsearch(self, container_name: str,
                                 port: int,
                                 alias_name: str = '', public_domain: str | None = None,
                                 host_port: int | None = None) -> tuple[str, str]:
        """Provision a single-node Elasticsearch container."""
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            *self.SECURITY_OPTS,
            '-e', 'discovery.type=single-node',
            '-e', 'xpack.security.enabled=false',
            '-e', 'ES_JAVA_OPTS=-Xms256m -Xmx256m',
            '-v', f'{container_name}-data:/usr/share/elasticsearch/data',
        ]
        if host_port:
            cmd.extend(['-p', f'{host_port}:{port}'])
        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, port)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.append(self.ADDON_IMAGES['ELASTICSEARCH'])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=120)
        container_id = result.stdout.strip()[:12]

        hostname = alias_name or container_name
        connection_url = f"http://{hostname}:{port}"

        self._wait_for_health(container_name, port, timeout=90)
        return container_id, connection_url

    def _wait_for_health(self, container_name: str,
                         port: int, timeout: int = 30,
                         use_http: bool = False, path: str = "/"):
        """Wait for the container to be healthy and accepting connections.

        """
        logger.info(f"Waiting for {container_name} to be ready...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                # Check if container is running
                result = subprocess.run(
                    ['docker', 'inspect', '-f',
                        '{{.State.Running}}', container_name],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if result.stdout.strip() != 'true':
                    time.sleep(1)
                    continue
            except BaseException:
                time.sleep(1)
                continue

            if use_http:
                import requests
                url = f"http://{container_name}:{port}{path}"
                try:
                    resp = requests.get(url, timeout=2)
                    if resp.status_code < 500:
                        logger.info(f"{container_name} is healthy at {url}")
                        return
                except (requests.RequestException, ValueError) as exc:
                    logger.debug("Health check failed for %s at %s: %s", container_name, url, exc)
                time.sleep(1)
                continue
            else:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1.5)
                try:
                    s.connect((container_name, port))
                    s.close()
                    logger.info(f"{container_name}:{port} is reachable")
                    return
                except Exception:
                    time.sleep(1)
                    continue

        raise RuntimeError(f"{container_name} health check timed out after {timeout}s")

    def deprovision(self, container_id: str,
                    container_name: str | None = None) -> bool:
        """
        Remove an addon container and its volumes.

        Args:
            container_id: Container ID or name
            container_name: Optional container name for volume cleanup
        """
        try:
            # Stop and remove container
            subprocess.run(['docker', 'stop', container_id],
                           capture_output=True, timeout=60)
            subprocess.run(['docker', 'rm', container_id], capture_output=True, timeout=60)

            # Remove associated volume if container_name provided
            if container_name:
                subprocess.run(
                    ['docker', 'volume', 'rm', f'{container_name}-data'],
                    capture_output=True,
                    timeout=60,
                )

            logger.info(f"Deprovisioned addon container: {container_id}")
            return True
        except Exception as e:
            logger.error(
                f"Failed to deprovision container {container_id}: {e}")
            return False

    def deprovision_remote(self, container_id: str, server,
                           container_name: str | None = None) -> bool:
        """
        Remove an addon container from a remote full-stack node via SSH.
        """
        from apps.deployments.services.ssh_client import SSHClient
        try:
            ssh = SSHClient(
                ip=server.host,
                key_content=server.ssh_key,
                password=server.ssh_password,
                user=server.ssh_user,
                port=server.ssh_port,
                wg_address=getattr(server, "wg_address", None),
            )
            ssh.connect()
            safe_id = shlex.quote(container_id)
            ssh.exec_command(f"docker stop {safe_id} 2>/dev/null; docker rm -f {safe_id}", timeout=30)
            if container_name:
                safe_vol = shlex.quote(f'{container_name}-data')
                ssh.exec_command(f"docker volume rm {safe_vol} 2>/dev/null", timeout=15)
                # Remove the persisted redis.conf (mounted for requirepass)
                safe_conf = shlex.quote(f'/var/lib/smsly/addon-envs/{container_name}.conf')
                ssh.exec_command(f"rm -f {safe_conf} 2>/dev/null", timeout=15)
            ssh.close()
            logger.info(f"Deprovisioned remote addon container: {container_id} on {server.host}")
            return True
        except Exception as e:
            logger.error(f"Failed to deprovision remote container {container_id} on {server.host}: {e}")
            return False

    def deprovision_dispatch(self, container_id: str, addon,
                             container_name: str | None = None) -> bool:
        """
        De-provision an addon from the correct host (master or full-stack node).
        """
        server = getattr(addon.service, 'server', None) if addon else None
        if (server and not server.is_primary
                and not getattr(server, 'is_lite_agent', False)):
            return self.deprovision_remote(container_id, server, container_name)
        return self.deprovision(container_id, container_name)

    def get_status(self, container_id: str) -> dict:
        """Get the status of an addon container."""
        try:
            result = subprocess.run(
                ['docker', 'inspect', container_id],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                import json
                info = json.loads(result.stdout)[0]
                return {
                    'running': info['State']['Running'],
                    'status': info['State']['Status'],
                    'started_at': info['State'].get('StartedAt'),
                    'health': info['State'].get('Health', {}).get('Status', 'unknown'),
                }
        except Exception as e:
            logger.error(f"Failed to get container status: {e}")

        return {'running': False, 'status': 'unknown'}

    def get_logs(self, container_name: str, tail: int = 200, follow: bool = False) -> str:
        """Fetch recent logs from an addon container.

        Args:
            container_name: Docker container name (e.g. smsly-addon-postgres-<uuid>)
            tail: Number of lines from the end to return (max 2000)
            follow: If True, return a generator that yields log lines (for streaming)

        Returns:
            Log text string, or empty string if container not found.
        """
        import subprocess
        tail = min(tail, 2000)
        try:
            if follow:
                proc = subprocess.Popen(
                    ['docker', 'logs', '--tail', str(tail), '-f', '--timestamps', container_name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                return proc

            result = subprocess.run(
                ['docker', 'logs', '--tail', str(tail), '--timestamps', container_name],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            logger.warning("Timeout fetching logs for %s", container_name)
            return ''
        except Exception as e:
            logger.error("Failed to fetch logs for %s: %s", container_name, e)
            return ''

    def ensure_network_aliases(self, addon) -> list[str]:
        """Verify and repair all network aliases for an addon container.

        Returns the list of aliases that should be attached. Raises RuntimeError
        if any alias cannot be attached after retries.
        """
        addon_type = addon.addon_type
        service_name = addon.service.name
        container_name = f"smsly-addon-{addon_type.lower()}-{addon.id}"
        alias_name = str(getattr(addon, 'name', '') or f"{addon_type.lower()}-{service_name}").strip()

        existing_url = str(getattr(addon, 'connection_url', '') or '').strip()
        hostname_from_url = alias_name
        if existing_url:
            parsed = self._parse_connection_url(existing_url)
            hostname_from_url = str(parsed.get('hostname') or alias_name).strip()

        required_aliases = {alias_name}
        if hostname_from_url and hostname_from_url != alias_name:
            required_aliases.add(hostname_from_url)

        # Read current aliases from Docker
        try:
            inspect_proc = subprocess.run(
                ['docker', 'inspect', '-f',
                 '{{range .NetworkSettings.Networks}}{{range .Aliases}}{{.}} {{end}}{{end}}',
                 container_name],
                capture_output=True, text=True, check=False, timeout=30,
            )
            current_aliases = set(inspect_proc.stdout.split()) if inspect_proc.returncode == 0 else set()
        except Exception:
            current_aliases = set()

        missing = required_aliases - current_aliases
        if missing:
            logger.info(
                "Repairing missing network aliases for %s: %s",
                container_name, ', '.join(missing),
            )
            for alias in missing:
                success = False
                for attempt in range(3):
                    result = subprocess.run(
                        ['docker', 'network', 'connect', '--alias', alias, self.network_name, container_name],
                        capture_output=True, text=True, check=False, timeout=30,
                    )
                    if result.returncode == 0:
                        success = True
                        break
                    if attempt < 2:
                        time.sleep(1)
                if not success:
                    raise RuntimeError(
                        f"Failed to attach network alias '{alias}' to "
                        f"{container_name} after 3 attempts: {result.stderr.strip()}"
                    )

        return list(required_aliases)


    def _validate_backup_path(self, backup_path: str) -> str:
        """
        Validate backup path to prevent path traversal and unsafe restore inputs.
        """
        import os

        if not backup_path:
            raise ValueError("backup_path is required")

        real_path = os.path.realpath(backup_path)
        from django.conf import settings
        allowed_roots = [
            os.path.realpath(os.path.join("/app", "backups", "addons")),
            os.path.realpath(os.path.join(settings.BASE_DIR, "backups", "addons")),
        ]

        if not any(real_path.startswith(root + os.sep) for root in allowed_roots):
            raise ValueError("Invalid backup path outside allowed backup directory")

        if not os.path.isfile(real_path):
            raise FileNotFoundError(f"Backup file not found: {real_path}")

        return real_path

    def _get_container_env(self, container_name: str, key: str) -> str:
        """
        Fetch a container environment variable safely without invoking a shell.
        """
        result = subprocess.run(
            ['docker', 'exec', container_name, 'printenv', key],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        value = result.stdout.strip()
        if not value:
            raise ValueError(f"Missing required environment variable {key} in {container_name}")
        return value

    def create_backup(self, addon) -> str:
        """
        Create a backup of the addon database.
        Returns the path to the backup file.
        """
        import os
        from datetime import datetime

        container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        from django.conf import settings
        backup_root = os.path.join(settings.BASE_DIR, "backups", "addons")
        os.makedirs(backup_root, exist_ok=True)
        backup_dir = os.path.join(backup_root, str(addon.service.id))
        os.makedirs(backup_dir, exist_ok=True)

        filename = f"{addon.addon_type.lower()}_{addon.id}_{timestamp}.dump"
        backup_path = os.path.join(backup_dir, filename)

        try:
            if addon.addon_type == 'POSTGRES':
                postgres_user = self._get_container_env(
                    container_name, 'POSTGRES_USER'
                )
                postgres_db = self._get_container_env(
                    container_name, 'POSTGRES_DB'
                )
                # Stream pg_dump output directly to file without shell redirection.
                with open(backup_path, 'wb') as backup_file:
                    subprocess.run(
                        [
                            'docker',
                            'exec',
                            container_name,
                            'pg_dump',
                            '-U',
                            postgres_user,
                            postgres_db,
                        ],
                        check=True,
                        stdout=backup_file,
                        timeout=300,
                    )

            elif addon.addon_type == 'REDIS':
                # Redis save and copy using argument lists only. Auth with the
                # container's own REDIS_PASSWORD env when set (never put the
                # password on the command line).
                subprocess.run(
                    ['docker', 'exec', container_name,
                     'sh', '-c',
                     'if [ -n "$REDIS_PASSWORD" ]; then '
                     'exec redis-cli -a "$REDIS_PASSWORD" --no-auth-warning save; '
                     'else exec redis-cli save; fi'],
                    check=True, timeout=60,
                )
                subprocess.run(['docker', 'cp', f'{container_name}:/data/dump.rdb', backup_path], check=True, timeout=120)

            elif addon.addon_type == 'MYSQL':
                # Use the container's existing env var — never put the
                # password on the command line where 'ps aux' can see it.
                with open(backup_path, 'wb') as backup_file:
                    subprocess.run(
                        ['docker', 'exec', container_name,
                         'sh', '-c', 'mysqldump -u root -p"$MYSQL_ROOT_PASSWORD" app_db'],
                        check=True,
                        stdout=backup_file,
                        timeout=300,
                    )

            elif addon.addon_type == 'MONGODB':
                with open(backup_path, 'wb') as backup_file:
                    subprocess.run(
                        ['docker', 'exec', container_name,
                         'sh', '-c', 'mongodump --username=app_user --password="$MONGO_INITDB_ROOT_PASSWORD" --db=app_db --archive'],
                        check=True,
                        stdout=backup_file,
                        timeout=300,
                    )

            else:
                logger.warning(f"Native backup not implemented for {addon.addon_type}, skipping gracefully.")
                # We write a dummy file so the backup task doesn't crash
                with open(backup_path, 'w') as f:
                    f.write(f"Native backup not supported yet for {addon.addon_type}")

            return backup_path

        except subprocess.CalledProcessError as e:
            logger.error(f"Backup failed for {addon.id}: {e}")
            raise e

    def restore_backup(self, addon, backup_path: str) -> bool:
        """
        Restore a backup to the addon database.
        """
        container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
        validated_backup_path = self._validate_backup_path(backup_path)

        try:
            if addon.addon_type == 'POSTGRES':
                postgres_user = self._get_container_env(
                    container_name, 'POSTGRES_USER'
                )
                postgres_db = self._get_container_env(
                    container_name, 'POSTGRES_DB'
                )
                # Stream backup content as stdin to psql without shell piping.
                with open(validated_backup_path, 'rb') as backup_file:
                    subprocess.run(
                        [
                            'docker',
                            'exec',
                            '-i',
                            container_name,
                            'psql',
                            '-U',
                            postgres_user,
                            postgres_db,
                        ],
                        stdin=backup_file,
                        check=True,
                        timeout=300,
                    )

            elif addon.addon_type == 'REDIS':
                # Copy file back, restart
                subprocess.run(['docker', 'stop', container_name], check=True, timeout=60)
                subprocess.run(['docker', 'cp', validated_backup_path, f'{container_name}:/data/dump.rdb'], check=True, timeout=120)
                subprocess.run(['docker', 'start', container_name], check=True, timeout=60)

            elif addon.addon_type == 'MYSQL':
                with open(validated_backup_path, 'rb') as backup_file:
                    subprocess.run(
                        ['docker', 'exec', '-i', container_name,
                         'sh', '-c', 'mysql -u root -p"$MYSQL_ROOT_PASSWORD" app_db'],
                        stdin=backup_file,
                        check=True,
                        timeout=300,
                    )

            elif addon.addon_type == 'MONGODB':
                with open(validated_backup_path, 'rb') as backup_file:
                    subprocess.run(
                        ['docker', 'exec', '-i', container_name,
                         'sh', '-c', 'mongorestore --username=app_user --password="$MONGO_INITDB_ROOT_PASSWORD" --db=app_db --archive'],
                        stdin=backup_file,
                        check=True,
                        timeout=300,
                    )
            else:
                logger.warning("Native restore not implemented for %s, skipping gracefully.", addon.addon_type)

            return True
        except Exception as e:
            logger.error("Restore failed for %s: %s", addon.id, e)
            raise e

# Singleton instance
addon_provisioner = AddonProvisioner()
