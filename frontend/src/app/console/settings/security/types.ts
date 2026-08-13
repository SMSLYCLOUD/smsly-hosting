// mTLS TypeScript Types
// Generic — works with any tenant service, not SMSLY-specific.

export interface MtlsHealth {
  spire_server_healthy: boolean;
  spire_agent_healthy: boolean;
  total_services: number;
  mtls_enabled_services: number;
  expired_svids: number;
  trust_domain: string;
  platform?: {
    spire_server_healthy: boolean;
    spire_agent_healthy: boolean;
    trust_domain: string;
  };
  ecosystem?: {
    spire_server_healthy: boolean;
    spire_agent_healthy: boolean;
    trust_domain: string;
  };
}

export interface MtlsConfig {
  service_id: string;
  service_name: string;
  mtls_enabled: boolean;
  spiffe_id: string;
  svid_expiry: string | null;
  is_svid_expired: boolean;
  last_rotation: string | null;
  sidecar_enabled?: boolean;
}

export interface MtlsStatusResponse {
  service_id: string;
  service_name: string;
  mtls_enabled: boolean;
  trust_domain: string;
  spiffe_id: string;
  svid_expiry: string | null;
  svid_ttl_remaining: number;
  is_svid_expired: boolean;
  last_rotation: string | null;
}

export interface MtlsAuthorizationPolicy {
  id: number;
  name: string;
  source_spiffe_id: string;
  target_service_id: string;
  target_service_name: string;
  paths: string[];
  methods: string[];
  action: 'allow' | 'deny';
  priority: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}
