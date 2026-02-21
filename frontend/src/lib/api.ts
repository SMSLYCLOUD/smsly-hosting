import axios from 'axios';
import { clearAuthCookies } from '@/lib/auth-cookies';

// Use dynamic origin detection - works in browser and during SSR
const getApiUrl = () => {
  if (typeof window !== 'undefined') {
    return `${window.location.origin}/api/v1`;
  }
  return process.env.NEXT_PUBLIC_API_URL || '/api/v1';
};

const api = axios.create({
  baseURL: getApiUrl(),
  withCredentials: true,
});

function getAuthTokenFromCookie(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)auth_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

// Interceptor to add auth token
api.interceptors.request.use((config) => {
  const token = typeof window !== 'undefined'
    ? (localStorage.getItem('auth_token') || getAuthTokenFromCookie())
    : null;
  if (token) {
    config.headers.Authorization = `Token ${token}`;
  }
  return config;
});

function isProtectedPath(path: string): boolean {
  const protectedPrefixes = [
    '/dashboard',
    '/services',
    '/projects',
    '/deployments',
    '/new',
    '/project',
    '/settings',
    '/billing',
    '/servers',
    '/tunnels',
    '/intelligence',
    '/backups',
    '/transfers',
    '/admin-dashboard',
    '/topology',
    '/functions',
  ];
  return protectedPrefixes.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
}

// Auto-clear stale tokens on 401 and redirect to login
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const statusCode = error?.response?.status;
    const requestUrl = String(error?.config?.url || '');

    // Deploy actions intentionally return 409 when another deploy is active.
    // Treat this as a non-fatal app state so callers can display a friendly
    // "already deploying" message instead of surfacing Axios stack traces.
    if (
      statusCode === 409 &&
      /\/services\/[^/]+\/deploy\/?$/.test(requestUrl) &&
      error?.response
    ) {
      return Promise.resolve(error.response);
    }

    if (error.response?.status === 401 && typeof window !== 'undefined') {
      const path = window.location.pathname;
      // Avoid redirect loops and keep public pages accessible.
      if (isProtectedPath(path) && !path.startsWith('/login') && !path.startsWith('/register')) {
        localStorage.removeItem('auth_token');
        clearAuthCookies();
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  }
);

export interface Service {
  id: string;
  name: string;
  repository_url?: string;
  branch?: string;
  internal_port?: number;
  public_domain?: string;
  custom_domains?: string[];
  domain_verified?: boolean;
  verification_token?: string;
  created_at?: string;
  cpu_cores?: number;
  memory_mb?: number;
  min_replicas?: number;
  max_replicas?: number;
  autoscale_cpu_target?: number;
  vpa_enabled?: boolean;
  buildpack?: 'NIXPACKS' | 'DOCKER' | 'STATIC';
  root_directory?: string;
  build_command?: string;
  deploy_type?: 'GIT' | 'DOCKER' | 'UPLOAD' | 'TEMPLATE' | 'FUNCTION';
  function_code?: string;
  function_runtime?: string;
  docker_image?: string;
  start_command?: string;
  template_id?: string;
  provider?: string;  // Cloud provider: 'local', 'aws', 'gcp', 'azure', 'digitalocean', etc.
  region?: string;    // Deployment region
  health_status?: 'healthy' | 'unhealthy' | 'unknown' | 'starting';
  restart_policy?: 'always' | 'unless-stopped' | 'on-failure' | 'no';
  health_check_path?: string;
  health_check_interval?: number;
  health_check_timeout?: number;
  health_check_retries?: number;
  // Project grouping
  project?: string | null;
  project_name?: string | null;
  project_slug?: string | null;
  project_emoji?: string | null;
  latest_deployment?: {
    id: string;
    status: string;
    commit_hash?: string;
    created_at: string;
  };
}

