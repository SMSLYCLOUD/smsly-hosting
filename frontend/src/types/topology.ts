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
  type: 'SERVICE' | 'ADDON' | 'EXTERNAL';
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
  type: 'OWNS' | 'CONNECTS_TO';
  label?: string;
  data?: TopologyEdgeData;
}

export interface TopologyGraph {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}
