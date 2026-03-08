# Stable migration 0034
#
# Adds:
#   1. ServerBackup.error_message (idempotent across existing/fresh DBs)
#   2. Expanded Addon.addon_type choices (11 -> 50)

from django.db import migrations, models


def _add_serverbackup_error_message_if_missing(apps, schema_editor):
    """
    Add deployments_serverbackup.error_message only when missing.

    This avoids duplicate-column failures on already-patched databases and
    avoids SQLite-incompatible SQL such as "ADD COLUMN IF NOT EXISTS".
    """
    ServerBackup = apps.get_model("deployments", "ServerBackup")
    table_name = ServerBackup._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        existing_columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(cursor, table_name)
        }

    if "error_message" in existing_columns:
        return

    field = models.TextField(blank=True, default="")
    field.set_attributes_from_name("error_message")
    schema_editor.add_field(ServerBackup, field)


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
                    model_name="serverbackup",
                    name="error_message",
                    field=models.TextField(blank=True, default=''),
                ),
            ],
            database_operations=[
                migrations.RunPython(
                    code=_add_serverbackup_error_message_if_missing,
                    reverse_code=migrations.RunPython.noop,
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