export interface Project {
  id: string;
  name: string;
  slug: string;
  description?: string;
  icon_emoji: string;
  color: string;
  is_default: boolean;
  services_count: number;
  latest_deploy_status?: string | null;
  latest_deploy_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Deployment {
  id: string;
  service: string;
  commit_hash: string;
  commit_message?: string;
  status: string;
  build_logs?: string;
  pipeline_stages?: { name: string; status: string; duration?: number }[];
  ai_diagnosis?: string;
  duration_seconds?: number;
  created_at: string;
  finished_at?: string;
}

export interface EnvVar {
  id: number;
  key: string;
  value: string;
  is_secret: boolean;
}

export interface CronJob {
    id: number;
    name: string;
    schedule: string;
    command: string;
    last_run_at?: string;
}

export interface Volume {
    id: number;
    name: string;
    mount_path: string;
    size_gb: number;
}

export const servicesApi = {
  list: async (): Promise<Service[]> => {
    const response = await api.get('/services/');
    // Handle paginated (object with results) or direct array responses
    return Array.isArray(response.data) ? response.data : (response.data?.results || []);
  },
  create: async (data: any): Promise<Service> => {
    // If it's a file upload, use FormData
    if (data instanceof FormData) {
      const response = await api.post('/services/', data, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      return response.data;
    }
    const response = await api.post('/services/', data);
    return response.data;
  },
  get: async (id: string): Promise<Service> => {
    const response = await api.get(`/services/${id}/`);
    return response.data;
  },
  update: async (id: string, data: Partial<Service>): Promise<Service> => {
    const response = await api.patch(`/services/${id}/`, data);
    return response.data;
  },
  deploy: async (id: string, ref: string = 'HEAD') => {
    const response = await api.post(`/services/${id}/deploy/`, { ref });
    return response.data;
  },
  restart: async (id: string): Promise<any> => {
    const response = await api.post(`/services/${id}/restart/`);
    return response.data;
  },
  stop: async (id: string): Promise<any> => {
    const response = await api.post(`/services/${id}/stop/`);
    return response.data;
  },

  // Deployment Management
  getDeployments: async (serviceId: string): Promise<Deployment[]> => {
    const response = await api.get(`/services/${serviceId}/deployments/`);
    return Array.isArray(response.data) ? response.data : (response.data?.results || []);
  },
  getDeployment: async (id: string): Promise<Deployment> => {
    const response = await api.get(`/deployments/${id}/`);
    return response.data;
  },
  rollback: async (deploymentId: string): Promise<any> => {
    const response = await api.post(`/deployments/${deploymentId}/rollback/`);
    return response.data;
  },
  cancelDeployment: async (deploymentId: string): Promise<any> => {
    const response = await api.post(`/deployments/${deploymentId}/cancel/`);
    return response.data;
  },
  approveDeployment: async (deploymentId: string, overrides?: {
    cpu_cores?: number;
    memory_mb?: number;
    env_overrides?: Record<string, string>;
  }): Promise<any> => {
    const response = await api.post(`/deployments/${deploymentId}/approve/`, overrides || {});
    return response.data;
  },

  // Env Vars Management
  getEnvVars: async (serviceId: string): Promise<EnvVar[]> => {
    const response = await api.get(`/services/${serviceId}/env_vars/`);
    return Array.isArray(response.data) ? response.data : (response.data?.results || []);
  },
  createEnvVar: async (serviceId: string, data: Partial<EnvVar>): Promise<EnvVar> => {
    const response = await api.post(`/services/${serviceId}/env_vars/`, data);
    return response.data;
  },
  deleteEnvVar: async (serviceId: string, envVarId: number): Promise<void> => {
    await api.delete(`/services/${serviceId}/env_vars/${envVarId}/`);
  },

  // Metrics
  getMetrics: async (serviceId: string, duration: string = '1h'): Promise<any> => {
    const response = await api.get(`/services/${serviceId}/metrics/`, { params: { duration } });
    return response.data;
  },
  recheckHealth: async (serviceId: string, reset_backoff: boolean = true): Promise<any> => {
    const response = await api.post(`/services/${serviceId}/recheck-health/`, { reset_backoff });
    return response.data;
  },

  // Cron Jobs
  getCronJobs: async (serviceId: string): Promise<CronJob[]> => {
    const response = await api.get(`/services/${serviceId}/cron/`);
    return Array.isArray(response.data) ? response.data : (response.data?.results || []);
  },
  createCronJob: async (serviceId: string, data: Partial<CronJob>): Promise<CronJob> => {
    const response = await api.post(`/services/${serviceId}/cron/`, data);
    return response.data;
  },
  deleteCronJob: async (serviceId: string, jobId: number): Promise<void> => {
    await api.delete(`/services/${serviceId}/cron/${jobId}/`);
  },

  // Storage
  getVolumes: async (serviceId: string): Promise<Volume[]> => {
      const response = await api.get(`/services/${serviceId}/volumes/`);
      return Array.isArray(response.data) ? response.data : (response.data?.results || []);
  },
  createVolume: async (serviceId: string, data: Partial<Volume>): Promise<Volume> => {
      const response = await api.post(`/services/${serviceId}/volumes/`, data);
      return response.data;
  },
  deleteVolume: async (serviceId: string, volId: number): Promise<void> => {
      await api.delete(`/services/${serviceId}/volumes/${volId}/`);
  },
  browseVolume: async (serviceId: string, volId: number, path: string): Promise<any> => {
      const response = await api.get(`/services/${serviceId}/volumes/${volId}/browse/`, { params: { path } });
      return response.data;
  },

  // Domain verification
  verifyDomain: async (serviceId: string, domain: string): Promise<{ domain: string; verified: boolean; cname_target: string; message: string }> => {
      const response = await api.post(`/services/${serviceId}/verify-domain/`, { domain });
      return response.data;
  }
};

export const templatesApi = {
  list: async (): Promise<any[]> => {
    const response = await api.get('/templates/');
    // Handle pagination if present, or raw list
    return Array.isArray(response.data) ? response.data : response.data.results || [];
  },
  get: async (id: string): Promise<any> => {
    const response = await api.get(`/templates/${id}/`);
    return response.data;
  }
};

export const systemApi = {
  getConfig: async (): Promise<any> => {
    const response = await api.get('/system/config/');
    return response.data;
  },
  getDomainConfig: async (): Promise<any> => {
    const response = await api.get('/system/domain-config/');
    return response.data;
  },
  updateDomainConfig: async (data: {
    domain?: string;
    use_ssl?: boolean;
    wildcard_subdomains?: boolean;
    cloudflare_api_token?: string;
    server_ip?: string;
  }): Promise<any> => {
    const response = await api.put('/system/domain-config/', data);
    return response.data;
  },
};

export interface AIProviderBalance {
  balance: string;
  currency: string;
  raw: Record<string, any>;
}

export interface AIProviderInfo {
  id: string;
  name: string;
  configured: boolean;
  model: string;
  balance?: AIProviderBalance;
}

export interface AIProvidersResponse {
  providers: AIProviderInfo[];
  mode: 'mock' | 'solo' | 'senate_committee';
  mode_label: string;
  active_count: number;
  total_available: number;
}

export interface AITestResponse {
  response: string;
  provider: string;
  mode: string;
  active_count: number;
}

export const aiApi = {
  /** Get all AI providers with config status. Pass includeBalance=true for credit info. */
  getProviders: async (includeBalance: boolean = false): Promise<AIProvidersResponse> => {
    const response = await api.get('/ai/providers/', {
      params: includeBalance ? { include_balance: 'true' } : {},
    });
    return response.data;
  },

  /** Update AI provider settings (admin only). */
  updateProviders: async (data: Record<string, string>): Promise<any> => {
    const response = await api.post('/ai/providers/update/', data);
    return response.data;
  },

  /** Test AI with a prompt. Returns response + which provider/mode was used. */
  testPrompt: async (prompt: string, systemPrompt?: string): Promise<AITestResponse> => {
    const response = await api.post('/ai/test/', {
      prompt,
      system_prompt: systemPrompt,
    });
    return response.data;
  },
};

// ─── Teams API ──────────────────────────────────────────────────────────────

export interface Team {
  id: string;
  name: string;
  members_count: number;
  owner: string;
  created_at: string;
}

export interface TeamMember {
  id: number;
  user: number;
  username: string;
  email: string;
  role: 'ADMIN' | 'MEMBER' | 'VIEWER';
  team: string;
}

export const teamsApi = {
  list: async (): Promise<Team[]> => {
    const response = await api.get('/teams/');
    return Array.isArray(response.data) ? response.data : (response.data?.results || []);
  },
  create: async (name: string): Promise<Team> => {
    const response = await api.post('/teams/', { name });
    return response.data;
  },
  get: async (id: string): Promise<Team> => {
    const response = await api.get(`/teams/${id}/`);
    return response.data;
  },
  members: async (id: string): Promise<TeamMember[]> => {
    const response = await api.get(`/teams/${id}/members/`);
    return response.data;
  },
  inviteMember: async (teamId: string, email: string, role: string): Promise<any> => {
    const response = await api.post(`/teams/${teamId}/invite_member/`, { email, role });
    return response.data;
  },
  removeMember: async (teamId: string, userId: number): Promise<any> => {
    const response = await api.post(`/teams/${teamId}/remove_member/`, { user_id: userId });
    return response.data;
  },
};

// ─── Servers API ────────────────────────────────────────────────────────────

export interface ManagedServer {
  id: string;
  name: string;
  host: string;
  api_url: string;
  api_token?: string;
  ssh_port: number;
  is_primary: boolean;
  status: 'ONLINE' | 'OFFLINE' | 'UNKNOWN';
  last_health_check: string | null;
  server_version: string;
  services_count: number;
  created_at: string;
}

export const serversApi = {
  list: async (): Promise<ManagedServer[]> => {
    const res = await api.get('/servers/');
    return res.data?.results || res.data || [];
  },
  get: async (id: string): Promise<ManagedServer> => {
    const res = await api.get(`/servers/${id}/`);
    return res.data;
  },
  create: async (data: Partial<ManagedServer>): Promise<ManagedServer> => {
    const res = await api.post('/servers/', data);
    return res.data;
  },
  update: async (id: string, data: Partial<ManagedServer>): Promise<ManagedServer> => {
    const res = await api.patch(`/servers/${id}/`, data);
    return res.data;
  },
  delete: async (id: string): Promise<void> => {
    await api.delete(`/servers/${id}/`);
  },
  healthCheck: async (id: string): Promise<ManagedServer> => {
    const res = await api.post(`/servers/${id}/health_check/`);
    return res.data;
  },
  checkAll: async (): Promise<{ servers: ManagedServer[] }> => {
    const res = await api.post('/servers/check_all/');
    return res.data;
  },
  proxy: async (id: string, method: string, path: string, body?: any): Promise<any> => {
    const res = await api.post(`/servers/${id}/proxy/`, { method, path, body });
    return res.data;
  },
  remoteServices: async (id: string): Promise<any> => {
    const res = await api.get(`/servers/${id}/services/`);
    return res.data;
  },
  remoteDeployments: async (id: string): Promise<any> => {
    const res = await api.get(`/servers/${id}/deployments/`);
    return res.data;
  },
  remoteDomains: async (id: string): Promise<any> => {
    const res = await api.get(`/servers/${id}/domains/`);
    return res.data;
  },
  // Remote service management via proxy
  remoteDeployService: async (id: string, serviceId: string, ref: string = 'HEAD'): Promise<any> => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/deploy/`, body: { ref },
    });
    return res.data;
  },
  remoteStopService: async (id: string, serviceId: string): Promise<any> => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/stop/`,
    });
    return res.data;
  },
  remoteRestartService: async (id: string, serviceId: string): Promise<any> => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/restart/`,
    });
    return res.data;
  },
  // Remote domain management via proxy
  remoteAddDomain: async (id: string, serviceId: string, domain: string): Promise<any> => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/add-domain/`, body: { domain },
    });
    return res.data;
  },
  remoteDeleteDomain: async (id: string, serviceId: string, domain: string): Promise<any> => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/delete-domain/`, body: { domain },
    });
    return res.data;
  },
  remoteVerifyDomain: async (id: string, serviceId: string, domain: string): Promise<any> => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/verify-domain/`, body: { domain },
    });
    return res.data;
  },
  provision: async (data: any): Promise<any> => {
    const res = await api.post('/servers/provision/', data);
    return res.data;
  },
  provisionLogs: async (id: string): Promise<any> => {
    const res = await api.get(`/servers/${id}/provision-logs/`);
    return res.data;
  },
};

