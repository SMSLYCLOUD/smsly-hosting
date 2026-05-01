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

function isServerProxyUrl(url?: string): boolean {
  if (!url) return false;
  const cleanUrl = url.split('?')[0];
  return /\/servers\/[^/]+\/proxy\/?$/.test(cleanUrl);
}

function appendQuery(path: string, params: Record<string, any> | undefined): string {
  if (!params || typeof params !== 'object') return path;
  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null) return;
    if (Array.isArray(value)) {
      value.forEach((item) => {
        if (item !== undefined && item !== null) {
          searchParams.append(key, String(item));
        }
      });
      return;
    }
    searchParams.append(key, String(value));
  });
  const qs = searchParams.toString();
  if (!qs) return path;
  return path.includes('?') ? `${path}&${qs}` : `${path}?${qs}`;
}

const REMOTE_PROXY_FAIL_KEY = 'smsly_remote_proxy_failures';

function readRemoteProxyFailures(): Record<string, number> {
  if (typeof window === 'undefined') return {};
  try {
    const raw = localStorage.getItem(REMOTE_PROXY_FAIL_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writeRemoteProxyFailures(value: Record<string, number>) {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(REMOTE_PROXY_FAIL_KEY, JSON.stringify(value));
  } catch {
    // ignore storage errors
  }
}

function bumpRemoteProxyFailure(serverId: string): number {
  const current = readRemoteProxyFailures();
  const next = Number(current[serverId] || 0) + 1;
  current[serverId] = next;
  writeRemoteProxyFailures(current);
  return next;
}

function resetRemoteProxyFailure(serverId: string | null) {
  if (!serverId) return;
  const current = readRemoteProxyFailures();
  if (serverId in current) {
    delete current[serverId];
    writeRemoteProxyFailures(current);
  }
}

// ─── Remote Server Proxy Interceptor ────────────────────────────────────────
// When a remote server is selected (smsly_active_server in localStorage),
// automatically rewrite API calls to go through /servers/{id}/proxy/.
// Paths that should NEVER be proxied (they're always local):
const PROXY_BYPASS_PREFIXES = [
  '/auth/',
  '/servers/',
  '/transfers/',
  '/system/',
  '/teams/',
  '/licensing/',
  '/ai/',
  '/cloud/',
  '/mesh/',
  '/clusters/',
  '/replication/',
  '/platform-updates/',
];

api.interceptors.request.use((config) => {
  if (typeof window === 'undefined') return config;
  if ((config as any)?._skipRemoteProxy) return config;

  const activeServer = localStorage.getItem('smsly_active_server');
  if (!activeServer) return config;

  // Extract path from the URL relative to baseURL
  const url = config.url || '';
  // Only proxy /api/v1/ calls (relative paths like /services/ or absolute)
  const relPath = url.startsWith('/api/v1/') ? url.slice(7) : url; // strip /api/v1 prefix if absolute

  // Skip if it's a bypass path
  if (PROXY_BYPASS_PREFIXES.some(prefix => relPath.startsWith(prefix))) {
    return config;
  }

  // Skip if already going through proxy (prevent infinite loop)
  if (relPath.includes('/proxy/')) return config;

  // Rewrite: original method + path → POST to /servers/{id}/proxy/
  const originalMethod = (config.method || 'GET').toUpperCase();
  let originalPath = `/api/v1${relPath.startsWith('/') ? relPath : '/' + relPath}`;
  originalPath = appendQuery(originalPath, (config as any).params);
  const originalBody = config.data;

  config.method = 'post';
  config.url = `/servers/${activeServer}/proxy/`;
  // Query params are now embedded in originalPath; prevent axios from adding
  // them to the proxy endpoint itself.
  delete (config as any).params;
  config.data = {
    method: originalMethod,
    path: originalPath,
    body: originalBody || null,
  };

  // Mark this config so the response interceptor knows to unwrap it
  (config as any)._isProxied = true;

  return config;
});

// Response interceptor: unwrap proxy responses {status_code, data} → normal response
api.interceptors.response.use(
  (response) => {
    const shouldUnwrapProxyResponse = (
      (response.config as any)?._isProxied ||
      isServerProxyUrl(response.config?.url)
    ) && response.data?.status_code !== undefined;

    if (shouldUnwrapProxyResponse) {
      const proxyStatusCode = response.data.status_code;
      const proxyData = response.data.data;

      // Rewrite the response to look like a direct API call
      response.data = proxyData;
      response.status = proxyStatusCode;

      // If remote returned an error status, reject as an Axios error
      if (proxyStatusCode >= 400) {
        const error: any = new Error(`Remote server returned ${proxyStatusCode}`);
        error.response = { ...response, status: proxyStatusCode, data: proxyData };
        error.config = response.config;
        return Promise.reject(error);
      }

      // Successful remote response: clear failure counter for active server.
      if (typeof window !== 'undefined') {
        resetRemoteProxyFailure(localStorage.getItem('smsly_active_server'));
      }
    }
    return response;
  },
  (error) => {
    // If the proxy call itself failed (502, network error), provide a clear message
    const isProxyRequest = (error?.config as any)?._isProxied || isServerProxyUrl(error?.config?.url);
    if (isProxyRequest) {
      const statusCode = error?.response?.status;
      const isGatewayFailure = statusCode === 502 || statusCode === 503 || statusCode === 504 || !statusCode;
      if (isGatewayFailure && typeof window !== 'undefined') {
        const activeServer = localStorage.getItem('smsly_active_server');
        const msg = error?.response?.data?.error || 'Remote server is unreachable';
        if (activeServer) {
          const attempts = bumpRemoteProxyFailure(activeServer);
          if (attempts >= 3) {
            localStorage.removeItem('smsly_active_server');
            resetRemoteProxyFailure(activeServer);
            window.dispatchEvent(new CustomEvent('smsly:server-changed', { detail: null }));
            error.message = `${msg}. Switched to Local server after repeated remote failures.`;
          } else {
            error.message = `${msg} (${attempts}/3)`;
          }
        } else {
          error.message = msg;
        }
      }
    }
    return Promise.reject(error);
  }
);

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
      // Only clear stale credentials. Do NOT redirect here — the AuthProvider
      // is the single source of truth for login redirects. Having two redirect
      // paths (interceptor + AuthProvider) causes an infinite redirect loop.
      localStorage.removeItem('auth_token');
      clearAuthCookies();
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
  // Compose deployment
  deploy_mode?: 'SINGLE' | 'COMPOSE';
  compose_file?: string;
  compose_main_service?: string;
  // Domain visibility
  is_public?: boolean;
  public_domain_hidden?: boolean;
  node_metadata?: { id: string; name: string; host: string; status: string };
  estimated_cost?: {
    enabled: boolean;
    currency?: string;
    monthly?: number;
    basis?: string;
    confidence?: string;
    breakdown?: Record<string, any>;
  };
}

export interface AiRouterDetectedModel {
  service_id: string;
  service_name: string;
  public_domain: string;
  model: string;
  alias: string;
  api_base: string;
  mode: 'chat' | 'embedding';
  selected: boolean;
}

export interface AiRouterConfig {
  service_id: string;
  api_base: string;
  ui_base: string;
  braid_alias: string;
  braid_enabled: boolean;
  selected_service_ids: string[];
  detected_models: AiRouterDetectedModel[];
  config_preview: string;
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
  is_locked?: boolean;
  source?: 'USER' | 'ADDON' | 'SHORTCODE' | 'SYSTEM';
}

export interface CronJob {
    id: number;
    name: string;
    schedule: string;
    command: string;
    last_run_at?: string;
}

export interface Volume {
    id: string;
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
  create: async (data: any, requestConfig?: any): Promise<Service> => {
    // If it's a file upload, use FormData
    if (data instanceof FormData) {
      const response = await api.post('/services/', data, {
        ...(requestConfig || {}),
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      return response.data;
    }
    const response = await api.post('/services/', data, requestConfig);
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
  restart: async (id: string, forceRebuild: boolean = false): Promise<any> => {
    const response = await api.post(`/services/${id}/restart/`, { force_rebuild: forceRebuild });
    return response.data;
  },
  forceRebuild: async (id: string): Promise<any> => {
    const response = await api.post(`/services/${id}/restart/`, { force_rebuild: true });
    return response.data;
  },
  stop: async (id: string): Promise<any> => {
    const response = await api.post(`/services/${id}/stop/`);
    return response.data;
  },
  delete: async (id: string): Promise<void> => {
    await api.delete(`/services/${id}/`);
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
    const response = await api.post(`/deployments/${deploymentId}/rollback/`, { confirm: true });
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
  bulkCancelDeployments: async (deploymentIds: string[]): Promise<{ cancelled: number; message: string }> => {
    const response = await api.post('/deployments/bulk-cancel/', { deployment_ids: deploymentIds });
    return response.data;
  },
  promoteDeployment: async (deploymentId: string): Promise<any> => {
    const response = await api.post(`/deployments/${deploymentId}/promote/`);
    return response.data;
  },
  pruneDeployments: async (): Promise<{
    message: string;
    deployments_deleted: number;
    containers_removed: number;
    stale_queued_cancelled: number;
    space_reclaimed_mb: number;
  }> => {
    const response = await api.post('/deployments/prune/');
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
  upsertEnvVars: async (
    serviceId: string,
    vars: Array<{ key: string; value: string; is_secret?: boolean }>,
  ): Promise<{ added: number; updated: number; count: number; env_vars: EnvVar[] }> => {
    const response = await api.post(`/services/${serviceId}/env_vars/`, { vars });
    return {
      added: Number(response.data?.added || 0),
      updated: Number(response.data?.updated || 0),
      count: Number(response.data?.count || vars.length || 0),
      env_vars: Array.isArray(response.data?.env_vars) ? response.data.env_vars : [],
    };
  },
  deleteEnvVar: async (serviceId: string, envVarId: number): Promise<void> => {
    await api.delete(`/services/${serviceId}/env_vars/${envVarId}/`);
  },
  patchEnvVar: async (serviceId: string, envVarId: number, data: Partial<EnvVar>): Promise<EnvVar> => {
    const response = await api.patch(`/services/${serviceId}/env_vars/${envVarId}/`, data);
    return response.data;
  },
  getEnvVarValue: async (serviceId: string, envVarId: number): Promise<string> => {
    const response = await api.get(`/services/${serviceId}/env_vars/${envVarId}/`);
    return String(response.data?.value ?? '');
  },
  revealEnvVar: async (serviceId: string, key: string): Promise<{ value: string }> => {
    const vars = await servicesApi.getEnvVars(serviceId);
    const match = vars.find((v) => v.key === key);
    if (!match) throw new Error(`Environment variable not found: ${key}`);
    const value = await servicesApi.getEnvVarValue(serviceId, match.id);
    return { value };
  },
  getAiRouterConfig: async (serviceId: string): Promise<AiRouterConfig> => {
    const response = await api.get(`/services/${serviceId}/ai-router-config/`);
    return response.data;
  },
  saveAiRouterConfig: async (
    serviceId: string,
    data: Pick<AiRouterConfig, 'api_base' | 'ui_base' | 'braid_alias' | 'braid_enabled' | 'selected_service_ids'>,
  ): Promise<AiRouterConfig> => {
    const response = await api.post(`/services/${serviceId}/ai-router-config/`, data);
    return response.data;
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
  deleteVolume: async (serviceId: string, volId: string): Promise<void> => {
      await api.delete(`/services/${serviceId}/volumes/${volId}/`);
  },

  // Domain verification
  verifyDomain: async (serviceId: string, domain: string): Promise<{ domain: string; verified: boolean; cname_target: string; message: string }> => {
      const response = await api.post(`/services/${serviceId}/verify-domain/`, { domain });
      return response.data;
  },

  // Volume Browser
  browseVolume: async (serviceId: string, volumeId: string, path?: string): Promise<{ path: string; files: any[] }> => {
      const res = await api.get(`/services/${serviceId}/volumes/${volumeId}/browse/`, { params: { path } });
      return res.data;
  },
  deleteVolumeFile: async (serviceId: string, volumeId: string, path: string): Promise<void> => {
      await api.post(`/services/${serviceId}/volumes/${volumeId}/delete-file/`, { path });
  },
  createVolumeFolder: async (serviceId: string, volumeId: string, path: string): Promise<void> => {
      await api.post(`/services/${serviceId}/volumes/${volumeId}/mkdir/`, { path });
  },
  downloadVolumeFile: (serviceId: string, volumeId: string, path: string) => {
      const token = typeof window !== 'undefined' ? (localStorage.getItem('auth_token') || getAuthTokenFromCookie()) : null;
      const url = `${getApiUrl()}/services/${serviceId}/volumes/${volumeId}/download-file/?path=${encodeURIComponent(path)}&token=${token}`;
      window.open(url, '_blank');
  },
  browseFiles: async (serviceId: string, path: string = '/app'): Promise<{ path: string; files: any[] }> => {
      const response = await api.get(`/services/${serviceId}/file-browse/`, { params: { path } });
      return response.data;
  },
  readFile: async (serviceId: string, path: string): Promise<{ path: string; content: string }> => {
      const response = await api.get(`/services/${serviceId}/file-read/`, { params: { path } });
      return response.data;
  },
  writeFile: async (serviceId: string, path: string, content: string): Promise<{ message: string; path: string }> => {
      const response = await api.post(`/services/${serviceId}/file-write/`, { path, content });
      return response.data;
  },
  deleteFile: async (serviceId: string, path: string): Promise<void> => {
      await api.post(`/services/${serviceId}/file-delete/`, { path });
  },
  createFolder: async (serviceId: string, path: string): Promise<void> => {
      await api.post(`/services/${serviceId}/file-mkdir/`, { path });
  },
  downloadFile: (serviceId: string, path: string) => {
      const token = typeof window !== 'undefined' ? (localStorage.getItem('auth_token') || getAuthTokenFromCookie()) : null;
      const url = `${getApiUrl()}/services/${serviceId}/file-download/?path=${encodeURIComponent(path)}&token=${token}`;
      window.open(url, '_blank');
  }
};

export const platformApi = {
  resources: async (): Promise<any> => {
    const response = await api.get('/platform/resources/');
    return response.data;
  },
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
      _skipRemoteProxy: true,
    } as any);
    return response.data;
  },

  /** Update AI provider settings (admin only). */
  updateProviders: async (data: Record<string, string>): Promise<any> => {
    const response = await api.post('/ai/providers/update/', data, {
      _skipRemoteProxy: true,
    });
    return response.data;
  },

  /** Test AI with a prompt. Returns response + which provider/mode was used. */
  testPrompt: async (prompt: string, systemPrompt?: string): Promise<AITestResponse> => {
    const response = await api.post('/ai/test/', {
      prompt,
      system_prompt: systemPrompt,
    }, {
      _skipRemoteProxy: true,
    });
    return response.data;
  },

  /** Analyze logs with AI */
  analyzeLogs: async (logs: string, context: string = 'deployment'): Promise<{
    diagnosis: string;
    issues: { type: string; confidence: number; pattern: string }[];
    recommendations: string[];
    provider: string;
  }> => {
    const res = await api.post('/ai/analyze/', { logs, context }, {
      _skipRemoteProxy: true,
    });
    return res.data;
  },

  /** Get cost estimates with AI recommendations */
  costEstimate: async (config: {
    cpu_cores: number;
    memory_mb: number;
    stack?: string;
    provider?: string;
  }): Promise<{
    estimates: Record<string, number>;
    ai_recommendations: string;
  }> => {
    const res = await api.post('/ai/cost-estimate/', config, {
      _skipRemoteProxy: true,
    });
    return res.data;
  },

  /** Get latest intelligence report */
  getReport: async (): Promise<any> => {
    const res = await api.get('/ai/report/', {
      _skipRemoteProxy: true,
    } as any);
    return res.data;
  },

  /** Get anomaly detection history */
  getAnomalies: async (): Promise<{
    anomalies: {
      id: string;
      service_name: string;
      issue_type: string;
      severity: string;
      detected_at: string;
      auto_fixed: boolean;
      fix_result: string;
    }[];
  }> => {
    const res = await api.get('/ai/anomalies/', {
      _skipRemoteProxy: true,
    } as any);
    return res.data;
  },
};

// ─── Code Analysis API ──────────────────────────────────────────────────────

export const codeAnalysisApi = {
  /** Trigger async codebase analysis for a service */
  analyze: async (serviceId: string): Promise<{ task_id: string; status: string; service: string }> => {
    const res = await api.post('/cloud/code-analysis/analyze/', { service_id: serviceId }, {
      _skipRemoteProxy: true,
    });
    return res.data;
  },

  /** Poll for analysis results */
  getResult: async (taskId: string): Promise<{
    status: 'pending' | 'analyzing' | 'complete' | 'failed';
    data?: {
      nodes: { id: string; type: string; data: Record<string, any> }[];
      edges: { id: string; source: string; target: string; type: string; label?: string }[];
      tech_stack: string[];
      stats: { files: number; directories: number; lines: number; languages: Record<string, number> };
      summary: string;
    };
    error?: string;
  }> => {
    const res = await api.get(`/cloud/code-analysis/result/${taskId}/`, {
      _skipRemoteProxy: true,
    } as any);
    return res.data;
  },
};

// ─── Preview Environments API ───────────────────────────────────────────────

export interface PreviewEnvironment {
  id: string;
  name: string;
  branch: string;
  pr_number: number | null;
  preview_url: string;
  health_status: string;
  created_at: string;
  latest_deployment: {
    id: string;
    status: string;
    created_at: string;
  } | null;
}

export const previewApi = {
  /** Create a preview environment for a service */
  create: async (serviceId: string, branch: string, prNumber?: number): Promise<any> => {
    const res = await api.post(`/services/${serviceId}/create-preview/`, {
      branch,
      ...(prNumber ? { pr_number: prNumber } : {}),
    });
    return res.data;
  },

  /** List all previews for a service */
  list: async (serviceId: string): Promise<{ count: number; results: PreviewEnvironment[] }> => {
    const res = await api.get(`/services/${serviceId}/previews/`);
    return res.data;
  },

  /** Destroy a preview */
  destroy: async (serviceId: string, previewId: string): Promise<{ message: string }> => {
    const res = await api.delete(`/services/${serviceId}/destroy-preview/`, {
      data: { preview_id: previewId },
    });
    return res.data;
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
  has_ssh_credentials?: boolean;
  is_primary: boolean;
  status: 'ONLINE' | 'OFFLINE' | 'UNKNOWN';
  last_health_check: string | null;
  server_version: string;
  services_count: number;
  created_at: string;
}

const proxiedRequestConfig = (): any => ({ _isProxied: true });

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
    const res = await api.post(`/servers/${id}/proxy/`, { method, path, body }, proxiedRequestConfig());
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
    }, proxiedRequestConfig());
    return res.data;
  },
  remoteStopService: async (id: string, serviceId: string): Promise<any> => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/stop/`,
    }, proxiedRequestConfig());
    return res.data;
  },
  remoteRestartService: async (id: string, serviceId: string): Promise<any> => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/restart/`,
    }, proxiedRequestConfig());
    return res.data;
  },
  // Remote domain management via proxy
  remoteAddDomain: async (id: string, serviceId: string, domain: string): Promise<any> => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/add-domain/`, body: { domain },
    }, proxiedRequestConfig());
    return res.data;
  },
  remoteDeleteDomain: async (id: string, serviceId: string, domain: string): Promise<any> => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/delete-domain/`, body: { domain },
    }, proxiedRequestConfig());
    return res.data;
  },
  remoteVerifyDomain: async (id: string, serviceId: string, domain: string): Promise<any> => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/verify-domain/`, body: { domain },
    }, proxiedRequestConfig());
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
    includeLocal: boolean = true,
    requestConfig?: any,
  ): Promise<any> => {
    const res = await api.post(`/services/${serviceId}/multi-deploy/`, {
      ref,
      server_ids: serverIds,
      include_local: includeLocal,
    }, requestConfig);
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

function normalizeTunnel(raw: any): Tunnel {
  return {
    tunnel_id: String(raw?.tunnel_id ?? raw?.tunnelId ?? raw?.id ?? ''),
    subdomain: String(raw?.subdomain ?? ''),
    public_url: String(raw?.public_url ?? raw?.publicUrl ?? ''),
    local_port: Number(raw?.local_port ?? raw?.localPort ?? 0),
    type: raw?.type === 'tcp' ? 'tcp' : 'http',
    is_active: Boolean(raw?.is_active ?? raw?.isActive ?? true),
    created_at: String(raw?.created_at ?? raw?.createdAt ?? new Date().toISOString()),
    expires_at: raw?.expires_at ?? null,
    request_count: Number(raw?.request_count ?? raw?.requestCount ?? 0),
    bandwidth_used: Number(raw?.bandwidth_used ?? raw?.bandwidthUsed ?? 0),
    shared_with: Array.isArray(raw?.shared_with)
      ? raw.shared_with
      : (Array.isArray(raw?.sharedWith) ? raw.sharedWith : []),
    user_id: String(raw?.user_id ?? raw?.userId ?? ''),
    tier: String(raw?.tier ?? ''),
  };
}

function normalizeTunnelRequest(raw: any): TunnelRequest {
  return {
    id: String(raw?.id ?? ''),
    method: String(raw?.method ?? 'GET'),
    path: String(raw?.path ?? '/'),
    status: Number(raw?.status ?? 0),
    duration: Number(raw?.duration ?? raw?.response_time_ms ?? raw?.responseTimeMs ?? 0),
    timestamp: String(raw?.timestamp ?? new Date().toISOString()),
    headers: raw?.headers && typeof raw.headers === 'object' ? raw.headers : {},
    body: typeof raw?.body === 'string' ? raw.body : undefined,
    response_body: typeof raw?.response_body === 'string' ? raw.response_body : undefined,
  };
}

export const tunnelsApi = {
  /** List active tunnels for the current user */
  list: async (): Promise<Tunnel[]> => {
    const res = await api.get('/tunnels/');
    const rows = Array.isArray(res.data?.tunnels)
      ? res.data.tunnels
      : (Array.isArray(res.data) ? res.data : []);
    return rows.map(normalizeTunnel);
  },

  /** Create a new tunnel */
  create: async (data: { local_port: number; subdomain?: string; type?: string }): Promise<Tunnel> => {
    const res = await api.post('/tunnels/', data);
    return normalizeTunnel(res.data);
  },

  /** Get detail of a specific tunnel */
  get: async (id: string): Promise<Tunnel> => {
    const res = await api.get(`/tunnels/${id}/`);
    return normalizeTunnel(res.data);
  },

  /** Delete / close a tunnel */
  delete: async (id: string): Promise<void> => {
    await api.delete(`/tunnels/${id}/`);
  },

  /** Get request logs for a tunnel */
  requests: async (id: string): Promise<TunnelRequest[]> => {
    const res = await api.get(`/tunnels/${id}/requests/`);
    const rows = Array.isArray(res.data?.requests)
      ? res.data.requests
      : (Array.isArray(res.data) ? res.data : []);
    return rows.map(normalizeTunnelRequest);
  },

  /** Replay a request */
  replay: async (tunnelId: string, requestId: string): Promise<any> => {
    const res = await api.post(`/tunnels/${tunnelId}/replay/${requestId}/`);
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
  max_addons?: number;
  max_team_members?: number;
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

  // Admin Users Management
  adminGetUsers: async (): Promise<any[]> => {
    const res = await api.get('/admin/users/');
    return Array.isArray(res.data) ? res.data : res.data.results || [];
  },
  adminUpdateUser: async (id: number, data: any): Promise<any> => {
    const res = await api.patch(`/admin/users/${id}/`, data);
    return res.data;
  },

  // Notifications
  getNotifications: async (): Promise<any[]> => {
    const res = await api.get('/notifications/');
    return Array.isArray(res.data) ? res.data : res.data.results || [];
  },
  markAllNotificationsRead: async (): Promise<void> => {
    await api.post('/notifications/mark_all_read/');
  },
  getNotificationPreferences: async (): Promise<any[]> => {
    const res = await api.get('/preferences/');
    return Array.isArray(res.data) ? res.data : res.data.results || [];
  },
  updateNotificationPreference: async (id: number, data: any): Promise<any> => {
    const res = await api.patch(`/preferences/${id}/`, data);
    return res.data;
  }
};

// ─── Addons API ─────────────────────────────────────────────────────────────

export interface Addon {
    id: string;
    service: string;
    name: string;
    addon_type: string;
    status: 'PROVISIONING' | 'ACTIVE' | 'RUNNING' | 'FAILED' | 'STOPPED' | 'DELETED';
    created_at: string;
    connection_url?: string;
    public_domain?: string | null;
    is_bucket_public?: boolean;
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
    update: async (id: string, data: Partial<Addon>): Promise<Addon> => {
        const res = await api.patch(`/addons/${id}/`, data);
        return res.data;
    },
    delete: async (id: string): Promise<void> => {
        await api.delete(`/addons/${id}/`);
    },
    expose: async (id: string): Promise<any> => {
        const res = await api.post(`/addons/${id}/expose/`);
        return res.data;
    },
    deprovision: async (id: string): Promise<any> => {
        const res = await api.post(`/addons/${id}/deprovision/`);
        return res.data;
    },
    reprovision: async (id: string): Promise<any> => {
        const res = await api.post(`/addons/${id}/reprovision/`);
        return res.data;
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
    },
    addonCredentials: async (addonId: string): Promise<Record<string, string>> => {
      const res = await api.get(`/addons/${addonId}/credentials/`);
      return res.data;
    },
    statusCheck: async (addonId: string): Promise<any> => {
      const res = await api.get(`/addons/${addonId}/status_check/`);
      return res.data;
    },
    backup: async (addonId: string): Promise<any> => {
      const res = await api.post(`/addons/${addonId}/backup/`);
      return res.data;
    },
    restore: async (addonId: string, backupId: string): Promise<any> => {
      const res = await api.post(`/addons/${addonId}/restore/`, { backup_id: backupId });
      return res.data;
    },
    backups: async (addonId: string): Promise<any[]> => {
      const res = await api.get(`/addons/${addonId}/backups/`);
      return Array.isArray(res.data) ? res.data : [];
    },
    toggleBucketPublic: async (addonId: string, isPublic: boolean): Promise<any> => {
      const res = await api.post(`/addons/${addonId}/toggle_bucket_public/`, { is_public: isPublic });
      return res.data;
    },
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

// =============================================================================
// Autoscaler (VPS-level cross-service)
// =============================================================================

export interface AutoscalerService {
  type: 'gunicorn' | 'celery' | 'daphne';
  app: string;
  priority: number;
  status: string;
  demand_score: number;
  cpu_percent: number;
  memory_mb: number;
  memory_limit_mb: number;
  memory_percent: number;
  net_rx_mb: number;
  net_tx_mb: number;
  pids: number;
  current_workers: number;
  min_workers: number;
  max_workers: number;
  last_action: string;
  last_action_at: string;
}

export interface AutoscalerBudget {
  total_system_mb: number;
  infra_reserve_mb: number;
  app_budget_mb: number;
  used_mb: number;
  free_mb: number;
}

export interface AutoscalerStatus {
  status: string;
  uptime_seconds: number;
  check_interval: number;
  last_check_at: string;
  budget: AutoscalerBudget;
  services: Record<string, AutoscalerService>;
  recent_decisions: {
    timestamp: string;
    container: string;
    action: string;
    current_workers: number;
    target_workers: number;
    current_memory_mb: number;
    target_memory_mb: number;
    reason: string;
  }[];
}

export interface AutoscalerHistory {
  timestamps: string[];
  services: Record<string, {
    cpu: number[];
    memory_mb: number[];
    demand_score: number[];
    workers: number[];
  }>;
  budget: {
    used_mb: number[];
    free_mb: number[];
  };
}

export const autoscalerApi = {
  getStatus: async (): Promise<AutoscalerStatus> => {
    const { data } = await api.get('/autoscaler/status/');
    return data;
  },
  getHistory: async (minutes: number = 60): Promise<AutoscalerHistory> => {
    const { data } = await api.get('/autoscaler/history/', { params: { minutes } });
    return data;
  },
  updateConfig: async (config: any): Promise<any> => {
    const { data } = await api.post('/autoscaler/config/', config);
    return data;
  },
  trigger: async (): Promise<AutoscalerStatus> => {
    const { data } = await api.post('/autoscaler/trigger/');
    return data;
  },
};

// ─── Licensing API ──────────────────────────────────────────────────────────

export interface LicenseStatus {
  tier: 'community' | 'pro' | 'enterprise';
  is_valid: boolean;
  licensed_to?: string;
  expires_at: string | null;
  features: Record<string, boolean>;
  max_services: number;
  max_team_members: number;
}

export const licensingApi = {
  getStatus: async (): Promise<LicenseStatus> => {
    const { data } = await api.get('/licensing/status/');
    return data;
  },
  activate: async (license_key: string): Promise<any> => {
    const { data } = await api.post('/licensing/activate/', { license_key });
    return data;
  },
  deactivate: async (): Promise<any> => {
    const { data } = await api.post('/licensing/deactivate/');
    return data;
  },
};
