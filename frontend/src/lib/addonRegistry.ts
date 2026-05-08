export interface AddonRegistryItem {
    id: string; // The slug like "postgres-16"
    addon_type: string; // The backend enum value like "POSTGRES"
    name: string;
    description: string;
    category: string;
    logo: string;
    color: string;
    has_dashboard: boolean;
}

export const ADDON_REGISTRY: AddonRegistryItem[] = [
    // ── Relational Databases ──
    { id: 'postgres-16', addon_type: 'POSTGRES', name: 'PostgreSQL 16', description: 'Latest stable Postgres with JSONB, full-text search, and pgvector support.', category: 'Database', logo: '/logos/addons/postgres.svg', color: 'text-blue-400', has_dashboard: false },
    { id: 'postgres-timescale', addon_type: 'TIMESCALEDB', name: 'TimescaleDB', description: 'Time-series extension for PostgreSQL. Ideal for IoT and analytics.', category: 'Database', logo: '/logos/addons/timescaledb.svg', color: 'text-amber-500', has_dashboard: false },
    { id: 'pgbouncer', addon_type: 'POSTGRES', name: 'PgBouncer', description: 'Lightweight connection pooler for PostgreSQL. Reduces connection overhead.', category: 'Database', logo: '/logos/addons/postgres.svg', color: 'text-blue-400', has_dashboard: false },
    { id: 'mysql-8', addon_type: 'MYSQL', name: 'MySQL 8.0', description: 'Reliable relational database with InnoDB and window functions.', category: 'Database', logo: '/logos/addons/mysql.svg', color: 'text-cyan-400', has_dashboard: false },
    { id: 'mariadb-11', addon_type: 'MARIADB', name: 'MariaDB 11', description: 'MySQL-compatible with columnar storage and enhanced performance.', category: 'Database', logo: '/logos/addons/mariadb.svg', color: 'text-teal-400', has_dashboard: false },
    { id: 'cockroachdb', addon_type: 'COCKROACHDB', name: 'CockroachDB', description: 'Distributed SQL database for global scale.', category: 'Database', logo: '/logos/addons/cockroachdb.svg', color: 'text-indigo-400', has_dashboard: true },
    { id: 'percona', addon_type: 'PERCONA', name: 'Percona', description: 'MySQL/MongoDB server', category: 'Database', logo: '/logos/addons/percona.svg', color: 'text-sky-400', has_dashboard: false },
    { id: 'vitess', addon_type: 'VITESS', name: 'Vitess', description: 'MySQL sharding', category: 'Database', logo: '/logos/addons/vitess.svg', color: 'text-lime-400', has_dashboard: true },

    // ── Document Databases ──
    { id: 'mongodb-7', addon_type: 'MONGODB', name: 'MongoDB 7', description: 'Document database with aggregation pipelines and change streams.', category: 'Database', logo: '/logos/addons/mongodb.svg', color: 'text-green-400', has_dashboard: false },
    { id: 'couchdb', addon_type: 'COUCHDB', name: 'CouchDB', description: 'Document database', category: 'Database', logo: '/logos/addons/couchdb.svg', color: 'text-red-300', has_dashboard: false },
    { id: 'rethinkdb', addon_type: 'RETHINKDB', name: 'RethinkDB', description: 'Realtime document DB', category: 'Database', logo: '/logos/addons/rethinkdb.svg', color: 'text-green-300', has_dashboard: true },
    { id: 'arangodb', addon_type: 'ARANGODB', name: 'ArangoDB', description: 'Multi-model database', category: 'Database', logo: '/logos/addons/arangodb.svg', color: 'text-emerald-400', has_dashboard: false },
    { id: 'ferretdb', addon_type: 'FERRETDB', name: 'FerretDB', description: 'MongoDB alternative', category: 'Database', logo: '/logos/addons/ferretdb.svg', color: 'text-orange-300', has_dashboard: false },
    { id: 'surrealdb', addon_type: 'SURREALDB', name: 'SurrealDB', description: 'Multi-model cloud DB', category: 'Database', logo: '/logos/addons/surrealdb.svg', color: 'text-fuchsia-400', has_dashboard: false },

    // ── Key-Value Stores ──
    { id: 'redis-7', addon_type: 'REDIS', name: 'Redis 7', description: 'In-memory data store for caching, sessions, and pub/sub.', category: 'Cache', logo: '/logos/addons/redis.svg', color: 'text-red-400', has_dashboard: false },
    { id: 'memcached', addon_type: 'MEMCACHED', name: 'Memcached', description: 'High-performance distributed memory cache. Simple key-value.', category: 'Cache', logo: '/logos/addons/memcached.svg', color: 'text-purple-400', has_dashboard: false },
    { id: 'keydb', addon_type: 'KEYDB', name: 'KeyDB', description: 'Multi-threaded Redis fork', category: 'Cache', logo: '/logos/addons/keydb.svg', color: 'text-yellow-300', has_dashboard: false },
    { id: 'valkey', addon_type: 'VALKEY', name: 'Valkey', description: 'Open-source Redis fork. Drop-in compatible, community-driven.', category: 'Cache', logo: '/logos/addons/valkey.svg', color: 'text-blue-300', has_dashboard: false },
    { id: 'dragonfly', addon_type: 'DRAGONFLYDB', name: 'Dragonfly', description: 'Modern in-memory store. 25x faster than Redis on a single node.', category: 'Cache', logo: '/logos/addons/dragonflydb.svg', color: 'text-red-500', has_dashboard: false },
    { id: 'etcd', addon_type: 'ETCD', name: 'etcd', description: 'Distributed KV store', category: 'Cache', logo: '/logos/addons/etcd.svg', color: 'text-cyan-300', has_dashboard: false },

    // ── Column / Wide-Column ──
    { id: 'clickhouse', addon_type: 'CLICKHOUSE', name: 'ClickHouse', description: 'Column-oriented OLAP database for real-time analytics at scale.', category: 'Analytics', logo: '/logos/addons/clickhouse.svg', color: 'text-amber-400', has_dashboard: false },
    { id: 'cassandra', addon_type: 'CASSANDRA', name: 'Cassandra', description: 'Distributed wide-column store. Massively scalable writes.', category: 'Database', logo: '/logos/addons/cassandra.svg', color: 'text-sky-300', has_dashboard: false },
    { id: 'scylladb', addon_type: 'SCYLLADB', name: 'ScyllaDB', description: 'High-perf Cassandra', category: 'Database', logo: '/logos/addons/scylladb.svg', color: 'text-violet-500', has_dashboard: false },

    // ── Graph Databases ──
    { id: 'neo4j', addon_type: 'NEO4J', name: 'Neo4j', description: 'Graph database for relationship-heavy data. Cypher query language.', category: 'Database', logo: '/logos/addons/neo4j.svg', color: 'text-blue-500', has_dashboard: true },
    { id: 'dgraph', addon_type: 'DGRAPH', name: 'Dgraph', description: 'Distributed graph DB', category: 'Database', logo: '/logos/addons/dgraph.svg', color: 'text-rose-400', has_dashboard: false },

    // ── Vector Databases (AI) ──
    { id: 'qdrant', addon_type: 'QDRANT', name: 'Qdrant', description: 'Vector database (AI)', category: 'AI', logo: '/logos/addons/qdrant.svg', color: 'text-violet-400', has_dashboard: true },
    { id: 'weaviate', addon_type: 'WEAVIATE', name: 'Weaviate', description: 'Vector search engine', category: 'AI', logo: '/logos/addons/weaviate.svg', color: 'text-green-500', has_dashboard: false },
    { id: 'milvus', addon_type: 'MILVUS', name: 'Milvus', description: 'Vector similarity DB', category: 'AI', logo: '/logos/addons/milvus.svg', color: 'text-blue-400', has_dashboard: false },
    { id: 'chromadb', addon_type: 'CHROMADB', name: 'ChromaDB', description: 'AI embedding store', category: 'AI', logo: '/logos/addons/chromadb.svg', color: 'text-pink-400', has_dashboard: false },

    // ── Search Engines ──
    { id: 'elasticsearch', addon_type: 'ELASTICSEARCH', name: 'Elasticsearch', description: 'Full-text search and analytics engine. Log aggregation and APM.', category: 'Search', logo: '/logos/addons/elasticsearch.svg', color: 'text-yellow-400', has_dashboard: false },
    { id: 'opensearch', addon_type: 'OPENSEARCH', name: 'OpenSearch', description: 'Open-source search', category: 'Search', logo: '/logos/addons/opensearch.svg', color: 'text-blue-400', has_dashboard: false },
    { id: 'meilisearch', addon_type: 'MEILISEARCH', name: 'Meilisearch', description: 'Lightning-fast, typo-tolerant search engine. Easy to set up.', category: 'Search', logo: '/logos/addons/meilisearch.svg', color: 'text-purple-500', has_dashboard: false },
    { id: 'typesense', addon_type: 'TYPESENSE', name: 'Typesense', description: 'Typo-tolerant search', category: 'Search', logo: '/logos/addons/typesense.svg', color: 'text-cyan-400', has_dashboard: false },
    { id: 'solr', addon_type: 'SOLR', name: 'Apache Solr', description: 'Enterprise search', category: 'Search', logo: '/logos/addons/solr.svg', color: 'text-orange-400', has_dashboard: true },

    // ── Message Queues / Streaming ──
    { id: 'rabbitmq', addon_type: 'RABBITMQ', name: 'RabbitMQ', description: 'Robust message broker with AMQP. Queues, routing, and dead-letter.', category: 'Queue', logo: '/logos/addons/rabbitmq.svg', color: 'text-orange-400', has_dashboard: true },
    { id: 'kafka', addon_type: 'KAFKA', name: 'Apache Kafka', description: 'Event streaming', category: 'Queue', logo: '/logos/addons/kafka.svg', color: 'text-slate-300', has_dashboard: false },
    { id: 'nats', addon_type: 'NATS', name: 'NATS', description: 'Lightweight, high-performance messaging for microservices.', category: 'Queue', logo: '/logos/addons/nats.svg', color: 'text-green-400', has_dashboard: false },
    { id: 'redpanda', addon_type: 'REDPANDA', name: 'Redpanda', description: 'Kafka-compatible', category: 'Queue', logo: '/logos/addons/redpanda.svg', color: 'text-red-400', has_dashboard: false },
    { id: 'pulsar', addon_type: 'PULSAR', name: 'Apache Pulsar', description: 'Pub-sub messaging', category: 'Queue', logo: '/logos/addons/pulsar.svg', color: 'text-indigo-400', has_dashboard: false },
    { id: 'activemq', addon_type: 'ACTIVEMQ', name: 'ActiveMQ', description: 'Java message broker', category: 'Queue', logo: '/logos/addons/activemq.svg', color: 'text-rose-300', has_dashboard: true },

    // ── Object Storage ──
    { id: 'minio', addon_type: 'MINIO', name: 'MinIO', description: 'S3-compatible object storage. Store blobs, backups, and assets.', category: 'Storage', logo: '/logos/addons/minio.svg', color: 'text-pink-400', has_dashboard: true },
    { id: 'seaweedfs', addon_type: 'SEAWEEDFS', name: 'SeaweedFS', description: 'Distributed storage', category: 'Storage', logo: '/logos/addons/seaweedfs.svg', color: 'text-teal-300', has_dashboard: false },

    // ── Time-Series ──
    { id: 'influxdb', addon_type: 'INFLUXDB', name: 'InfluxDB', description: 'Purpose-built time-series database for metrics and monitoring.', category: 'Time-Series', logo: '/logos/addons/influxdb.svg', color: 'text-purple-400', has_dashboard: false },
    { id: 'questdb', addon_type: 'QUESTDB', name: 'QuestDB', description: 'Fast time-series DB', category: 'Time-Series', logo: '/logos/addons/questdb.svg', color: 'text-amber-300', has_dashboard: true },
    { id: 'victoriametrics', addon_type: 'VICTORIAMETRICS', name: 'VictoriaMetrics', description: 'Metrics storage', category: 'Time-Series', logo: '/logos/addons/victoriametrics.svg', color: 'text-sky-400', has_dashboard: false },

    // ── Monitoring / Observability ──
    { id: 'prometheus', addon_type: 'PROMETHEUS', name: 'Prometheus', description: 'Metrics & alerting', category: 'Observability', logo: '/logos/addons/prometheus.svg', color: 'text-orange-500', has_dashboard: true },
    { id: 'grafana', addon_type: 'GRAFANA', name: 'Grafana', description: 'Dashboards & viz', category: 'Observability', logo: '/logos/addons/grafana.svg', color: 'text-orange-300', has_dashboard: true },
    { id: 'jaeger', addon_type: 'JAEGER', name: 'Jaeger', description: 'Distributed tracing', category: 'Observability', logo: '/logos/addons/jaeger.svg', color: 'text-cyan-400', has_dashboard: true },

    // ── Workflow / Infrastructure ──
    { id: 'n8n', addon_type: 'N8N', name: 'n8n', description: 'Workflow automation', category: 'Infrastructure', logo: '/logos/addons/n8n.svg', color: 'text-rose-400', has_dashboard: true },
    { id: 'temporal', addon_type: 'TEMPORAL', name: 'Temporal', description: 'Workflow orchestration', category: 'Infrastructure', logo: '/logos/addons/temporal.svg', color: 'text-indigo-300', has_dashboard: true },
    { id: 'vault', addon_type: 'VAULT', name: 'HashiCorp Vault', description: 'Secrets management', category: 'Infrastructure', logo: '/logos/addons/vault.svg', color: 'text-yellow-400', has_dashboard: true },
    { id: 'consul', addon_type: 'CONSUL', name: 'Consul', description: 'Service discovery', category: 'Infrastructure', logo: '/logos/addons/consul.svg', color: 'text-pink-500', has_dashboard: true },
    { id: 'keycloak', addon_type: 'KEYCLOAK', name: 'Keycloak', description: 'Identity & access', category: 'Infrastructure', logo: '/logos/addons/keycloak.svg', color: 'text-blue-500', has_dashboard: true },
];

export const getAddonMetadata = (addonType: string): AddonRegistryItem | undefined => {
    return ADDON_REGISTRY.find(a => a.addon_type === addonType);
};

export const getAddonMetadataById = (id: string): AddonRegistryItem | undefined => {
    return ADDON_REGISTRY.find(a => a.id === id);
};

export const DASHBOARD_ADDONS = ADDON_REGISTRY.filter(a => a.has_dashboard).map(a => a.addon_type);