// ─── Multi-Deploy API ───────────────────────────────────────────────────────

export const deployApi = {
  /** Deploy a service locally + to selected remote servers */
  multiDeploy: async (
    serviceId: string,
    ref: string = 'HEAD',
    serverIds: string[] = [],
  ): Promise<any> => {
    const res = await api.post(`/services/${serviceId}/multi-deploy/`, {
      ref,
      server_ids: serverIds,
    });
    return res.data;
  },
};

// ─── Tokens API ─────────────────────────────────────────────────────────────

export interface APIToken {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  is_active: boolean;
}

export const tokensApi = {
  list: async (): Promise<APIToken[]> => {
    const res = await api.get('/tokens/');
    return res.data?.tokens || [];
  },
  create: async (name: string = 'CLI Token'): Promise<{ token: string; id: string; name: string; prefix: string }> => {
    const res = await api.post('/tokens/create/', { name });
    return res.data;
  },
  revoke: async (id: string): Promise<void> => {
    await api.delete(`/tokens/${id}/revoke/`);
  },
};

// ─── Tunnels API ────────────────────────────────────────────────────────────

export interface Tunnel {
  tunnel_id: string;
  subdomain: string;
  public_url: string;
  local_port: number;
  type: 'http' | 'tcp';
  is_active: boolean;
  created_at: string;
  expires_at: string | null;
  request_count: number;
  bandwidth_used?: number;
  shared_with?: string[];
  user_id: string;
  tier: string;
}

