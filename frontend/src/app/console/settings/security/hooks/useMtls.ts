// useMtls Hook
// React Query hook for mTLS management API.

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { config } from '@/lib/config';
import { tokenManager } from '@/lib/token-manager';
import type { MtlsHealth, MtlsConfig } from '../types';

const API_BASE = config.api.baseUrl;

async function fetchJson<T>(path: string): Promise<T> {
  const token = tokenManager.getAccessToken();
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function postJson<T>(path: string): Promise<T> {
  const token = tokenManager.getAccessToken();
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export function useMtls() {
  const queryClient = useQueryClient();

  const health = useQuery<MtlsHealth>({
    queryKey: ['mtls', 'health'],
    queryFn: () => fetchJson('mtls/health'),
    refetchInterval: 30_000, // Refresh every 30s
  });

  const configs = useQuery<MtlsConfig[]>({
    queryKey: ['mtls', 'configs'],
    queryFn: () => fetchJson('mtls/configs'),
    refetchInterval: 30_000,
  });

  const enableMtls = useMutation({
    mutationFn: (serviceId: string) => postJson(`services/${serviceId}/mtls/enable`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mtls'] });
    },
  });

  const disableMtls = useMutation({
    mutationFn: (serviceId: string) => postJson(`services/${serviceId}/mtls/disable`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mtls'] });
    },
  });

  return {
    health,
    configs,
    enableMtls,
    disableMtls,
  };
}
