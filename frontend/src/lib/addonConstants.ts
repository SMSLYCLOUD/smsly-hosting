export const ADDON_TYPES = [
    // ── Relational Databases ──
    { value: 'POSTGRES', label: 'PostgreSQL', logo: '/logos/addons/postgres.svg', color: 'text-blue-400', description: 'Relational database', has_dashboard: false },
    { value: 'MYSQL', label: 'MySQL', logo: '/logos/addons/mysql.svg', color: 'text-cyan-400', description: 'Relational database', has_dashboard: false },
    { value: 'MARIADB', label: 'MariaDB', icon: '🦭', color: 'text-teal-400', description: 'MySQL-compatible DB', has_dashboard: false },
    { value: 'COCKROACHDB', label: 'CockroachDB', icon: '🪳', color: 'text-indigo-400', description: 'Distributed SQL', has_dashboard: true },
    { value: 'TIMESCALEDB', label: 'TimescaleDB', icon: '⏱️', color: 'text-amber-500', description: 'Time-series SQL', has_dashboard: false },
    { value: 'PERCONA', label: 'Percona', icon: '🔷', color: 'text-sky-400', description: 'MySQL/MongoDB server', has_dashboard: false },
    { value: 'VITESS', label: 'Vitess', icon: '🌐', color: 'text-lime-400', description: 'MySQL sharding', has_dashboard: true },
    // ── Document Databases ──
    { value: 'MONGODB', label: 'MongoDB', logo: '/logos/addons/mongodb.svg', color: 'text-green-400', description: 'Document database', has_dashboard: false },
    { value: 'COUCHDB', label: 'CouchDB', icon: '🛋️', color: 'text-red-300', description: 'Document database', has_dashboard: false },
    { value: 'RETHINKDB', label: 'RethinkDB', icon: '💭', color: 'text-green-300', description: 'Realtime document DB', has_dashboard: true },
    { value: 'ARANGODB', label: 'ArangoDB', icon: '🥑', color: 'text-emerald-400', description: 'Multi-model database', has_dashboard: false },
    { value: 'FERRETDB', label: 'FerretDB', icon: '🦦', color: 'text-orange-300', description: 'MongoDB alternative', has_dashboard: false },
    { value: 'SURREALDB', label: 'SurrealDB', icon: '🌀', color: 'text-fuchsia-400', description: 'Multi-model cloud DB', has_dashboard: false },
    // ── Key-Value Stores ──
    { value: 'REDIS', label: 'Redis', logo: '/logos/addons/redis.svg', color: 'text-red-400', description: 'In-memory cache & store', has_dashboard: false },
    { value: 'MEMCACHED', label: 'Memcached', icon: '⚡', color: 'text-purple-400', description: 'Distributed cache', has_dashboard: false },
    { value: 'KEYDB', label: 'KeyDB', icon: '🔑', color: 'text-yellow-300', description: 'Multi-threaded Redis fork', has_dashboard: false },
    { value: 'VALKEY', label: 'Valkey', icon: '🔓', color: 'text-blue-300', description: 'Redis-compatible store', has_dashboard: false },
    { value: 'DRAGONFLYDB', label: 'DragonflyDB', icon: '🐉', color: 'text-red-500', description: 'Ultra-fast cache', has_dashboard: false },
    { value: 'ETCD', label: 'etcd', icon: '🗂️', color: 'text-cyan-300', description: 'Distributed KV store', has_dashboard: false },
    // ── Column / Wide-Column ──
    { value: 'CLICKHOUSE', label: 'ClickHouse', icon: '📊', color: 'text-amber-400', description: 'Analytics database', has_dashboard: false },
    { value: 'CASSANDRA', label: 'Cassandra', icon: '👁️', color: 'text-sky-300', description: 'Wide-column store', has_dashboard: false },
    { value: 'SCYLLADB', label: 'ScyllaDB', icon: '🐙', color: 'text-violet-500', description: 'High-perf Cassandra', has_dashboard: false },
    // ── Graph Databases ──
    { value: 'NEO4J', label: 'Neo4j', icon: '🕸️', color: 'text-blue-500', description: 'Graph database', has_dashboard: true },
    { value: 'DGRAPH', label: 'Dgraph', icon: '📐', color: 'text-rose-400', description: 'Distributed graph DB', has_dashboard: false },
    // ── Vector Databases (AI) ──
    { value: 'QDRANT', label: 'Qdrant', logo: '/logos/addons/qdrant.svg', color: 'text-violet-400', description: 'Vector database (AI)', has_dashboard: true },
    { value: 'WEAVIATE', label: 'Weaviate', icon: '🧬', color: 'text-green-500', description: 'Vector search engine', has_dashboard: false },
    { value: 'MILVUS', label: 'Milvus', icon: '🧮', color: 'text-blue-400', description: 'Vector similarity DB', has_dashboard: false },
    { value: 'CHROMADB', label: 'ChromaDB', icon: '🎨', color: 'text-pink-400', description: 'AI embedding store', has_dashboard: false },
    // ── Search Engines ──
    { value: 'ELASTICSEARCH', label: 'Elasticsearch', logo: '/logos/addons/elasticsearch.svg', color: 'text-yellow-400', description: 'Search & analytics', has_dashboard: false },
    { value: 'OPENSEARCH', label: 'OpenSearch', icon: '🔍', color: 'text-blue-400', description: 'Open-source search', has_dashboard: false },
    { value: 'MEILISEARCH', label: 'MeiliSearch', icon: '🔎', color: 'text-purple-500', description: 'Instant search engine', has_dashboard: false },
    { value: 'TYPESENSE', label: 'Typesense', icon: '⌨️', color: 'text-cyan-400', description: 'Typo-tolerant search', has_dashboard: false },
    { value: 'SOLR', label: 'Apache Solr', icon: '☀️', color: 'text-orange-400', description: 'Enterprise search', has_dashboard: true },
    // ── Message Queues / Streaming ──
    { value: 'RABBITMQ', label: 'RabbitMQ', icon: '🐇', color: 'text-orange-400', description: 'Message broker', has_dashboard: true },
    { value: 'KAFKA', label: 'Apache Kafka', icon: '📨', color: 'text-slate-300', description: 'Event streaming', has_dashboard: false },
    { value: 'NATS', label: 'NATS', icon: '📡', color: 'text-green-400', description: 'Cloud messaging', has_dashboard: false },
    { value: 'REDPANDA', label: 'Redpanda', icon: '🐼', color: 'text-red-400', description: 'Kafka-compatible', has_dashboard: false },
    { value: 'PULSAR', label: 'Apache Pulsar', icon: '💫', color: 'text-indigo-400', description: 'Pub-sub messaging', has_dashboard: false },
    { value: 'ACTIVEMQ', label: 'ActiveMQ', icon: '📬', color: 'text-rose-300', description: 'Java message broker', has_dashboard: true },
    // ── Object Storage ──
    { value: 'MINIO', label: 'MinIO', logo: '/logos/addons/minio.svg', color: 'text-pink-400', description: 'S3-compatible storage', has_dashboard: true },
    { value: 'SEAWEEDFS', label: 'SeaweedFS', icon: '🌊', color: 'text-teal-300', description: 'Distributed storage', has_dashboard: false },
    // ── Time-Series ──
    { value: 'INFLUXDB', label: 'InfluxDB', icon: '📈', color: 'text-purple-400', description: 'Time-series database', has_dashboard: false },
    { value: 'QUESTDB', label: 'QuestDB', icon: '⏳', color: 'text-amber-300', description: 'Fast time-series DB', has_dashboard: true },
    { value: 'VICTORIAMETRICS', label: 'VictoriaMetrics', icon: '📉', color: 'text-sky-400', description: 'Metrics storage', has_dashboard: false },
    // ── Monitoring / Observability ──
    { value: 'PROMETHEUS', label: 'Prometheus', icon: '🔥', color: 'text-orange-500', description: 'Metrics & alerting', has_dashboard: true },
    { value: 'GRAFANA', label: 'Grafana', icon: '📊', color: 'text-orange-300', description: 'Dashboards & viz', has_dashboard: true },
    { value: 'JAEGER', label: 'Jaeger', icon: '🔭', color: 'text-cyan-400', description: 'Distributed tracing', has_dashboard: true },
    // ── Workflow / Infrastructure ──
    { value: 'N8N', label: 'n8n', icon: '🔄', color: 'text-rose-400', description: 'Workflow automation', has_dashboard: true },
    { value: 'TEMPORAL', label: 'Temporal', icon: '⏰', color: 'text-indigo-300', description: 'Workflow orchestration', has_dashboard: true },
    { value: 'VAULT', label: 'HashiCorp Vault', icon: '🔐', color: 'text-yellow-400', description: 'Secrets management', has_dashboard: true },
    { value: 'CONSUL', label: 'Consul', icon: '🏛️', color: 'text-pink-500', description: 'Service discovery', has_dashboard: true },
    { value: 'KEYCLOAK', label: 'Keycloak', icon: '🛡️', color: 'text-blue-500', description: 'Identity & access', has_dashboard: true },
];

export const DASHBOARD_ADDONS = ADDON_TYPES.filter(a => a.has_dashboard).map(a => a.value);
