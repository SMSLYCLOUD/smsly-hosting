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
import secrets
import subprocess
import logging
import time
from urllib.parse import urlparse
from typing import Dict, Optional, Tuple
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
        'MARIADB': {"image": "mariadb:10.11", "port": 3306, "env_url": "MARIADB_URL", "auth": True, "user_env": "MARIADB_USER", "pass_env": "MARIADB_PASSWORD", "db_env": "MARIADB_DATABASE", "root_pass_env": "MARIADB_ROOT_PASSWORD"},
        'COCKROACHDB': {"image": "cockroachdb/cockroach:v23.1.10", "port": 26257, "dashboard_port": 8080, "env_url": "COCKROACHDB_URL", "command": ["start-single-node", "--insecure"], "auth": False},
        'TIMESCALEDB': {"image": "timescale/timescaledb:latest-pg15", "port": 5432, "env_url": "DATABASE_URL", "auth": True, "user_env": "POSTGRES_USER", "pass_env": "POSTGRES_PASSWORD", "db_env": "POSTGRES_DB"},
        'PERCONA': {"image": "percona:8.0", "port": 3306, "env_url": "MYSQL_URL", "auth": True, "user_env": "MYSQL_USER", "pass_env": "MYSQL_PASSWORD", "db_env": "MYSQL_DATABASE", "root_pass_env": "MYSQL_ROOT_PASSWORD"},
        'VITESS': {"image": "vitess/lite:latest", "port": 15306, "dashboard_port": 15000, "env_url": "VITESS_URL", "auth": False},
        'COUCHDB': {"image": "couchdb:3.3.3", "port": 5984, "env_url": "COUCHDB_URL", "auth": True, "user_env": "COUCHDB_USER", "pass_env": "COUCHDB_PASSWORD"},
        'RETHINKDB': {"image": "rethinkdb:2.4", "port": 28015, "dashboard_port": 8080, "env_url": "RETHINKDB_URL", "auth": False},
        'ARANGODB': {"image": "arangodb:3.11", "port": 8529, "env_url": "ARANGODB_URL", "auth": True, "root_pass_env": "ARANGO_ROOT_PASSWORD"},
        'FERRETDB': {"image": "ghcr.io/ferretdb/ferretdb:latest", "port": 27017, "env_url": "MONGODB_URI", "auth": False},
        'SURREALDB': {"image": "surrealdb/surrealdb:latest", "port": 8000, "env_url": "SURREALDB_URL", "command": ["start", "--user", "root", "--pass", "{password}"], "auth": True},
        'MEMCACHED': {"image": "memcached:1.6-alpine", "port": 11211, "env_url": "MEMCACHED_URL", "auth": False},
        'KEYDB': {"image": "eqalpha/keydb:latest", "port": 6379, "env_url": "KEYDB_URL", "auth": True, "command": ["keydb-server", "--requirepass", "{password}"]},
        'VALKEY': {"image": "valkey/valkey:7.2", "port": 6379, "env_url": "VALKEY_URL", "auth": True, "command": ["valkey-server", "--requirepass", "{password}"]},
        'DRAGONFLYDB': {"image": "docker.dragonflydb.io/dragonflydb/dragonfly:latest", "port": 6379, "env_url": "DRAGONFLY_URL", "auth": True, "command": ["dragonfly", "--requirepass", "{password}"]},
        'ETCD': {"image": "bitnami/etcd:3.5", "port": 2379, "env_url": "ETCD_URL", "auth": False, "env": {"ALLOW_NONE_AUTHENTICATION": "yes"}},
        'CLICKHOUSE': {"image": "clickhouse/clickhouse-server:23.8", "port": 8123, "env_url": "CLICKHOUSE_URL", "auth": True, "user_env": "CLICKHOUSE_USER", "pass_env": "CLICKHOUSE_PASSWORD"},
        'CASSANDRA': {"image": "cassandra:4.1", "port": 9042, "env_url": "CASSANDRA_URL", "auth": False},
        'SCYLLADB': {"image": "scylladb/scylla:5.2.0", "port": 9042, "env_url": "SCYLLADB_URL", "auth": False},
        'NEO4J': {"image": "neo4j:5.12.0", "port": 7687, "dashboard_port": 7474, "env_url": "NEO4J_URL", "auth": True, "env": {"NEO4J_AUTH": "neo4j/{password}"}},
        'DGRAPH': {"image": "dgraph/standalone:v23.0.0", "port": 8080, "env_url": "DGRAPH_URL", "auth": False},
        'WEAVIATE': {"image": "semitechnologies/weaviate:1.21.2", "port": 8080, "env_url": "WEAVIATE_URL", "auth": False, "env": {"AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED": "true", "PERSISTENCE_DATA_PATH": "/var/lib/weaviate"}},
        'MILVUS': {"image": "milvusdb/milvus:v2.3.1", "port": 19530, "env_url": "MILVUS_URL", "auth": False, "command": ["milvus", "run", "standalone"]},
        'CHROMADB': {"image": "chromadb/chroma:0.4.14", "port": 8000, "env_url": "CHROMADB_URL", "auth": False},
        'OPENSEARCH': {"image": "opensearchproject/opensearch:2.11.0", "port": 9200, "env_url": "OPENSEARCH_URL", "auth": True, "env": {"discovery.type": "single-node", "OPENSEARCH_INITIAL_ADMIN_PASSWORD": "{password}"}},
        'MEILISEARCH': {"image": "getmeili/meilisearch:v1.4.0", "port": 7700, "env_url": "MEILISEARCH_URL", "auth": True, "pass_env": "MEILI_MASTER_KEY"},
        'TYPESENSE': {"image": "typesense/typesense:0.25.1", "port": 8108, "env_url": "TYPESENSE_URL", "auth": True, "pass_env": "TYPESENSE_API_KEY", "command": ["--data-dir", "/data", "--api-key", "{password}"]},
        'SOLR': {"image": "solr:9.3", "port": 8983, "env_url": "SOLR_URL", "auth": False},
        'KAFKA': {"image": "bitnami/kafka:3.5.1", "port": 9092, "env_url": "KAFKA_URL", "auth": False, "env": {"KAFKA_ENABLE_KRAFT": "yes", "KAFKA_CFG_PROCESS_ROLES": "broker,controller", "KAFKA_CFG_CONTROLLER_LISTENER_NAMES": "CONTROLLER", "KAFKA_CFG_LISTENERS": "PLAINTEXT://:9092,CONTROLLER://:9093", "KAFKA_CFG_LISTENER_SECURITY_PROTOCOL_MAP": "CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT", "KAFKA_CFG_ADVERTISED_LISTENERS": "PLAINTEXT://localhost:9092", "KAFKA_CFG_CONTROLLER_QUORUM_VOTERS": "1@localhost:9093", "KAFKA_KRAFT_CLUSTER_ID": "abcdefghijklmnopqrstuv", "KAFKA_BROKER_ID": "1"}},
        'NATS': {"image": "nats:2.9.22-alpine", "port": 4222, "env_url": "NATS_URL", "auth": False},
        'REDPANDA': {"image": "redpandadata/redpanda:v23.2.14", "port": 9092, "env_url": "REDPANDA_URL", "auth": False, "command": ["redpanda", "start", "--overprovisioned", "--smp", "1", "--memory", "1G", "--reserve-memory", "0M", "--node-id", "0", "--check=false"]},
        'PULSAR': {"image": "apachepulsar/pulsar:3.1.0", "port": 6650, "env_url": "PULSAR_URL", "auth": False, "command": ["bin/pulsar", "standalone"]},
        'ACTIVEMQ': {"image": "apache/activemq-classic:5.18.3", "port": 61616, "dashboard_port": 8161, "env_url": "ACTIVEMQ_URL", "auth": True, "env": {"ACTIVEMQ_ADMIN_LOGIN": "admin", "ACTIVEMQ_ADMIN_PASSWORD": "{password}"}},
        'SEAWEEDFS': {"image": "chrislusf/seaweedfs:3.59", "port": 8888, "env_url": "SEAWEEDFS_URL", "auth": False, "command": ["server", "-dir=/data", "-s3"]},
        'INFLUXDB': {"image": "influxdb:2.7-alpine", "port": 8086, "env_url": "INFLUXDB_URL", "auth": True, "env": {"DOCKER_INFLUXDB_INIT_MODE": "setup", "DOCKER_INFLUXDB_INIT_USERNAME": "admin", "DOCKER_INFLUXDB_INIT_PASSWORD": "{password}", "DOCKER_INFLUXDB_INIT_ORG": "myorg", "DOCKER_INFLUXDB_INIT_BUCKET": "mybucket"}},
        'QUESTDB': {"image": "questdb/questdb:7.3.1", "port": 9000, "env_url": "QUESTDB_URL", "auth": False},
        'VICTORIAMETRICS': {"image": "victoriametrics/victoria-metrics:v1.93.4", "port": 8428, "env_url": "VICTORIAMETRICS_URL", "auth": False},
        'PROMETHEUS': {"image": "prom/prometheus:v2.47.0", "port": 9090, "env_url": "PROMETHEUS_URL", "auth": False},
        'GRAFANA': {"image": "grafana/grafana:10.1.5", "port": 3000, "env_url": "GRAFANA_URL", "auth": True, "env": {"GF_SECURITY_ADMIN_PASSWORD": "{password}"}},
        'JAEGER': {"image": "jaegertracing/all-in-one:1.49", "port": 16686, "env_url": "JAEGER_URL", "auth": False},
        'N8N': {"image": "n8nio/n8n:1.8.0", "port": 5678, "env_url": "N8N_URL", "auth": True, "env": {"N8N_BASIC_AUTH_ACTIVE": "true", "N8N_BASIC_AUTH_USER": "admin", "N8N_BASIC_AUTH_PASSWORD": "{password}"}},
        'TEMPORAL': {"image": "temporalio/auto-setup:1.22.1", "port": 7233, "dashboard_port": 8080, "env_url": "TEMPORAL_URL", "auth": False},
        'VAULT': {"image": "hashicorp/vault:1.15", "port": 8200, "env_url": "VAULT_URL", "auth": True, "env": {"VAULT_DEV_ROOT_TOKEN_ID": "{password}", "VAULT_DEV_LISTEN_ADDRESS": "0.0.0.0:8200"}},
        'CONSUL': {"image": "hashicorp/consul:1.16", "port": 8500, "env_url": "CONSUL_URL", "auth": False, "command": ["agent", "-dev", "-client", "0.0.0.0"]},
        'KEYCLOAK': {"image": "quay.io/keycloak/keycloak:22.0.4", "port": 8080, "env_url": "KEYCLOAK_URL", "auth": True, "env": {"KEYCLOAK_ADMIN": "admin", "KEYCLOAK_ADMIN_PASSWORD": "{password}"}, "command": ["start-dev"]},
    }

    def __init__(self):
        self.network_name = config(
            'DOCKER_NETWORK',
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

    def _append_traefik_labels(self, cmd_list: list, router_name: str, domain: str, target_port: int):
        """Append standard Traefik labels to expose an addon container publicly."""
        import os
        enable_tls = (str(os.getenv("TRAEFIK_ENABLE_WEBSECURE", "false")).strip().lower() in {"1", "true", "yes", "on"})
        cmd_list.extend(['-l', 'traefik.enable=true'])
        cmd_list.extend(['-l', f'traefik.http.routers.{router_name}.rule=Host(`{domain}`)'])
        cmd_list.extend(['-l', f'traefik.http.services.{router_name}.loadbalancer.server.port={target_port}'])
        cmd_list.extend(['-l', f'traefik.http.routers.{router_name}.priority=100'])
        cmd_list.extend(['-l', f'traefik.docker.network={self.network_name}'])
        if enable_tls:
            cmd_list.extend(['-l', f'traefik.http.routers.{router_name}.entrypoints=web,websecure'])
            cmd_list.extend(['-l', f'traefik.http.routers.{router_name}.tls.certresolver=letsencrypt'])
        else:
            cmd_list.extend(['-l', f'traefik.http.routers.{router_name}.entrypoints=web'])

    def _container_status(self, container_name: str) -> Tuple[Optional[str], bool]:
        """
        Return (container_id, is_running) for a given docker container name.

        Returns (None, False) if the container does not exist or docker inspect fails.
        """
        try:
            result = subprocess.run(
                ['docker', 'inspect', '-f', '{{.Id}} {{.State.Running}}', container_name],
                capture_output=True,
                text=True,
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
            )
            return result.returncode == 0
        except Exception:
            return False

    def _parse_connection_url(self, url: str) -> Dict[str, object]:
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
        """Ensure the Docker network exists for service connectivity."""
        if self._network_checked:
            return
        try:
            result = subprocess.run(
                ['docker', 'network', 'inspect', self.network_name],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                subprocess.run(
                    ['docker', 'network', 'create', self.network_name],
                    check=True
                )
                logger.info(f"Created Docker network: {self.network_name}")
            self._network_checked = True
        except Exception as e:
            logger.warning(f"Could not create/verify network: {e}")

    def provision(self, addon) -> Tuple[str, str]:
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
            image = generic_config['image']
            port = generic_config['port']

        if not image:
            raise ValueError(f"Unknown addon type: {addon_type}")


        logger.info(f"Provisioning {addon_type} addon for service {service_name}")

        # If the container already exists, never "re-provision" (which would rotate passwords).
        existing_cid, is_running = self._container_status(container_name)
        if existing_cid:
            if not is_running:
                logger.info("Starting existing addon container: %s", container_name)
                self._start_container(container_name)
                time.sleep(1)
                existing_cid, _ = self._container_status(container_name)

            # Enforce network connection for existing containers to fix missing aliases
            # when upgrading from older platforms or recovering from network drops.
            parsed_hostname = alias_name or container_name
            if existing_url:
                parsed_hostname = str(self._parse_connection_url(existing_url).get('hostname') or parsed_hostname).strip()

            try:
                # Check if the container is already connected to the network with the correct alias
                # to prevent dropping active database connections on every deployment.
                inspect_proc = subprocess.run(
                    ['docker', 'inspect', '-f', f'{{{{range .NetworkSettings.Networks}}}}{{{{.Aliases}}}}{{{{end}}}}', container_name],
                    capture_output=True,
                    text=True,
                    check=False
                )

                # If the alias is not found in the output, attempt to attach it.
                if parsed_hostname not in inspect_proc.stdout:
                    subprocess.run(
                        ['docker', 'network', 'disconnect', self.network_name, container_name],
                        capture_output=True,
                        check=False
                    )
                    subprocess.run(
                        ['docker', 'network', 'connect', '--alias', parsed_hostname, self.network_name, container_name],
                        capture_output=True,
                        check=False
                    )
            except Exception as e:
                logger.debug("Network reconnect failed/ignored for %s: %s", container_name, e)

            # Wait for readiness to reduce flakiness on immediate retries.
            try:
                if addon_type == 'RABBITMQ':
                    self._wait_for_health(
                        container_name,
                        port,
                        path="/api/health/checks/alarms",
                        use_http=True,
                    )
                else:
                    self._wait_for_health(container_name, port)
            except Exception as exc:  # pragma: no cover
                logger.warning("Addon health check failed for %s: %s", container_name, exc)

            if existing_url:
                return existing_cid, existing_url

            # URL missing in DB but container exists (e.g. task crashed after docker run).
            # Attempt best-effort reconstruction from container config to avoid password rotation.
            hostname = alias_name or container_name
            try:
                if addon_type == 'MINIO':
                    minio_user = self._get_container_env(container_name, 'MINIO_ROOT_USER')
                    minio_password = self._get_container_env(container_name, 'MINIO_ROOT_PASSWORD')
                    return existing_cid, f"s3://{minio_user}:{minio_password}@{hostname}:{port}"

                if addon_type == 'POSTGRES':
                    db_user = self._get_container_env(container_name, 'POSTGRES_USER')
                    db_name = self._get_container_env(container_name, 'POSTGRES_DB')
                    password = self._get_container_env(container_name, 'POSTGRES_PASSWORD')
                    return existing_cid, f"postgresql://{db_user}:{password}@{hostname}:{port}/{db_name}"

                if addon_type == 'MYSQL':
                    db_user = self._get_container_env(container_name, 'MYSQL_USER')
                    db_name = self._get_container_env(container_name, 'MYSQL_DATABASE')
                    password = self._get_container_env(container_name, 'MYSQL_PASSWORD')
                    return existing_cid, f"mysql://{db_user}:{password}@{hostname}:{port}/{db_name}"

                if addon_type == 'MONGODB':
                    db_user = self._get_container_env(container_name, 'MONGO_INITDB_ROOT_USERNAME')
                    password = self._get_container_env(container_name, 'MONGO_INITDB_ROOT_PASSWORD')
                    return existing_cid, f"mongodb://{db_user}:{password}@{hostname}:{port}/app_db?authSource=admin"

                if addon_type == 'RABBITMQ':
                    user = self._get_container_env(container_name, 'RABBITMQ_DEFAULT_USER')
                    password = self._get_container_env(container_name, 'RABBITMQ_DEFAULT_PASS')
                    return existing_cid, f"amqp://{user}:{password}@{hostname}:{port}//"

                if addon_type == 'REDIS':
                    result = subprocess.run(
                        ['docker', 'inspect', '-f', '{{json .Config.Cmd}}', container_name],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode == 0 and (result.stdout or '').strip():
                        import json
                        cmd = json.loads(result.stdout)
                        if isinstance(cmd, list) and '--requirepass' in cmd:
                            idx = cmd.index('--requirepass')
                            if idx + 1 < len(cmd) and cmd[idx + 1]:
                                password = cmd[idx + 1]
                                return existing_cid, f"redis://:{password}@{hostname}:{port}/0"

                if addon_type in ('QDRANT', 'ELASTICSEARCH'):
                    return existing_cid, f"http://{hostname}:{port}"

                if generic_config:
                    scheme = generic_config.get('scheme', addon_type.lower())
                    user = 'admin'
                    db = 'app_db'
                    password = ''
                    if generic_config.get('auth'):
                        # attempt to fetch password
                        pass_env = generic_config.get('pass_env') or generic_config.get('root_pass_env')
                        if pass_env:
                            password = self._get_container_env(container_name, pass_env)
                        if generic_config.get('user_env'):
                            user = self._get_container_env(container_name, generic_config['user_env'])
                        if generic_config.get('db_env'):
                            db = self._get_container_env(container_name, generic_config['db_env'])

                        if generic_config.get('user_env'):
                            return existing_cid, f"{scheme}://{user}:{password}@{hostname}:{port}/{db}"
                        else:
                            return existing_cid, f"{scheme}://:{password}@{hostname}:{port}"
                    else:
                        return existing_cid, f"{scheme}://{hostname}:{port}"

            except Exception as exc:
                logger.warning("Failed to reconstruct addon URL for %s: %s", container_name, exc)

            raise RuntimeError(f"Addon container exists but connection_url is missing: {container_name}")

        public_domain = getattr(addon, 'public_domain', None)

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
                container_id, _ = self._provision_generic(addon_type, container_name, password, port, hostname, generic_config, username=username, db_name=db_name, public_domain=public_domain)
                return container_id, existing_url

            if addon_type == 'MINIO':
                container_id, _ = self._provision_minio(container_name, password, port, hostname, username=username, public_domain=public_domain)
                return container_id, existing_url

            if addon_type == 'POSTGRES':
                container_id, _ = self._provision_postgres(
                    container_name,
                    password,
                    port,
                    hostname,
                    db_user=username or None,
                    db_name=db_name or None,
                    public_domain=public_domain,
                )
                return container_id, existing_url

            if addon_type == 'REDIS':
                container_id, _ = self._provision_redis(container_name, password, port, hostname, public_domain=public_domain)
                return container_id, existing_url

            if addon_type == 'MYSQL':
                container_id, _ = self._provision_mysql(container_name, password, port, hostname, public_domain=public_domain)
                return container_id, existing_url

            if addon_type == 'MONGODB':
                container_id, _ = self._provision_mongodb(container_name, password, port, hostname, public_domain=public_domain)
                return container_id, existing_url

            if addon_type == 'QDRANT':
                container_id, _ = self._provision_qdrant(container_name, port, hostname, public_domain=public_domain)
                return container_id, existing_url

            if addon_type == 'ELASTICSEARCH':
                container_id, _ = self._provision_elasticsearch(container_name, port, hostname, public_domain=public_domain)
                return container_id, existing_url

            if addon_type == 'RABBITMQ':
                container_id, _ = self._provision_rabbitmq(container_name, password, port, hostname, public_domain=public_domain)
                return container_id, existing_url

            raise ValueError(f"Unsupported addon type: {addon_type}")

        # First-time provisioning: generate fresh credentials for passworded addons.
        is_passworded = addon_type in (
            'POSTGRES', 'REDIS', 'MYSQL', 'MONGODB', 'RABBITMQ', 'MINIO'
        )
        if generic_config and generic_config.get('auth'):
            is_passworded = True

        password = secrets.token_urlsafe(24) if is_passworded else ''

        if generic_config:
            container_id, connection_url = self._provision_generic(addon_type, container_name, password, port, alias_name, generic_config, public_domain=public_domain)
        elif addon_type == 'MINIO':
            # Minio needs a username too, we can auto-generate one or use a default like 'admin'
            username = secrets.token_hex(8)
            container_id, connection_url = self._provision_minio(container_name, password, port, alias_name, username=username, public_domain=public_domain)
        elif addon_type == 'POSTGRES':
            container_id, connection_url = self._provision_postgres(container_name, password, port, alias_name, public_domain=public_domain)
        elif addon_type == 'REDIS':
            container_id, connection_url = self._provision_redis(container_name, password, port, alias_name, public_domain=public_domain)
        elif addon_type == 'MYSQL':
            container_id, connection_url = self._provision_mysql(container_name, password, port, alias_name, public_domain=public_domain)
        elif addon_type == 'MONGODB':
            container_id, connection_url = self._provision_mongodb(container_name, password, port, alias_name, public_domain=public_domain)
        elif addon_type == 'QDRANT':
            container_id, connection_url = self._provision_qdrant(container_name, port, alias_name, public_domain=public_domain)
        elif addon_type == 'ELASTICSEARCH':
            container_id, connection_url = self._provision_elasticsearch(container_name, port, alias_name, public_domain=public_domain)
        elif addon_type == 'RABBITMQ':
            container_id, connection_url = self._provision_rabbitmq(container_name, password, port, alias_name, public_domain=public_domain)
        else:
            raise ValueError(f"Unsupported addon type: {addon_type}")

        logger.info(f"Addon {addon_type} provisioned: {container_name} (alias: {alias_name})")
        return container_id, connection_url

    def _provision_rabbitmq(self, container_name: str,
                            password: str, port: int,
                            alias_name: str = '', public_domain: str = None) -> Tuple[str, str]:
        """Provision a RabbitMQ container with management plugin enabled."""
        user = "appuser"
        vhost = "/"
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '-e', f'RABBITMQ_DEFAULT_USER={user}',
            '-e', f'RABBITMQ_DEFAULT_PASS={password}',
            '-e', f'RABBITMQ_DEFAULT_VHOST={vhost}',
            '-v', f'{container_name}-data:/var/lib/rabbitmq',
        ]
        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace('.', '-').replace('_', '-'), public_domain, 15672) # Expose Management port, not AMQP

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.append(self.ADDON_IMAGES['RABBITMQ'])

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        container_id = result.stdout.strip()[:12]

        hostname = alias_name or container_name
        connection_url = f"amqp://{user}:{password}@{hostname}:{port}//"

        self._wait_for_health(container_name, port, path="/api/health/checks/alarms", use_http=True)
        return container_id, connection_url

    def _provision_minio(self, container_name: str,
                         password: str, port: int,
                         alias_name: str = '', username: str = 'admin', public_domain: str = None) -> Tuple[str, str]:
        """Provision a MinIO container."""
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '-e', f'MINIO_ROOT_USER={username}',
            '-e', f'MINIO_ROOT_PASSWORD={password}',
            '-v', f'{container_name}-data:/data',
        ]
        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, 9001) # Expose Web Console, not API

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.extend([
            self.ADDON_IMAGES['MINIO'],
            'server', '/data', '--console-address', ':9001'
        ])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True)
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
            # First, configure the alias
            subprocess.run([
                'docker', 'exec', container_name,
                'mc', 'alias', 'set', 'myminio', f'http://127.0.0.1:{port}', username, password
            ], capture_output=True, check=True)

            # Then, create the bucket
            subprocess.run([
                'docker', 'exec', container_name,
                'mc', 'mb', f'myminio/{bucket_name}'
            ], capture_output=True, check=False) # check=False because it might already exist on re-provision
        except Exception as e:
            logger.warning("Failed to auto-create default MinIO bucket %s: %s", bucket_name, e)

        return container_id, connection_url

    def _provision_generic(self, addon_type: str, container_name: str,
                           password: str, port: int, alias_name: str, config: dict,
                           username: str = '', db_name: str = '', public_domain: str = None) -> Tuple[str, str]:
        """Provision a generic addon from GENERIC_ADDONS_CONFIG."""
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '-v', f'{container_name}-data:/data'
        ]

        if public_domain:
            # Use dashboard port if explicitly defined for this addon, otherwise default API port
            target_port = config.get('dashboard_port', port)
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, target_port)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])

        # Add dynamic environment variables
        hostname = alias_name or container_name
        user = username or 'admin'
        db = db_name or 'app_db'

        if config.get('user_env'):
            cmd.extend(['-e', f'{config["user_env"]}={user}'])
        if config.get('pass_env'):
            cmd.extend(['-e', f'{config["pass_env"]}={password}'])
        if config.get('root_pass_env'):
            cmd.extend(['-e', f'{config["root_pass_env"]}={password}'])
        if config.get('db_env'):
            cmd.extend(['-e', f'{config["db_env"]}={db}'])

        env_extra = config.get('env', {})
        for k, v in env_extra.items():
            # Format custom variables if they need password/hostname injection
            val = v.replace('{password}', password).replace('{hostname}', hostname)
            cmd.extend(['-e', f'{k}={val}'])

        cmd.append(config['image'])

        if config.get('command'):
            cmd_args = [arg.replace('{password}', password).replace('{hostname}', hostname) for arg in config['command']]
            cmd.extend(cmd_args)

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        container_id = result.stdout.strip()[:12]

        # Build connection URL
        scheme = config.get('scheme', addon_type.lower())
        if config.get('auth'):
            if config.get('user_env'):
                connection_url = f"{scheme}://{user}:{password}@{hostname}:{port}/{db}"
            else:
                connection_url = f"{scheme}://:{password}@{hostname}:{port}"
        else:
            connection_url = f"{scheme}://{hostname}:{port}"

        self._wait_for_health(container_name, port)
        return container_id, connection_url

    def _provision_postgres(
        self,
        container_name: str,
        password: str,
        port: int,
        alias_name: str = '',
        db_user: Optional[str] = None,
        db_name: Optional[str] = None,
        public_domain: str = None,
    ) -> Tuple[str, str]:
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

        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '-e', f'POSTGRES_PASSWORD={password}',
            '-e', f'POSTGRES_USER={db_user}',
            '-e', f'POSTGRES_DB={db_name}',
            '-v', f'{container_name}-data:/var/lib/postgresql/data',
        ]

        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, port)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.append(self.ADDON_IMAGES['POSTGRES'])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True)
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
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("pgvector extension init failed for %s: %s", container_name, exc)

        return container_id, connection_url

    def _provision_redis(self, container_name: str,
                         password: str, port: int,
                         alias_name: str = '', public_domain: str = None) -> Tuple[str, str]:
        """Provision a Redis container with authentication."""
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '-v', f'{container_name}-data:/data',
        ]
        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, port)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.extend([
            self.ADDON_IMAGES['REDIS'],
            'redis-server', '--requirepass', password, '--appendonly', 'yes'
        ])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True)
        container_id = result.stdout.strip()[:12]

        hostname = alias_name or container_name
        connection_url = f"redis://:{password}@{hostname}:{port}/0"

        self._wait_for_health(container_name, port)
        return container_id, connection_url

    def _provision_mysql(self, container_name: str,
                         password: str, port: int,
                         alias_name: str = '', public_domain: str = None) -> Tuple[str, str]:
        """Provision a MySQL container."""
        db_name = "app_db"
        db_user = "app_user"

        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '-e', f'MYSQL_ROOT_PASSWORD={password}',
            '-e', f'MYSQL_DATABASE={db_name}',
            '-e', f'MYSQL_USER={db_user}',
            '-e', f'MYSQL_PASSWORD={password}',
            '-v', f'{container_name}-data:/var/lib/mysql',
        ]
        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, port)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.append(self.ADDON_IMAGES['MYSQL'])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True)
        container_id = result.stdout.strip()[:12]

        connection_url = f"mysql://{db_user}:{password}@{container_name}:{port}/{db_name}"

        self._wait_for_health(
            container_name,
            port,
            timeout=60)  # MySQL takes longer
        return container_id, connection_url

    def _provision_mongodb(self, container_name: str,
                           password: str, port: int,
                           alias_name: str = '', public_domain: str = None) -> Tuple[str, str]:
        """Provision a MongoDB container."""
        db_user = "app_user"
        db_name = "app_db"

        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '-e', f'MONGO_INITDB_ROOT_USERNAME={db_user}',
            '-e', f'MONGO_INITDB_ROOT_PASSWORD={password}',
            '-v', f'{container_name}-data:/data/db',
        ]
        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, port)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.append(self.ADDON_IMAGES['MONGODB'])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True)
        container_id = result.stdout.strip()[:12]

        connection_url = f"mongodb://{db_user}:{password}@{container_name}:{port}/{db_name}?authSource=admin"  # pylint: disable=line-too-long

        self._wait_for_health(container_name, port)
        return container_id, connection_url

    def _provision_qdrant(self, container_name: str,
                          port: int,
                          alias_name: str = '', public_domain: str = None) -> Tuple[str, str]:
        """Provision a Qdrant vector database container."""
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '-e', 'QDRANT__SERVICE__GRPC_PORT=6334',
            '-v', f'{container_name}-data:/qdrant/storage',
        ]
        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, port)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.append(self.ADDON_IMAGES['QDRANT'])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True)
        container_id = result.stdout.strip()[:12]

        # Qdrant uses HTTP API — no auth by default
        connection_url = f"http://{container_name}:{port}"

        self._wait_for_health(container_name, port)
        return container_id, connection_url

    def _provision_elasticsearch(self, container_name: str,
                                 port: int,
                                 alias_name: str = '', public_domain: str = None) -> Tuple[str, str]:
        """Provision a single-node Elasticsearch container."""
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '-e', 'discovery.type=single-node',
            '-e', 'xpack.security.enabled=false',
            '-e', 'ES_JAVA_OPTS=-Xms256m -Xmx256m',
            '-v', f'{container_name}-data:/usr/share/elasticsearch/data',
        ]
        if public_domain:
            self._append_traefik_labels(cmd, container_name.replace(".", "-").replace("_", "-"), public_domain, port)

        if alias_name:
            cmd.extend(['--network-alias', alias_name])
        cmd.append(self.ADDON_IMAGES['ELASTICSEARCH'])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True)
        container_id = result.stdout.strip()[:12]

        connection_url = f"http://{container_name}:{port}"

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
                    text=True
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
                except Exception:
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
                    container_name: Optional[str] = None) -> bool:
        """
        Remove an addon container and its volumes.

        Args:
            container_id: Container ID or name
            container_name: Optional container name for volume cleanup
        """
        try:
            # Stop and remove container
            subprocess.run(['docker', 'stop', container_id],
                           capture_output=True)
            subprocess.run(['docker', 'rm', container_id], capture_output=True)

            # Remove associated volume if container_name provided
            if container_name:
                subprocess.run(
                    ['docker', 'volume', 'rm', f'{container_name}-data'],
                    capture_output=True
                )

            logger.info(f"Deprovisioned addon container: {container_id}")
            return True
        except Exception as e:
            logger.error(
                f"Failed to deprovision container {container_id}: {e}")
            return False

    def get_status(self, container_id: str) -> Dict:
        """Get the status of an addon container."""
        try:
            result = subprocess.run(
                ['docker', 'inspect', container_id],
                capture_output=True,
                text=True
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


    def _validate_backup_path(self, backup_path: str) -> str:
        """
        Validate backup path to prevent path traversal and unsafe restore inputs.
        """
        import os

        if not backup_path:
            raise ValueError("backup_path is required")

        real_path = os.path.realpath(backup_path)
        allowed_root = os.path.realpath(os.path.join("/tmp", "backups"))

        if not real_path.startswith(allowed_root + os.sep):
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
        backup_dir = os.path.join("/tmp", "backups", str(addon.service.id))
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
                    )

            elif addon.addon_type == 'REDIS':
                # Redis save and copy using argument lists only.
                subprocess.run(['docker', 'exec', container_name, 'redis-cli', 'save'], check=True)
                subprocess.run(['docker', 'cp', f'{container_name}:/data/dump.rdb', backup_path], check=True)

            elif addon.addon_type == 'MYSQL':
                mysql_password = self._get_container_env(
                    container_name, 'MYSQL_ROOT_PASSWORD'
                )

                with open(backup_path, 'wb') as backup_file:
                    subprocess.run(
                        ['docker', 'exec', container_name, 'mysqldump', '-u', 'root', f'--password={mysql_password}', 'app_db'],
                        check=True,
                        stdout=backup_file,
                    )

            elif addon.addon_type == 'MONGODB':
                mongo_password = self._get_container_env(
                    container_name, 'MONGO_INITDB_ROOT_PASSWORD'
                )

                with open(backup_path, 'wb') as backup_file:
                    subprocess.run(
                        ['docker', 'exec', container_name, 'mongodump', '--username=app_user', f'--password={mongo_password}', '--db=app_db', '--archive'],
                        check=True,
                        stdout=backup_file,
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
                    )

            elif addon.addon_type == 'REDIS':
                # Copy file back, restart
                subprocess.run(['docker', 'stop', container_name], check=True)
                subprocess.run(['docker', 'cp', validated_backup_path, f'{container_name}:/data/dump.rdb'], check=True)
                subprocess.run(['docker', 'start', container_name], check=True)

            elif addon.addon_type == 'MYSQL':
                mysql_password = self._get_container_env(
                    container_name, 'MYSQL_ROOT_PASSWORD'
                )
                with open(validated_backup_path, 'rb') as backup_file:
                    subprocess.run(
                        ['docker', 'exec', '-i', container_name, 'mysql', '-u', 'root', f'--password={mysql_password}', 'app_db'],
                        stdin=backup_file,
                        check=True,
                    )

            elif addon.addon_type == 'MONGODB':
                mongo_password = self._get_container_env(
                    container_name, 'MONGO_INITDB_ROOT_PASSWORD'
                )
                with open(validated_backup_path, 'rb') as backup_file:
                    subprocess.run(
                        ['docker', 'exec', '-i', container_name, 'mongorestore', '--username=app_user', f'--password={mongo_password}', '--db=app_db', '--archive'],
                        stdin=backup_file,
                        check=True,
                    )
            else:
                logger.warning(f"Native restore not implemented for {addon.addon_type}, skipping gracefully.")
            
            return True
        except Exception as e:
            logger.error(f"Restore failed for {addon.id}: {e}")
            raise e

# Singleton instance
addon_provisioner = AddonProvisioner()
