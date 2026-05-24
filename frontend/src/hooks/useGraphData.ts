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
    { id: 'internet', type: 'service', data: { name: 'Internet', status: 'ACTIVE', kind: 'EXTERNAL', subtype: 'External Traffic Source', region: 'global' } },
    { id: 'frp', type: 'service', data: { name: 'FRP Server', status: 'ACTIVE', kind: 'EXTERNAL', subtype: 'Tunnel Relay', region: 'global' } },
    { id: 'caddy', type: 'service', data: { name: 'Caddy', status: 'ACTIVE', kind: 'PROXY', subtype: 'Edge Proxy / TLS Termination', region: 'us-east' } },
    { id: 'frontend', type: 'service', data: { name: 'Frontend (Next.js)', status: 'ACTIVE', kind: 'COMPUTE', subtype: 'Web Dashboard', region: 'us-east' } },
    { id: 'backend', type: 'service', data: { name: 'Backend (Django)', status: 'ACTIVE', kind: 'COMPUTE', subtype: 'REST API / Admin', region: 'us-east' } },
    { id: 'celery-default', type: 'service', data: { name: 'Celery Default', status: 'ACTIVE', kind: 'WORKER', subtype: 'General Tasks', region: 'us-east' } },
    { id: 'celery-fast', type: 'service', data: { name: 'Celery Fast', status: 'ACTIVE', kind: 'WORKER', subtype: 'Heartbeats / Metrics', region: 'us-east' } },
    { id: 'celery-beat', type: 'service', data: { name: 'Celery Beat', status: 'ACTIVE', kind: 'WORKER', subtype: 'Periodic Scheduler', region: 'us-east' } },
    { id: 'celery-deploy', type: 'service', data: { name: 'Celery Deploy', status: 'ACTIVE', kind: 'WORKER', subtype: 'Builds / Provisioning', region: 'us-east' } },
    { id: 'traefik', type: 'service', data: { name: 'Traefik', status: 'ACTIVE', kind: 'PROXY', subtype: 'Docker Service Router', region: 'us-east' } },
    { id: 'postgres', type: 'addon', data: { name: 'PostgreSQL', kind: 'DATABASE', status: 'ACTIVE', subtype: 'Platform Database', region: 'us-east' } },
    { id: 'redis', type: 'addon', data: { name: 'Redis', kind: 'CACHE', status: 'ACTIVE', subtype: 'Cache / Channels Layer', region: 'us-east' } },
    { id: 'rabbitmq', type: 'addon', data: { name: 'RabbitMQ', kind: 'QUEUE', status: 'ACTIVE', subtype: 'Celery Message Broker', region: 'us-east' } },
    { id: 'socket-proxy', type: 'service', data: { name: 'Socket Proxy', kind: 'PROXY', status: 'ACTIVE', subtype: 'Docker API Proxy', region: 'us-east' } },
    { id: 'registry', type: 'addon', data: { name: 'Docker Registry', kind: 'STORAGE', status: 'ACTIVE', subtype: 'Image Storage', region: 'us-east' } },
    { id: 'user-containers', type: 'service', data: { name: 'User Containers', kind: 'COMPUTE', status: 'ACTIVE', subtype: 'Deployed User Apps', region: 'us-east' } },
  ],
  edges: [
    { id: 'e1', source: 'internet', target: 'caddy', type: 'TUNNEL', label: 'HTTP/HTTPS' },
    { id: 'e2', source: 'frp', target: 'caddy', type: 'TUNNEL', label: 'Tunnel relay' },
    { id: 'e3', source: 'caddy', target: 'frontend', type: 'PROXY_CHAIN', label: 'catch-all' },
    { id: 'e4', source: 'caddy', target: 'backend', type: 'PROXY_CHAIN', label: '/api /ws /admin /health' },
    { id: 'e5', source: 'caddy', target: 'traefik', type: 'PROXY_CHAIN', label: 'wildcard *.grid.smsly.cloud' },
    { id: 'e6', source: 'traefik', target: 'user-containers', type: 'PROXY_CHAIN', label: 'Dynamic routing' },
    { id: 'e7', source: 'frontend', target: 'backend', type: 'API' },
    { id: 'e8', source: 'backend', target: 'postgres', type: 'DATABASE', label: 'Data' },
    { id: 'e9', source: 'backend', target: 'redis', type: 'CACHE', label: 'Cache' },
    { id: 'e10', source: 'backend', target: 'rabbitmq', type: 'QUEUE', label: 'Publish tasks' },
    { id: 'e11', source: 'backend', target: 'socket-proxy', type: 'API', label: 'Deploy operations' },
    { id: 'e12', source: 'celery-default', target: 'rabbitmq', type: 'QUEUE', label: 'Consume AMQP' },
    { id: 'e13', source: 'celery-default', target: 'postgres', type: 'DATABASE', label: 'Task results' },
    { id: 'e14', source: 'celery-fast', target: 'rabbitmq', type: 'QUEUE', label: 'Consume AMQP' },
    { id: 'e15', source: 'celery-fast', target: 'redis', type: 'CACHE', label: 'Cache + Pub/Sub' },
    { id: 'e16', source: 'celery-beat', target: 'rabbitmq', type: 'QUEUE', label: 'Schedule' },
    { id: 'e17', source: 'celery-deploy', target: 'rabbitmq', type: 'QUEUE', label: 'Consume AMQP' },
    { id: 'e18', source: 'celery-deploy', target: 'socket-proxy', type: 'API', label: 'Build/Run images' },
    { id: 'e19', source: 'socket-proxy', target: 'user-containers', type: 'INTERNAL', label: 'Container lifecycle' },
    { id: 'e20', source: 'socket-proxy', target: 'registry', type: 'STORAGE', label: 'Pull/Push images' },
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
