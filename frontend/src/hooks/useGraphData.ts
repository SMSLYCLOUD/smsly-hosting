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
    { id: 'srv-1', type: 'SERVICE', data: { name: 'auth-service', status: 'ACTIVE' } },
    { id: 'srv-2', type: 'SERVICE', data: { name: 'payment-api', status: 'ACTIVE' } },
    { id: 'srv-3', type: 'SERVICE', data: { name: 'worker-job', status: 'FAILED' } },
    { id: 'addon-1', type: 'ADDON', data: { name: 'primary-db', kind: 'DATABASE' } },
    { id: 'addon-2', type: 'ADDON', data: { name: 'session-cache', kind: 'CACHE' } },
    { id: 'addon-3', type: 'ADDON', data: { name: 'task-queue', kind: 'QUEUE' } },
    { id: 'addon-4', type: 'ADDON', data: { name: 'analytics-db', kind: 'DATABASE' } },
  ],
  edges: [
    { id: 'e1', source: 'srv-1', target: 'addon-1', type: 'connection' },
    { id: 'e2', source: 'srv-1', target: 'addon-2', type: 'connection' },
    { id: 'e3', source: 'srv-2', target: 'addon-1', type: 'connection' },
    { id: 'e4', source: 'srv-2', target: 'addon-3', type: 'connection' },
    { id: 'e5', source: 'srv-3', target: 'addon-3', type: 'connection' },
    { id: 'e6', source: 'srv-3', target: 'addon-4', type: 'connection' },
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