export interface TunnelRequest {
  id: string;
  method: string;
  path: string;
  status: number;
  duration: number;
  timestamp: string;
  headers: Record<string, string>;
  body?: string;
  response_body?: string;
}

export interface ReservedSubdomain {
  subdomain: string;
  created_at: string;
}

export const tunnelsApi = {
  /** List active tunnels for the current user */
  list: async (): Promise<Tunnel[]> => {
    const res = await api.get('/tunnels/');
    return res.data?.tunnels || res.data || [];
  },

  /** Create a new tunnel */
  create: async (data: { local_port: number; subdomain?: string; type?: string }): Promise<Tunnel> => {
    const res = await api.post('/tunnels/', data);
    return res.data;
  },

  /** Get detail of a specific tunnel */
  get: async (id: string): Promise<Tunnel> => {
    const res = await api.get(`/tunnels/${id}/`);
    return res.data;
  },

  /** Delete / close a tunnel */
  delete: async (id: string): Promise<void> => {
    await api.delete(`/tunnels/${id}/`);
  },

  /** Get request logs for a tunnel */
  requests: async (id: string): Promise<TunnelRequest[]> => {
    const res = await api.get(`/tunnels/${id}/requests/`);
    return res.data?.requests || [];
  },

  /** Replay a request */
  replay: async (tunnelId: string, requestId: string): Promise<any> => {
    const res = await api.post(`/tunnels/${tunnelId}/requests/${requestId}/replay/`);
    return res.data;
  },

  /** Share tunnel with team member */
  share: async (tunnelId: string, email: string): Promise<any> => {
    const res = await api.post(`/tunnels/${tunnelId}/share/`, { email });
    return res.data;
  },

  // ─── Subdomain management ─────────────────────────────────────────────

  /** List reserved subdomains */
  subdomains: async (): Promise<{ subdomains: ReservedSubdomain[]; limit: number }> => {
    const res = await api.get('/subdomains/');
    return res.data;
  },

  /** Reserve a subdomain */
  reserveSubdomain: async (subdomain: string): Promise<ReservedSubdomain> => {
    const res = await api.post('/subdomains/', { subdomain });
    return res.data;
  },

  /** Release a reserved subdomain */
  releaseSubdomain: async (subdomain: string): Promise<void> => {
    await api.delete(`/subdomains/${subdomain}/`);
  },
};

