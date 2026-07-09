import { useEffect, useRef, useState } from 'react';

export interface ServiceStatusUpdate {
  type: 'service_status_update';
  service_id: string;
  service_name: string;
  status: string;
  deployment_status: string;
  updated_at: string;
}

export interface DeploymentStatusUpdate {
  type: 'deployment_status_update';
  service_id: string;
  service_name: string;
  deployment_id: string;
  status: string;
  updated_at: string;
}

export type WebSocketMessage = ServiceStatusUpdate | DeploymentStatusUpdate;

interface UseWebSocketOptions {
  url: string;
  onMessage?: (message: WebSocketMessage) => void;
  onOpen?: () => void;
  onClose?: (event: CloseEvent) => void;
  onError?: (event: Event) => void;
  reconnect?: boolean;
  reconnectInterval?: number;
  maxReconnectAttempts?: number;
}

export function useWebSocket(options: UseWebSocketOptions) {
  const {
    url,
    onMessage,
    onOpen,
    onClose,
    onError,
    reconnect = true,
    reconnectInterval = 5000,
    maxReconnectAttempts = 10,
  } = options;

  const wsRef = useRef<WebSocket | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<'connecting' | 'open' | 'closed' | 'error'>('closed');
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectAttempts = useRef(0);

  const connect = () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    setConnectionStatus('connecting');

    // Auth is provided by the HttpOnly auth cookie that the browser
    // attaches to the WebSocket upgrade request. The server's
    // QueryStringAuthMiddleware reads the cookie directly from the
    // Cookie header (no token in the query string) — see
    // backend/apps/deployments/middleware.py for the matching
    // server-side change.

    try {
      wsRef.current = new WebSocket(url);

      wsRef.current.onopen = () => {
        setConnectionStatus('open');
        reconnectAttempts.current = 0;
        onOpen?.();
      };

      wsRef.current.onmessage = (event) => {
        try {
          const message: WebSocketMessage = JSON.parse(event.data);
          onMessage?.(message);
        } catch (error) {
          console.error('Failed to parse WebSocket message:', error);
        }
      };

      wsRef.current.onclose = (event) => {
        setConnectionStatus('closed');
        onClose?.(event);
        
        if (reconnect && reconnectAttempts.current < maxReconnectAttempts) {
          // Exponential backoff: base * 2^attempt, capped at 30s
          // e.g. base=2000 → 2s, 4s, 8s, 16s, 30s, 30s, ...
          const delay = Math.min(reconnectInterval * Math.pow(2, reconnectAttempts.current), 30000);
          reconnectTimeoutRef.current = setTimeout(() => {
            reconnectAttempts.current++;
            connect();
          }, delay);
        }
      };

      wsRef.current.onerror = (event) => {
        setConnectionStatus('error');
        onError?.(event);
      };
    } catch (error) {
      console.error('WebSocket connection failed:', error);
      setConnectionStatus('error');
    }
  };

  const disconnect = () => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    
    setConnectionStatus('closed');
  };

  const sendMessage = (message: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message));
    }
  };

  useEffect(() => {
    connect();

    return () => {
      disconnect();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  return {
    connectionStatus,
    connect,
    disconnect,
    sendMessage,
  };
}

// Service status hook for dashboard and WebSocket URL resolver
export function getWsUrl(path: string): string {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || process.env.NEXT_PUBLIC_API_BASE;
  if (apiUrl && !apiUrl.startsWith('/')) {
    const baseUrl = apiUrl.replace(/\/api\/v\d+\/?$/, '').replace(/\/+$/, '');
    const wsScheme = baseUrl.startsWith('https') ? 'wss' : 'ws';
    const hostPart = baseUrl.replace(/^https?:\/\//, '');
    return `${wsScheme}://${hostPart}${path}`;
  }
  const proto = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss' : 'ws';
  let host = typeof window !== 'undefined' ? window.location.host : 'localhost:8000';
  if (typeof window !== 'undefined' && (window.location.port === '3000' || window.location.port === '3001')) {
    host = `${window.location.hostname}:8000`;
  }
  return `${proto}://${host}${path}`;
}

export function useServiceStatusUpdates(userId: string) {
  const [services, setServices] = useState<any[]>([]);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const { connectionStatus } = useWebSocket({
    url: getWsUrl('/ws/service-status/'),
    reconnect: true,
    reconnectInterval: 2000,
    maxReconnectAttempts: 15,
    onMessage: (message) => {
      if (message.type === 'service_status_update') {
        setServices(prev => {
          // Update existing service or add new one
          const existingIndex = prev.findIndex(s => s.id === message.service_id);
          const service = {
            id: message.service_id,
            name: message.service_name,
            status: message.status,
            deployment_status: message.deployment_status,
            updated_at: message.updated_at,
          };
          
          if (existingIndex >= 0) {
            const updated = [...prev];
            updated[existingIndex] = service;
            return updated;
          } else {
            return [...prev, service];
          }
        });
        
        setLastUpdated(new Date());
      }
    },
  });

  return {
    services,
    connectionStatus,
    lastUpdated,
  };
}

// Deployment status hook for individual service pages
export function useDeploymentStatusUpdates(serviceId: string) {
  const [deployments, setDeployments] = useState<any[]>([]);

  const { connectionStatus } = useWebSocket({
    url: getWsUrl('/ws/service-status/'),
    reconnect: true,
    reconnectInterval: 2000,
    maxReconnectAttempts: 15,
    onMessage: (message) => {
      if (message.type === 'deployment_status_update' && message.service_id === serviceId) {
        setDeployments(prev => {
          // Update existing deployment or add new one
          const existingIndex = prev.findIndex(d => d.id === message.deployment_id);
          const deployment = {
            id: message.deployment_id,
            service_id: message.service_id,
            service_name: message.service_name,
            status: message.status,
            updated_at: message.updated_at,
          };
          
          if (existingIndex >= 0) {
            const updated = [...prev];
            updated[existingIndex] = deployment;
            return updated;
          } else {
            return [...prev, deployment];
          }
        });
      }
    },
  });

  return {
    deployments,
    connectionStatus,
  };
}

// Utility function to get status color
export function getStatusColor(status: string): string {
  switch (status.toLowerCase()) {
    case 'active':
      return 'text-green-500';
    case 'failed':
      case 'deletion_failed':
      return 'text-red-500';
    case 'deletion_pending':
      return 'text-yellow-500';
    case 'building':
    case 'deploying':
    case 'review':
    case 'queued':
    case 'health_check':
    case 'traffic_shifting':
      return 'text-blue-500';
    case 'staged':
      return 'text-purple-500';
    default:
      return 'text-gray-500';
  }
}

// Utility function to get status icon
export function getStatusIcon(status: string): string {
  switch (status.toLowerCase()) {
    case 'active':
      return '✓';
    case 'failed':
      return '✗';
    case 'deletion_pending':
      return '⏳';
    case 'building':
    case 'deploying':
      return '🏗️';
    case 'review':
      return '👀';
    case 'queued':
      return '⏰';
    case 'health_check':
      return '❤️';
    case 'traffic_shifting':
      return '🔄';
    case 'staged':
      return '🎭';
    default:
      return '❓';
  }
}