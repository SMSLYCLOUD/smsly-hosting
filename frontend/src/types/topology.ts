export interface TopologyNodeData {
  name: string;
  label?: string;
  kind: 'COMPUTE' | 'DATABASE' | 'CACHE' | 'QUEUE' | 'STORAGE' | 'SEARCH' | 'EXTERNAL';
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
  type: 'service' | 'addon' | 'volume' | 'domain' | 'cron' | 'tunnel';
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
  type: 'DATABASE' | 'CACHE' | 'QUEUE' | 'SEARCH' | 'STORAGE' | 'ADDON' | 'API' | 'DOMAIN' | 'CRON' | 'TUNNEL';
  label?: string;
  data?: TopologyEdgeData;
}

export interface TopologyGraph {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}