// ─── Billing API ────────────────────────────────────────────────────────────

export interface PricingPlan {
  id: number;
  name: string;
  slug: string;
  description: string;
  price_monthly_usd: number;
  price_yearly_usd: number;
  max_services: number;
  max_cpu_cores: number;
  max_memory_mb: number;
  max_storage_gb: number;
  is_active: boolean;
  features: {
    has_auto_scaling: boolean;
    has_priority_support: boolean;
    has_backup: boolean;
    has_server_transfer: boolean;
    has_advanced_metrics: boolean;
    has_ai_diagnosis: boolean;
  }
}

export interface UserSubscription {
  id: number;
  plan: number;
  plan_name: string;
  status: 'ACTIVE' | 'PAST_DUE' | 'CANCELLED' | 'TRIAL';
  billing_cycle: 'MONTHLY' | 'YEARLY';
  current_period_end: string;
}

export interface Invoice {
  id: number;
  total: number;
  status: 'DRAFT' | 'SENT' | 'PAID' | 'OVERDUE';
  period_start: string;
  period_end: string;
  pdf_url?: string;
}

export interface UsageSummary {
  cpu_hours: number;
  memory_gb_hours: number;
  storage_gb: number;
  bandwidth_gb: number;
  active_services: number;
  active_addons: number;
}

