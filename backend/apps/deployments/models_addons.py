"""Models Addons module."""
import uuid
from encrypted_model_fields.fields import EncryptedCharField
from django.db import models
from .models_core import Service, TimeStampedModel


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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        'deployments.Project',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='project_addons',
        help_text="Project this addon belongs to (null = ungrouped)"
    )
    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name='addons')
    name = models.CharField(max_length=255)
    addon_type = models.CharField(max_length=20, choices=Type.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROVISIONING)
    connection_url = EncryptedCharField(
        max_length=512, blank=True)  # H-1 fix: encrypted at rest

    # Coolify Integration
    coolify_uuid = models.CharField(max_length=64, blank=True, null=True,
                                    help_text="UUID of the database in Coolify")

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
        return result

    def __str__(self):
        return f"{self.addon_type} for {self.service.name}"


class Backup(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    addon = models.ForeignKey(
        Addon,
        on_delete=models.CASCADE,
        related_name='backups')
    
    file_path = models.CharField(max_length=512, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING)
    
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    def __str__(self):
        return f"Backup {self.id} ({self.status})"
