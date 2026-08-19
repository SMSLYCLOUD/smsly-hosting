export interface TopologyNodeData {
  name: string;
  label?: string;
  kind: 'COMPUTE' | 'DATABASE' | 'CACHE' | 'QUEUE' | 'STORAGE' | 'SEARCH' | 'EXTERNAL' | 'PROXY' | 'WORKER';
  subtype: string;
  status: string;
  region: string;
  url?: string;
  img?: string;
  parent_id?: string;
  project_id?: string;
  project_name?: string;
  // Domain-specific
  ssl?: boolean;
  // Cron-specific
  schedule?: string;
  command?: string;
  // Tunnel-specific
  public_url?: string;
  local_port?: number;
  // Volume-specific
  mount_path?: string;
  size_gb?: number;
  // Addon-specific
  addon_type?: string;
  // Replica-specific
  node?: string;
  spawn_reason?: string;
  metrics?: Record<string, any>;
  created_at?: string;
  // Deploy info
  deploy_status?: string;
  deploy_commit?: string;
  deploy_time?: string;
  build_strategy?: string;
  health?: string;
  domain?: string;
  port?: number;
  replicas?: number;
  metadata?: {
    replicas?: number;
    port?: number;
    language?: string;
    repo?: string;
    branch?: string;
  };
}

export interface TopologyNode {
  id: string;
  type: 'service' | 'addon' | 'volume' | 'domain' | 'cron' | 'tunnel' | 'proxy' | 'worker' | 'broker' | 'platform' | 'platform_db' | 'platform_cache' | 'registry' | 'external' | 'replica';
  data: TopologyNodeData;
  // Position fields populated by layout engine
  x?: number;
  y?: number;
  z?: number;
  vx?: number;
  vy?: number;
  vz?: number;
}

export interface TopologyEdgeData {
  protocol?: string;
  evidence?: string;
}

export interface TopologyEdge {
  id: string;
  source: string;
  target: string;
  type: 'DATABASE' | 'CACHE' | 'QUEUE' | 'SEARCH' | 'STORAGE' | 'ADDON' | 'API' | 'DOMAIN' | 'CRON' | 'TUNNEL' | 'PROXY_CHAIN' | 'INTERNAL' | 'REPLICA';
  label?: string;
  data?: TopologyEdgeData;
}

export interface TopologyGraph {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

// ── Ecosystem Infrastructure Types ──────────────────────────────────────────

export interface EcosystemNode {
  id: string;
  type: string;
  kind: string;
  label: string;
  status?: 'healthy' | 'degraded' | 'down';
  metadata?: Record<string, any>;
}

export interface EcosystemEdge {
  source: string;
  target: string;
  type: string;
  label?: string;
  animated?: boolean;
}

export interface EcosystemGraph {
  nodes: EcosystemNode[];
  edges: EcosystemEdge[];
}