export const billingApi = {
  getPlans: async (): Promise<PricingPlan[]> => {
    const res = await api.get('/billing/plans/');
    return Array.isArray(res.data) ? res.data : res.data.results || [];
  },

  getSubscription: async (): Promise<UserSubscription | null> => {
    const res = await api.get('/billing/subscription/');
    const results = Array.isArray(res.data) ? res.data : res.data.results || [];
    return results.length > 0 ? results[0] : null;
  },

  subscribe: async (planId: number, cycle: 'MONTHLY' | 'YEARLY'): Promise<any> => {
    // This is a placeholder for the actual subscribe flow
    const res = await api.post('/billing/subscription/subscribe/', { plan_id: planId, cycle });
    return res.data;
  },

  cancelSubscription: async (): Promise<any> => {
    const res = await api.post('/billing/subscription/cancel/');
    return res.data;
  },

  getInvoices: async (): Promise<Invoice[]> => {
    const res = await api.get('/billing/invoices/');
    return Array.isArray(res.data) ? res.data : res.data.results || [];
  },

  getUsage: async (): Promise<UsageSummary> => {
    const res = await api.get('/billing/usage/');
    return res.data;
  },

  // Admin
  adminGetPlans: async (): Promise<PricingPlan[]> => {
    const res = await api.get('/billing/admin/plans/');
    return Array.isArray(res.data) ? res.data : res.data.results || [];
  },

  adminUpdatePlan: async (id: number, data: Partial<PricingPlan>): Promise<PricingPlan> => {
    const res = await api.patch(`/billing/admin/plans/${id}/`, data);
    return res.data;
  },

  adminCreatePlan: async (data: Partial<PricingPlan>): Promise<PricingPlan> => {
    const res = await api.post('/billing/admin/plans/', data);
    return res.data;
  },

  // Admin Analytics
  adminGetOverview: async (): Promise<any> => {
    const res = await api.get('/billing/admin/analytics/');
    return res.data;
  },
  adminGetRevenue: async (): Promise<any> => {
    const res = await api.get('/billing/admin/analytics/revenue/');
    return res.data;
  },
  adminGetPlanBreakdown: async (): Promise<any> => {
    const res = await api.get('/billing/admin/analytics/plans/');
    return res.data;
  },
  adminGetCustomers: async (): Promise<any> => {
    const res = await api.get('/billing/admin/analytics/customers/');
    return res.data;
  },
  adminGetCosts: async (): Promise<any> => {
    const res = await api.get('/billing/admin/analytics/costs/');
    return res.data;
  }
};

// ─── Core API ───────────────────────────────────────────────────────────────

export interface DashboardOverview {
  services: { total: number; running: number; failed: number; stopped: number };
  deployments_this_month: number;
  addons: { total: number; active: number };
  cost_estimate: { monthly_usd: number; currency: string };
  resource_usage: {
    cpu_hours: number;
    memory_gb_hours: number;
    storage_gb: number;
    bandwidth_gb: number;
  };
  recent_activity: any[];
  alerts: any[];
}

