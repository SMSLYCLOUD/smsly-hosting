import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { TopologyGraph } from '@/types/topology';

interface UseGraphDataResult {
  data: TopologyGraph | null;
  loading: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
}

export function useGraphData(pollInterval: number = 0): UseGraphDataResult {
  const [data, setData] = useState<TopologyGraph | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const response = await api.get('/topology/');
      // Ensure we have nodes and edges
      const graph: TopologyGraph = response.data || { nodes: [], edges: [] };
      setData(graph);
      setError(null);
    } catch (err: any) {
      console.error('Failed to fetch topology:', err);
      setError(err instanceof Error ? err : new Error('Failed to fetch topology'));
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
