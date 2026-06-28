import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { TopologyGraph, TopologyNode, TopologyEdge, EcosystemGraph } from '@/types/topology';

interface UseGraphDataResult {
  data: TopologyGraph | null;
  loading: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
}

const ECOSYSTEM_KIND_MAP: Record<string, TopologyNode['data']['kind']> = {
  COMPUTE: 'COMPUTE',
  DATABASE: 'DATABASE',
  CACHE: 'CACHE',
  QUEUE: 'QUEUE',
  STORAGE: 'STORAGE',
  EXTERNAL: 'EXTERNAL',
  PROXY: 'PROXY',
  WORKER: 'WORKER',
};

const ECOSYSTEM_STATUS_MAP: Record<string, string> = {
  healthy: 'ACTIVE',
  degraded: 'DEGRADED',
  down: 'DOWN',
};

const EDGE_ID_COUNTER = { next: 1 };

function ecosystemNodeToTopologyNode(e: EcosystemGraph['nodes'][0]): TopologyNode {
  const kind = ECOSYSTEM_KIND_MAP[e.kind] || 'COMPUTE';
  return {
    id: e.id,
    type: 'service',
    data: {
      name: e.label,
      label: e.label,
      kind,
      subtype: e.type,
      status: ECOSYSTEM_STATUS_MAP[e.status || 'healthy'] || 'ACTIVE',
      region: '',
      metadata: e.metadata || {},
    },
  };
}

function ecosystemEdgeToTopologyEdge(e: EcosystemGraph['edges'][0]): TopologyEdge {
  const edgeId = `eco-edge-${EDGE_ID_COUNTER.next++}`;
  return {
    id: edgeId,
    source: e.source,
    target: e.target,
    type: e.type as TopologyEdge['type'],
    label: e.label,
  };
}

function fetchEcosystem(): Promise<EcosystemGraph> {
  return fetch('/api/v1/topology/ecosystem/', {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  }).then(res => {
    if (!res.ok) throw new Error(`Ecosystem fetch failed: HTTP ${res.status}`);
    return res.json();
  });
}

export function useGraphData(pollInterval: number = 0): UseGraphDataResult {
  const [data, setData] = useState<TopologyGraph | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchData = useCallback(async () => {
    EDGE_ID_COUNTER.next = 1;
    try {
      const [userResp, ecosystemGraph] = await Promise.all([
        api.get('/topology/'),
        fetchEcosystem().catch(() => null),
      ]);

      const userGraph: TopologyGraph = userResp.data || { nodes: [], edges: [] };

      if (ecosystemGraph) {
        const ecoNodes = ecosystemGraph.nodes.map(ecosystemNodeToTopologyNode);
        const ecoEdges = ecosystemGraph.edges.map(ecosystemEdgeToTopologyEdge);

        const mergedEdges = [...ecoEdges, ...userGraph.edges];
        const existingNodeIds = new Set(ecoNodes.map(n => n.id));
        const mergedNodes = [
          ...ecoNodes,
          ...userGraph.nodes.filter(n => !existingNodeIds.has(n.id)),
        ];

        setData({ nodes: mergedNodes, edges: mergedEdges });
      } else {
        setData(userGraph);
      }
      setError(null);
    } catch (err: any) {
      setError(err);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();

    const handleRefresh = () => fetchData();
    window.addEventListener('smsly:topology-refresh', handleRefresh);

    if (pollInterval > 0) {
      const intervalId = setInterval(fetchData, pollInterval);
      return () => {
        clearInterval(intervalId);
        window.removeEventListener('smsly:topology-refresh', handleRefresh);
      };
    }
    return () => window.removeEventListener('smsly:topology-refresh', handleRefresh);
  }, [fetchData, pollInterval]);

  return { data, loading, error, refresh: fetchData };
}
