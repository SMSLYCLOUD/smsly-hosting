// useMtls Hook
// React Query hook for mTLS management API.

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { config } from '@/lib/config';
import { tokenManager } from '@/lib/token-manager';
import type { MtlsHealth, MtlsConfig, MtlsAuthorizationPolicy } from '../types';

const API_BASE = config.api.baseUrl;

async function fetchJson<T>(path: string): Promise<T> {
  const token = tokenManager.getAccessToken();
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const token = tokenManager.getAccessToken();
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const token = tokenManager.getAccessToken();
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'PUT',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function deleteJson(path: string): Promise<void> {
  const token = tokenManager.getAccessToken();
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
}

export function useMtls() {
  const queryClient = useQueryClient();

  const health = useQuery<MtlsHealth>({
    queryKey: ['mtls', 'health'],
    queryFn: () => fetchJson('mtls/health'),
    refetchInterval: 30_000,
  });

  const configs = useQuery<MtlsConfig[]>({
    queryKey: ['mtls', 'configs'],
    queryFn: () => fetchJson('mtls/configs'),
    refetchInterval: 30_000,
  });

  const enableMtls = useMutation({
    mutationFn: (serviceId: string) =>
      postJson<{ auto_injected: boolean }>(`services/${serviceId}/mtls/enable`),
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

  // --- Authorization Policies ---

  const policies = useQuery<MtlsAuthorizationPolicy[]>({
    queryKey: ['mtls', 'policies'],
    queryFn: () => {
      // Fetch all policies (no service filter = all policies)
      return fetchJson('mtls/policies/?service_id=*');
    },
  });

  const fetchPoliciesForService = useQuery<MtlsAuthorizationPolicy[]>({
    queryKey: ['mtls', 'policies', 'service'],
    queryFn: () => fetchJson('mtls/policies/'),
    enabled: false,
  });

  const createPolicy = useMutation({
    mutationFn: (data: {
      name: string;
      source_spiffe_id: string;
      target_service_id: string;
      paths?: string[];
      methods?: string[];
      action?: 'allow' | 'deny';
      priority?: number;
    }) => postJson<MtlsAuthorizationPolicy>('mtls/policies/', data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mtls', 'policies'] });
    },
  });

  const updatePolicy = useMutation({
    mutationFn: ({ id, ...data }: { id: number } & Partial<MtlsAuthorizationPolicy>) =>
      putJson<MtlsAuthorizationPolicy>(`mtls/policies/${id}/`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mtls', 'policies'] });
    },
  });

  const deletePolicy = useMutation({
    mutationFn: (id: number) => deleteJson(`mtls/policies/${id}/`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mtls', 'policies'] });
    },
  });

  return {
    health,
    configs,
    enableMtls,
    disableMtls,
    policies,
    createPolicy,
    updatePolicy,
    deletePolicy,
  };
}
