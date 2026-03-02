# Stable migration 0034
#
# Adds:
#   1. ServerBackup.error_message (may already exist in DB on VPS)
#   2. Expanded Addon.addon_type choices (11 -> 50)
#
# Uses SeparateDatabaseAndState for error_message so Django learns about the
# field in its state without trying to create the column (which already exists).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0033_merge_tunnel_and_blue_green"),
    ]

    operations = [
        # 1. error_message: DB column already exists (created by earlier auto-migration).
        #    We use SeparateDatabaseAndState so Django's state tracks the field
        #    but we don't touch the DB (avoiding "column already exists" error).
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='serverbackup',
                    name='error_message',
                    field=models.TextField(blank=True, default=''),
                ),
            ],
            database_operations=[
                # Column already exists; this is a no-op raw SQL that ensures
                # it exists even on a fresh DB (belt-and-suspenders).
                migrations.RunSQL(
                    sql="ALTER TABLE deployments_serverbackup ADD COLUMN IF NOT EXISTS error_message text DEFAULT '' NOT NULL;",
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
        # 2. Update addon_type choices (no DB change — choices are app-level only)
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
