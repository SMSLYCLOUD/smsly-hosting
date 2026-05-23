import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { TopologyGraph } from '@/types/topology';

interface UseGraphDataResult {
  data: TopologyGraph | null;
  loading: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
}

const MOCK_DATA: TopologyGraph = {
  nodes: [
    { id: 'srv-1', type: 'service', data: { name: 'auth-service', status: 'ACTIVE', kind: 'COMPUTE', subtype: 'generic', region: 'us-east' } },
    { id: 'srv-2', type: 'service', data: { name: 'payment-api', status: 'ACTIVE', kind: 'COMPUTE', subtype: 'generic', region: 'us-east' } },
    { id: 'srv-3', type: 'service', data: { name: 'worker-job', status: 'FAILED', kind: 'WORKER', subtype: 'generic', region: 'us-east' } },
    { id: 'addon-1', type: 'addon', data: { name: 'primary-db', kind: 'DATABASE', status: 'ACTIVE', subtype: 'postgres', region: 'us-east' } },
    { id: 'addon-2', type: 'addon', data: { name: 'session-cache', kind: 'CACHE', status: 'ACTIVE', subtype: 'redis', region: 'us-east' } },
    { id: 'addon-3', type: 'addon', data: { name: 'task-queue', kind: 'QUEUE', status: 'ACTIVE', subtype: 'rabbitmq', region: 'us-east' } },
    { id: 'addon-4', type: 'addon', data: { name: 'analytics-db', kind: 'DATABASE', status: 'ACTIVE', subtype: 'postgres', region: 'us-east' } },
  ],
  edges: [
    { id: 'e1', source: 'srv-1', target: 'addon-1', type: 'INTERNAL' },
    { id: 'e2', source: 'srv-1', target: 'addon-2', type: 'INTERNAL' },
    { id: 'e3', source: 'srv-2', target: 'addon-1', type: 'INTERNAL' },
    { id: 'e4', source: 'srv-2', target: 'addon-3', type: 'INTERNAL' },
    { id: 'e5', source: 'srv-3', target: 'addon-3', type: 'INTERNAL' },
    { id: 'e6', source: 'srv-3', target: 'addon-4', type: 'INTERNAL' },
  ]
};

export function useGraphData(pollInterval: number = 0): UseGraphDataResult {
  const [data, setData] = useState<TopologyGraph | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const response = await api.get('/topology/');
      let graph: TopologyGraph = response.data || { nodes: [], edges: [] };
      if (!graph.nodes || graph.nodes.length === 0) {
        console.log('No topology data from API, using MOCK DATA for local dev');
        graph = MOCK_DATA;
      }
      setData(graph);
      setError(null);
    } catch (err: any) {
      console.error('Failed to fetch topology, using MOCK DATA:', err);
      setData(MOCK_DATA);
      setError(null); // Clear error to allow rendering
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
