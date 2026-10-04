/**
 * addonColors — single source of truth for addon brand colors in
 * topology views (2D canvas + 3D graph). Hex per addon_type; unknown
 * future types get a deterministic hash hue steered clear of the
 * service-blue band so they never masquerade as services.
 */

const BRAND_HEX: Record<string, string> = {
  POSTGRES: '#6366f1', TIMESCALEDB: '#fde047', PGBOUNCER: '#a5b4fc',
  MYSQL: '#f59e0b', MARIADB: '#2dd4bf', COCKROACHDB: '#818cf8',
  PERCONA: '#0ea5e9', VITESS: '#a3e635', MONGODB: '#22c55e',
  COUCHDB: '#fca5a5', RETHINKDB: '#fb7185', ARANGODB: '#f472b6',
  FERRETDB: '#fdba74', SURREALDB: '#e879f9', CASSANDRA: '#7dd3fc',
  SCYLLADB: '#8b5cf6', CLICKHOUSE: '#fbbf24', NEO4J: '#60a5fa',
  DGRAPH: '#fda4af', REDIS: '#ef4444', MEMCACHED: '#c084fc',
  KEYDB: '#bef264', VALKEY: '#93c5fd', DRAGONFLYDB: '#ea580c',
  ETCD: '#67e8f9', ELASTICSEARCH: '#facc15', OPENSEARCH: '#38bdf8',
  MEILISEARCH: '#a855f7', TYPESENSE: '#22d3ee', SOLR: '#fb923c',
  RABBITMQ: '#f97316', KAFKA: '#e2e8f0', NATS: '#4ade80',
  REDPANDA: '#f87171', PULSAR: '#818cf8', ACTIVEMQ: '#fda4af',
  MINIO: '#f43f5e', GARAGE: '#fb7185', SEAWEEDFS: '#5eead4',
  INFLUXDB: '#c084fc', QUESTDB: '#fcd34d', VICTORIAMETRICS: '#7dd3fc',
  PROMETHEUS: '#fdba74', GRAFANA: '#fdba74', JAEGER: '#22d3ee',
  QDRANT: '#a78bfa', WEAVIATE: '#34d399', MILVUS: '#60a5fa',
  CHROMADB: '#f472b6', N8N: '#fb7185', TEMPORAL: '#a5b6fc',
  VAULT: '#facc15', CONSUL: '#ec4899', KEYCLOAK: '#3b82f6',
  STEEL: '#cbd5e1', BROWSERLESS: '#fbbf24', OPENCODE: '#34d399',
  COMMANDCODE: '#22d3ee', ANTIGRAVITYCLI: '#60a5fa', KIMCHI: '#fb923c',
  FORGECODE: '#a78bfa', DEEPAGENTS: '#a3e635', QWENCODE: '#c084fc',
  FACTORYDROID: '#fb7185',
};

export const SERVICE_HEX = '#3b82f6';
export const SHARED_HEX = '#f472b6';

function hashHue(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  let hue = h % 360;
  if (hue >= 205 && hue <= 235) hue = (hue + 60) % 360;
  return hue;
}

function hsl(h: number, s: number, l: number): string {
  s /= 100; l /= 100;
  const k = (n: number) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  const to = (x: number) => Math.round(255 * x).toString(16).padStart(2, '0');
  return `#${to(f(0))}${to(f(8))}${to(f(4))}`;
}

/** Brand color for an addon type, or a stable fallback hue. */
export function addonHex(addonType: string | undefined | null, nameFallback = ''): string {
  const t = (addonType || '').toUpperCase();
  if (t && BRAND_HEX[t]) return BRAND_HEX[t];
  return hsl(hashHue(t || nameFallback), 70, 55);
}