export const coreApi = {
  getDashboardOverview: async (): Promise<DashboardOverview> => {
    const res = await api.get('/dashboard/overview/');
    return res.data;
  },

  // API Keys
  getApiKeys: async (): Promise<any[]> => {
    const res = await api.get('/api-keys/');
    return Array.isArray(res.data) ? res.data : res.data.results || [];
  },
  createApiKey: async (name: string): Promise<any> => {
    const res = await api.post('/api-keys/', { name });
    return res.data;
  },
  revokeApiKey: async (id: number): Promise<void> => {
    await api.delete(`/api-keys/${id}/`);
  },

  // Notifications
  getNotifications: async (): Promise<any[]> => {
    const res = await api.get('/notifications/notifications/');
    return Array.isArray(res.data) ? res.data : res.data.results || [];
  },
  markAllNotificationsRead: async (): Promise<void> => {
    await api.post('/notifications/notifications/mark_all_read/');
  },
  getNotificationPreferences: async (): Promise<any[]> => {
    const res = await api.get('/notifications/preferences/');
    return Array.isArray(res.data) ? res.data : res.data.results || [];
  },
  updateNotificationPreference: async (id: number, data: any): Promise<any> => {
    const res = await api.patch(`/notifications/preferences/${id}/`, data);
    return res.data;
  }
};

// ─── Addons API ─────────────────────────────────────────────────────────────

export interface Addon {
    id: string;
    name: string;
    addon_type: 'POSTGRES' | 'REDIS' | 'MONGODB' | 'MINIO';
    status: 'PROVISIONING' | 'RUNNING' | 'FAILED' | 'STOPPED';
    created_at: string;
    connection_url?: string;
    config: Record<string, any>;
}

export const addonsApi = {
    list: async (): Promise<Addon[]> => {
        const res = await api.get('/addons/');
        return Array.isArray(res.data) ? res.data : res.data.results || [];
    },
    get: async (id: string): Promise<Addon> => {
        const res = await api.get(`/addons/${id}/`);
        return res.data;
    },
    create: async (data: Partial<Addon>): Promise<Addon> => {
        const res = await api.post('/addons/', data);
        return res.data;
    },
    delete: async (id: string): Promise<void> => {
        await api.delete(`/addons/${id}/`);
    },
    rotateCredentials: async (id: string): Promise<{ connection_url: string }> => {
        const res = await api.post(`/addons/${id}/rotate-credentials/`);
        return res.data;
    },
    getMetrics: async (id: string): Promise<any> => {
        const res = await api.get(`/addons/${id}/metrics/`);
        return res.data;
    },
    runQuery: async (id: string, query: string): Promise<{ results: any[], columns: string[], error?: string }> => {
        const res = await api.post(`/addons/${id}/query/`, { query });
        return res.data;
    }
};

export default api;

// ── Projects API ────────────────────────────────────────────────────────

export const projectsApi = {
  list: async (): Promise<Project[]> => {
    const response = await api.get('/projects/');
    return Array.isArray(response.data) ? response.data : (response.data?.results || []);
  },
  get: async (id: string): Promise<Project> => {
    const response = await api.get(`/projects/${id}/`);
    return response.data;
  },
  create: async (data: { name: string; description?: string; icon_emoji?: string; color?: string }): Promise<Project> => {
    const response = await api.post('/projects/', data);
    return response.data;
  },
  update: async (id: string, data: Partial<Project>): Promise<Project> => {
    const response = await api.patch(`/projects/${id}/`, data);
    return response.data;
  },
  delete: async (id: string): Promise<void> => {
    await api.delete(`/projects/${id}/`);
  },
  services: async (id: string): Promise<Service[]> => {
    const response = await api.get(`/projects/${id}/services/`);
    return Array.isArray(response.data) ? response.data : (response.data?.results || []);
  },
  moveService: async (projectId: string, serviceId: string): Promise<void> => {
    await api.post(`/projects/${projectId}/move-service/`, { service_id: serviceId });
  },
  removeService: async (projectId: string, serviceId: string): Promise<void> => {
    await api.post(`/projects/${projectId}/remove-service/`, { service_id: serviceId });
  },
};
