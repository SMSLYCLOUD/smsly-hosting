# Generated migration for:
# 1. ServerBackup.error_message (already exists on VPS from auto-generated 0034)
# 2. Addon.addon_type expanded choices (11 -> 50 types)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0033_merge_tunnel_and_blue_green"),
    ]

    operations = [
        # ServerBackup.error_message — safe: won't fail if column already exists
        # because Django's AddField with db_column checks existence.
        # However, VPS may already have this from an auto-generated migration.
        # We use RunSQL with IF NOT EXISTS for safety.
        migrations.RunSQL(
            sql="ALTER TABLE deployments_serverbackup ADD COLUMN IF NOT EXISTS error_message text DEFAULT '' NOT NULL;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        # Update addon_type choices (no DB change needed — choices are app-level only)
        migrations.AlterField(
            model_name='addon',
            name='addon_type',
            field=models.CharField(
                choices=[
                    ('POSTGRES', 'PostgreSQL'), ('MYSQL', 'MySQL'), ('MARIADB', 'MariaDB'),
                    ('COCKROACHDB', 'CockroachDB'), ('TIMESCALEDB', 'TimescaleDB'),
                    ('PERCONA', 'Percona Server'), ('VITESS', 'Vitess'),
                    ('MONGODB', 'MongoDB'), ('COUCHDB', 'CouchDB'), ('RETHINKDB', 'RethinkDB'),
                    ('ARANGODB', 'ArangoDB'), ('FERRETDB', 'FerretDB'), ('SURREALDB', 'SurrealDB'),
                    ('REDIS', 'Redis'), ('MEMCACHED', 'Memcached'), ('KEYDB', 'KeyDB'),
                    ('VALKEY', 'Valkey'), ('DRAGONFLYDB', 'DragonflyDB'), ('ETCD', 'etcd'),
                    ('CLICKHOUSE', 'ClickHouse'), ('CASSANDRA', 'Cassandra'), ('SCYLLADB', 'ScyllaDB'),
                    ('NEO4J', 'Neo4j'), ('DGRAPH', 'Dgraph'),
                    ('QDRANT', 'Qdrant'), ('WEAVIATE', 'Weaviate'), ('MILVUS', 'Milvus'), ('CHROMADB', 'ChromaDB'),
                    ('ELASTICSEARCH', 'Elasticsearch'), ('OPENSEARCH', 'OpenSearch'),
                    ('MEILISEARCH', 'MeiliSearch'), ('TYPESENSE', 'Typesense'), ('SOLR', 'Apache Solr'),
                    ('RABBITMQ', 'RabbitMQ'), ('KAFKA', 'Apache Kafka'), ('NATS', 'NATS'),
                    ('REDPANDA', 'Redpanda'), ('PULSAR', 'Apache Pulsar'), ('ACTIVEMQ', 'ActiveMQ'),
                    ('MINIO', 'MinIO'), ('SEAWEEDFS', 'SeaweedFS'),
                    ('INFLUXDB', 'InfluxDB'), ('QUESTDB', 'QuestDB'), ('VICTORIAMETRICS', 'VictoriaMetrics'),
                    ('PROMETHEUS', 'Prometheus'), ('GRAFANA', 'Grafana'), ('JAEGER', 'Jaeger'),
                    ('N8N', 'n8n'), ('TEMPORAL', 'Temporal'), ('VAULT', 'HashiCorp Vault'),
                    ('CONSUL', 'Consul'), ('KEYCLOAK', 'Keycloak'),
                ],
                max_length=20,
            ),
        ),
    ]
