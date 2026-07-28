"""Models Addons module."""
import logging
import uuid

from django.db import models
from encrypted_model_fields.fields import EncryptedCharField

from .core import Service, TimeStampedModel

logger = logging.getLogger(__name__)


class Addon(TimeStampedModel):
    class Type(models.TextChoices):
        # ── Relational Databases ──
        POSTGRES = 'POSTGRES', 'PostgreSQL'
        MYSQL = 'MYSQL', 'MySQL'
        MARIADB = 'MARIADB', 'MariaDB'
        COCKROACHDB = 'COCKROACHDB', 'CockroachDB'
        TIMESCALEDB = 'TIMESCALEDB', 'TimescaleDB'
        PERCONA = 'PERCONA', 'Percona Server'
        VITESS = 'VITESS', 'Vitess'
        # ── Document Databases ──
        MONGODB = 'MONGODB', 'MongoDB'
        COUCHDB = 'COUCHDB', 'CouchDB'
        RETHINKDB = 'RETHINKDB', 'RethinkDB'
        ARANGODB = 'ARANGODB', 'ArangoDB'
        FERRETDB = 'FERRETDB', 'FerretDB'
        SURREALDB = 'SURREALDB', 'SurrealDB'
        # ── Key-Value Stores ──
        REDIS = 'REDIS', 'Redis'
        MEMCACHED = 'MEMCACHED', 'Memcached'
        KEYDB = 'KEYDB', 'KeyDB'
        VALKEY = 'VALKEY', 'Valkey'
        DRAGONFLYDB = 'DRAGONFLYDB', 'DragonflyDB'
        ETCD = 'ETCD', 'etcd'
        # ── Column / Wide-Column ──
        CLICKHOUSE = 'CLICKHOUSE', 'ClickHouse'
        CASSANDRA = 'CASSANDRA', 'Cassandra'
        SCYLLADB = 'SCYLLADB', 'ScyllaDB'
        # ── Graph Databases ──
        NEO4J = 'NEO4J', 'Neo4j'
        DGRAPH = 'DGRAPH', 'Dgraph'
        # ── Vector Databases (AI) ──
        QDRANT = 'QDRANT', 'Qdrant'
        WEAVIATE = 'WEAVIATE', 'Weaviate'
        MILVUS = 'MILVUS', 'Milvus'
        CHROMADB = 'CHROMADB', 'ChromaDB'
        # ── Search Engines ──
        ELASTICSEARCH = 'ELASTICSEARCH', 'Elasticsearch'
        OPENSEARCH = 'OPENSEARCH', 'OpenSearch'
        MEILISEARCH = 'MEILISEARCH', 'MeiliSearch'
        TYPESENSE = 'TYPESENSE', 'Typesense'
        SOLR = 'SOLR', 'Apache Solr'
        # ── Message Queues / Streaming ──
        RABBITMQ = 'RABBITMQ', 'RabbitMQ'
        KAFKA = 'KAFKA', 'Apache Kafka'
        NATS = 'NATS', 'NATS'
        REDPANDA = 'REDPANDA', 'Redpanda'
        PULSAR = 'PULSAR', 'Apache Pulsar'
        ACTIVEMQ = 'ACTIVEMQ', 'ActiveMQ'
        # ── Object Storage ──
        MINIO = 'MINIO', 'MinIO'
        SEAWEEDFS = 'SEAWEEDFS', 'SeaweedFS'
        # ── Time-Series ──
        INFLUXDB = 'INFLUXDB', 'InfluxDB'
        QUESTDB = 'QUESTDB', 'QuestDB'
        VICTORIAMETRICS = 'VICTORIAMETRICS', 'VictoriaMetrics'
        # ── Monitoring / Observability ──
        PROMETHEUS = 'PROMETHEUS', 'Prometheus'
        GRAFANA = 'GRAFANA', 'Grafana'
        JAEGER = 'JAEGER', 'Jaeger'
        # ── Workflow / Infrastructure ──
        N8N = 'N8N', 'n8n'
        TEMPORAL = 'TEMPORAL', 'Temporal'
        VAULT = 'VAULT', 'HashiCorp Vault'
        CONSUL = 'CONSUL', 'Consul'
        KEYCLOAK = 'KEYCLOAK', 'Keycloak'

    class Status(models.TextChoices):
        PROVISIONING = 'PROVISIONING', 'Provisioning'
        ACTIVE = 'ACTIVE', 'Active'
        FAILED = 'FAILED', 'Failed'
        DELETED = 'DELETED', 'Deleted'
        DELETION_PENDING = 'DELETION_PENDING', 'Deletion Pending'
        DELETION_FAILED = 'DELETION_FAILED', 'Deletion Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    project = models.ForeignKey(  # type: ignore[var-annotated]
        'deployments.Project',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='project_addons',
        help_text="Project this addon belongs to (null = ungrouped)"
    )
    service = models.ForeignKey(  # type: ignore[var-annotated]
        Service,
        on_delete=models.CASCADE,
        related_name='addons')
    name = models.CharField(max_length=255)  # type: ignore[var-annotated]
    addon_type = models.CharField(max_length=20, choices=Type.choices)  # type: ignore[var-annotated]
    is_bucket_public = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="Whether the default bucket is public read-only."
    )
    status = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=Status.choices,
        default=Status.PROVISIONING)
    deletion_error = models.TextField(blank=True, default='')  # type: ignore[var-annotated]
    connection_url = EncryptedCharField(
        max_length=512, blank=True)  # H-1 fix: encrypted at rest

    # Coolify Integration
    coolify_uuid = models.CharField(max_length=64, blank=True, null=True,  # type: ignore[var-annotated]
                                    help_text="UUID of the database in Coolify")

    # Public Routing
    public_domain = models.CharField(max_length=255, blank=True, null=True, unique=True,  # type: ignore[var-annotated]
                                     help_text="Public domain assigned to expose this addon externally via proxy")

    @property
    def parsed_credentials(self) -> dict:
        """Parse connection_url into individual credential components."""
        from urllib.parse import urlparse
        if not self.connection_url:
            return {}
        parsed = urlparse(self.connection_url)
        slug = self.name.upper().replace('-', '_').replace(' ', '_')
        result = {
            f'{slug}_URL': self.connection_url,
        }
        if parsed.hostname:
            result[f'{slug}_HOST'] = parsed.hostname
        if parsed.port:
            result[f'{slug}_PORT'] = str(parsed.port)
        if parsed.username:
            result[f'{slug}_USER'] = parsed.username
        if parsed.password:
            result[f'{slug}_PASSWORD'] = parsed.password
        if parsed.path and parsed.path != '/':
            result[f'{slug}_DATABASE'] = parsed.path.lstrip('/')

        # Check if generic config has an implicit username not in the URL (like admin or root)
        try:
            from apps.addons.services.addon_provisioner import AddonProvisioner
            generic_config = AddonProvisioner.GENERIC_ADDONS_CONFIG.get(self.addon_type)
            if generic_config and generic_config.get('auth'):
                # Many generic configs set the password but default the user to 'admin' internally
                # or don't put it in the URL if user_env is absent.
                if not parsed.username:
                    if self.addon_type in ('GRAFANA', 'N8N', 'KEYCLOAK', 'INFLUXDB', 'ACTIVEMQ'):
                        result[f'{slug}_USER'] = 'admin'
                    elif self.addon_type in ('SURREALDB', 'ARANGODB'):
                        result[f'{slug}_USER'] = 'root'
        except (ValueError, TypeError) as exc:
            logger.debug("Failed to parse addon connection URL: %s", exc)

        # Addon specific custom mappings
        if self.addon_type == self.Type.MINIO:
            if parsed.hostname and parsed.port:
                result['MINIO_ENDPOINT'] = f"{parsed.hostname}:{parsed.port}"
                result['MINIO_URL'] = f"http://{parsed.hostname}:{parsed.port}"
            if parsed.username:
                result['MINIO_ACCESS_KEY'] = parsed.username
            if parsed.password:
                result['MINIO_SECRET_KEY'] = parsed.password
            if parsed.path and parsed.path != '/':
                result['MINIO_BUCKET'] = parsed.path.lstrip('/')

        elif self.addon_type == self.Type.POSTGRES:
            result['POSTGRES_URL'] = self.connection_url
            if parsed.username:
                result['POSTGRES_USER'] = parsed.username
            if parsed.password:
                result['POSTGRES_PASSWORD'] = parsed.password
            if parsed.path and parsed.path != '/':
                result['POSTGRES_DB'] = parsed.path.lstrip('/')
            if parsed.hostname:
                result['POSTGRES_HOST'] = parsed.hostname
            if parsed.port:
                result['POSTGRES_PORT'] = str(parsed.port)

        elif self.addon_type == self.Type.MYSQL:
            result['MYSQL_URL'] = self.connection_url
            if parsed.username:
                result['MYSQL_USER'] = parsed.username
            if parsed.password:
                result['MYSQL_PASSWORD'] = parsed.password
            if parsed.path and parsed.path != '/':
                result['MYSQL_DATABASE'] = parsed.path.lstrip('/')
            if parsed.hostname:
                result['MYSQL_HOST'] = parsed.hostname
            if parsed.port:
                result['MYSQL_PORT'] = str(parsed.port)

        elif self.addon_type == self.Type.REDIS:
            result['REDIS_URL'] = self.connection_url
            if parsed.password:
                result['REDIS_PASSWORD'] = parsed.password
            if parsed.hostname:
                result['REDIS_HOST'] = parsed.hostname
            if parsed.port:
                result['REDIS_PORT'] = str(parsed.port)

        elif self.addon_type == self.Type.MONGODB:
            result['MONGODB_URI'] = self.connection_url
            if parsed.username:
                result['MONGO_INITDB_ROOT_USERNAME'] = parsed.username
            if parsed.password:
                result['MONGO_INITDB_ROOT_PASSWORD'] = parsed.password

        elif self.addon_type == self.Type.RABBITMQ:
            result['RABBITMQ_URL'] = self.connection_url
            if parsed.username:
                result['RABBITMQ_DEFAULT_USER'] = parsed.username
            if parsed.password:
                result['RABBITMQ_DEFAULT_PASS'] = parsed.password

        elif self.addon_type == self.Type.ELASTICSEARCH:
            result['ELASTICSEARCH_URL'] = self.connection_url
            if parsed.password:
                result['ELASTIC_PASSWORD'] = parsed.password

        elif self.addon_type == self.Type.QDRANT:
            result['QDRANT_URL'] = self.connection_url
            if parsed.hostname:
                result['QDRANT_HOST'] = parsed.hostname
            if parsed.port:
                result['QDRANT_PORT'] = str(parsed.port)

        return result

    def __str__(self):
        return f"{self.addon_type} for {self.service.name}"

    class Meta:
        indexes = [
            models.Index(fields=["service", "status"], name="addon_service_status_idx"),
        ]


class Backup(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    addon = models.ForeignKey(  # type: ignore[var-annotated]
        Addon,
        on_delete=models.CASCADE,
        related_name='backups')

    file_path = models.CharField(max_length=512, blank=True)  # type: ignore[var-annotated]
    size_bytes = models.BigIntegerField(default=0)  # type: ignore[var-annotated]

    status = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING)

    completed_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    error_message = models.TextField(blank=True)  # type: ignore[var-annotated]

    def __str__(self):
        return f"Backup {self.id} ({self.status})"
