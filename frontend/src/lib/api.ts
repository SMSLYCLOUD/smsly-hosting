import axios from 'axios';
import { clearAuthCookies } from '@/lib/auth-cookies';

// Use dynamic origin detection - works in browser and during SSR
const getApiUrl = () => {
  if (typeof window !== 'undefined') {
    return `${window.location.origin}/api/v1`;
  }
  return process.env.NEXT_PUBLIC_API_URL || '/api/v1';
};

export const downloadBlob = (data: Blob | ArrayBuffer, path: string) => {
    const url = window.URL.createObjectURL(new Blob([data]));
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', path.split('/').pop() || 'file');
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
};

export interface ApiResponse<T = unknown> {
  data: T;
  status: number;
  statusText: string;
}

interface AxiosRequestConfigProxy {
  _skipRemoteProxy?: boolean;
  _isProxied?: boolean;
}

export const extractDataList = <T = unknown>(response: { data: unknown }): T[] => {
    const data = response.data;
    if (Array.isArray(data)) return data as T[];
    if (data && typeof data === 'object' && 'results' in data && Array.isArray((data as Record<string, unknown>).results)) {
      return (data as { results: unknown[] }).results as T[];
    }
    return [];
};

export const api = axios.create({
  baseURL: getApiUrl(),
  withCredentials: true,
  timeout: 30000,
});

function isServerProxyUrl(url?: string): boolean {
  if (!url) return false;
  const cleanUrl = url.split('?')[0];
  return /\/servers\/[^/]+\/proxy\/?$/.test(cleanUrl);
}

function appendQuery(path: string, params: Record<string, unknown> | undefined): string {
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
  '/cloud-storage/',
  '/mesh/',
  '/clusters/',
  '/replication/',
  '/platform-updates/',
  '/deployments/',
  '/addons/',
  '/mcp/',
];

// Sub-paths under /services/ that must always hit the local controller
// (deploy, deploy list, previews, etc.)
const PROXY_BYPASS_SUFFIX_PATTERNS = [
  '/deploy',
  '/deployments',
  '/multi-deploy',
  '/restart',
  '/stop',
  '/previews',
  '/create-preview',
  '/destroy-preview',
];

const LOCAL_DEPLOY_TARGETS = new Set(['', 'local', 'localhost', 'controller', 'master', 'primary']);

function normalizeDeployTarget(targetServerId?: string | null): { specified: boolean; value: string | null } {
  if (targetServerId === undefined) {
    return { specified: false, value: null };
  }
  if (targetServerId === null) {
    return { specified: true, value: null };
  }

  const raw = String(targetServerId).trim();
  if (LOCAL_DEPLOY_TARGETS.has(raw.toLowerCase())) {
    return { specified: true, value: null };
  }
  return { specified: true, value: raw };
}

api.interceptors.request.use((config) => {
  if (typeof window === 'undefined') return config;

  const activeTeamId = localStorage.getItem('smsly_active_team');
  if (activeTeamId && activeTeamId !== 'null' && activeTeamId !== 'undefined' && activeTeamId !== '') {
    if (!config.headers) {
      config.headers = {} as import('axios').AxiosHeaders;
    }
    (config.headers as Record<string, string>)['X-Team-ID'] = activeTeamId;
  }

  if ((config as unknown as AxiosRequestConfigProxy)?._skipRemoteProxy) return config;

  const activeServer = localStorage.getItem('smsly_active_server');
  if (!activeServer) return config;

  // Extract path from the URL relative to baseURL
  const url = config.url || '';
  // Only proxy /api/v1/ calls (relative paths like /services/ or absolute)
  const relPath = url.startsWith('/api/v1/') ? url.slice(7) : url; // strip /api/v1 prefix if absolute

  // Skip if it's a bypass path (exact prefix match)
  if (PROXY_BYPASS_PREFIXES.some(prefix => relPath.startsWith(prefix))) {
    return config;
  }

  // Skip if the service sub-path matches a bypass pattern (suffix match)
  // e.g. /services/{id}/deploy, /services/{id}/deployments, /services/{id}/previews
  if (PROXY_BYPASS_SUFFIX_PATTERNS.some(pattern => relPath.endsWith(pattern) || relPath.includes(pattern + '/'))) {
    return config;
  }

  // Skip if already going through proxy (prevent infinite loop)
  if (relPath.includes('/proxy/')) return config;

  // Rewrite: original method + path → POST to /servers/{id}/proxy/
  const originalMethod = (config.method || 'GET').toUpperCase();
  let originalPath = `/api/v1${relPath.startsWith('/') ? relPath : '/' + relPath}`;
  originalPath = appendQuery(originalPath, (config as unknown as { params?: Record<string, unknown> }).params);
  const originalBody = config.data;

  config.method = 'post';
  config.url = `/servers/${activeServer}/proxy/`;
  // Query params are now embedded in originalPath; prevent axios from adding
  // them to the proxy endpoint itself.
  delete (config as unknown as { params?: Record<string, unknown> }).params;
  config.data = {
    method: originalMethod,
    path: originalPath,
    body: originalBody || null,
  };

  // Mark this config so the response interceptor knows to unwrap it
  (config as unknown as AxiosRequestConfigProxy)._isProxied = true;

  return config;
});

// Response interceptor: unwrap proxy responses {status_code, data} → normal response
api.interceptors.response.use(
  (response) => {
    const shouldUnwrapProxyResponse = (
      (response.config as unknown as AxiosRequestConfigProxy)?._isProxied ||
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
        const error = new Error(`Remote server returned ${proxyStatusCode}`) as Error & { response?: ApiResponse; config?: unknown };
        error.response = { ...response, status: proxyStatusCode, data: proxyData } as ApiResponse;
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
    const isProxyRequest = (error?.config as unknown as AxiosRequestConfigProxy)?._isProxied || isServerProxyUrl(error?.config?.url);
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

// Auto-clear stale tokens on 401 and redirect to login
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const statusCode = error?.response?.status;
    const requestUrl = String(error?.config?.url || '');

    // Deploy actions intentionally return 409 when another deploy is active.
    if (
      statusCode === 409 &&
      /\/services\/[^/]+\/deploy\/?$/.test(requestUrl) &&
      error?.response
    ) {
      return Promise.resolve(error.response);
    }

    if (error.response?.status === 401 && typeof window !== 'undefined') {
      // Call backend logout to clear the HttpOnly __Host-auth_token cookie
      // via Set-Cookie: Max-Age=0.  Without this the cookie survives in the
      // browser, the middleware sees it on /login and redirects back to
      // /dashboard, creating an infinite loop.
      //
      // Do NOT redirect here — the auth-provider is the authority on auth
      // state and handles redirect.  Handling it in both places causes
      // double-fire (two logout calls, two redirects) and log spam from
      // revalidation polling.
      fetch('/api/v1/auth/logout/', { method: 'POST', credentials: 'include' }).catch(() => {});
      clearAuthCookies();
    }
    return Promise.reject(error);
  }
);

export interface Service {
  id: string;
  name: string;
  slug: string;
  status: 'ACTIVE' | 'DELETION_PENDING' | 'DELETION_FAILED' | 'UPDATING' | 'STOPPED';
  repository_url?: string;
  branch?: string;
  internal_port?: number;
  public_domain?: string;
  custom_domains?: string[];
  wildcard_url_enabled?: boolean;
  node_url_enabled?: boolean;
  wildcard_redirect_custom_domain?: boolean;
  wildcard_internal_only?: boolean;
  path_redirects?: { path: string; target: string }[];
  host_aliases?: { host: string; rewrite_root: string }[];
  node_url?: string | null;
  domain_instances?: { domain: string; verified: boolean }[];
  domain_verified?: boolean;
  verification_token?: string;
  staging_domain?: string;
  staging_domain_verified?: boolean;
  created_at?: string;
  cpu_cores?: number;
  memory_mb?: number;
  min_replicas?: number;
  max_replicas?: number;
  autoscale_cpu_target?: number;
  autoscale_enabled?: boolean;
  vpa_enabled?: boolean;
  buildpack?: 'NIXPACKS' | 'DOCKER' | 'STATIC';
  root_directory?: string;
  build_command?: string;
  deploy_type?: 'GIT' | 'DOCKER' | 'UPLOAD' | 'TEMPLATE' | 'FUNCTION';
  function_code?: string;
  function_runtime?: string;
  docker_image?: string;
  effective_registry?: string;
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
  server?: string | null;
  server_id?: string | null;
  project_name?: string | null;
  project_slug?: string | null;
  project_emoji?: string | null;
  latest_deployment?: {
    id: string;
    status: string;
    commit_hash?: string;
    created_at: string;
    target_server?: string | null;
    target_server_name?: string | null;
    target_is_local?: boolean;
  };
  // Compose deployment
  deploy_mode?: 'SINGLE' | 'COMPOSE';
  compose_file?: string;
  compose_main_service?: string;
  // Auto recovery
  auto_restart?: boolean;
  ha_mode?: 'none' | 'local' | 'remote';
  external_ha_endpoint?: string;
  external_ha_username?: string;
  external_ha_database?: string;
  auto_rollback_enabled?: boolean;
  auto_rollback_threshold?: number | null;
  // Domain visibility
  is_public?: boolean;
  public_domain_hidden?: boolean;
  // Environment scan depth
  env_scan_depth?: 'shallow' | 'standard' | 'deep';
  running_replicas?: number;
  node_metadata?: { id: string; name: string; host: string; status: string };
  estimated_cost?: {
    enabled: boolean;
    currency?: string;
    monthly?: number;
    basis?: string;
    confidence?: string;
    breakdown?: Record<string, any>;
  };
  // GitHub App
  watch_paths?: string[];
  bot_pr_strategy?: 'DEPLOY' | 'SKIP' | 'COMMENT_ONLY';
  last_pr_comment_id?: number;
  // Internal network (per-service)
  use_internal_network?: boolean;
  platform_internal_ip?: string | null;
  internal_addresses?: {
    network: string;
    ip: string;
    port: number;
    gateway?: string;
    aliases?: string[];
  }[];
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
  // Internal network
  internal_subnet?: string;
  internal_network_enabled?: boolean;
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
  staging_url?: string;
  staged_at?: string;
  created_at: string;
  finished_at?: string;
  is_rollback?: boolean;
  rollback_from?: string | null;
  target_server?: string | null;
  target_server_name?: string | null;
  target_is_local?: boolean;
}

export interface DeploymentRollbackResponse {
  id: string;
  service: string;
  commit_hash: string;
  commit_message?: string;
  status: string;
  is_rollback?: boolean;
  rollback_from?: string | null;
  rollback_state?: string;
  rollback_target?: string;
  created_at: string;
  finished_at?: string | null;
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
    return extractDataList(response);
  },
  create: async (data: Partial<Service> | FormData, requestConfig?: AxiosRequestConfigProxy): Promise<Service> => {
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
  deploy: async (id: string, ref: string = 'HEAD', targetServerId?: string | null) => {
    const body: Record<string, unknown> = { ref };
    const target = normalizeDeployTarget(targetServerId);
    if (target.specified) {
      body.target_server_id = target.value;
    }
    const response = await api.post(`/services/${id}/deploy/`, body, { _skipRemoteProxy: true });
    return response.data;
  },
  uploadDeploy: async (id: string, file: File, onUploadProgress?: (progressEvent: { loaded: number; total?: number; progress?: number }) => void): Promise<{ status: string; message?: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await api.post(`/services/${id}/upload-deploy/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress
    });
    return response.data;
  },
  restart: async (id: string, forceRebuild: boolean = false): Promise<{ status: string; message?: string }> => {
    const response = await api.post(`/services/${id}/restart/`, { force_rebuild: forceRebuild });
    return response.data;
  },
  triggerJulesFix: async (serviceId: string, deploymentId?: string): Promise<{ status: string; message?: string }> => {
    const body: Record<string, unknown> = {};
    if (deploymentId) body.deployment_id = deploymentId;
    const response = await api.post(`/services/${serviceId}/trigger-jules-fix/`, body);
    return response.data;
  },
  forceRebuild: async (id: string): Promise<{ status: string; message?: string }> => {
    const response = await api.post(`/services/${id}/restart/`, { force_rebuild: true });
    return response.data;
  },
  stop: async (id: string): Promise<{ status: string; message?: string }> => {
    const response = await api.post(`/services/${id}/stop/`);
    return response.data;
  },
  delete: async (id: string, force: boolean = false): Promise<void> => {
    const url = force ? `/services/${id}/?force=true` : `/services/${id}/`;
    await api.delete(url);
  },

  // Deployment Management
  getDeployments: async (serviceId: string): Promise<Deployment[]> => {
    const response = await api.get(`/services/${serviceId}/deployments/`);
    return extractDataList(response);
  },
  getDeployment: async (id: string): Promise<Deployment> => {
    const response = await api.get(`/deployments/${id}/`);
    return response.data;
  },
  getIncidentReport: async (serviceId: string) => {
    const response = await api.get(`/services/${serviceId}/incident-report/`, {
      _skipRemoteProxy: true,
    } as AxiosRequestConfigProxy);
    return response.data;
  },
  rollback: async (deploymentId: string): Promise<DeploymentRollbackResponse> => {
    const response = await api.post<DeploymentRollbackResponse>(
      `/deployments/${deploymentId}/rollback/`,
      { confirm: true },
    );
    return response.data;
  },
  cancelDeployment: async (deploymentId: string): Promise<{ status: string; message?: string }> => {
    const response = await api.post(`/deployments/${deploymentId}/cancel/`);
    return response.data;
  },
  approveDeployment: async (deploymentId: string, overrides?: {
    cpu_cores?: number;
    memory_mb?: number;
    env_overrides?: Record<string, string>;
  }): Promise<{ status: string; message?: string }> => {
    const response = await api.post(`/deployments/${deploymentId}/approve/`, overrides || {});
    return response.data;
  },
  bulkCancelDeployments: async (deploymentIds: string[]): Promise<{ cancelled: number; message: string }> => {
    const response = await api.post('/deployments/bulk-cancel/', { deployment_ids: deploymentIds });
    return response.data;
  },
  promoteDeployment: async (deploymentId: string): Promise<{ status: string; message?: string }> => {
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
    return extractDataList(response);
  },
  getPreviews: async (serviceId: string): Promise<PreviewEnvironment[]> => {
    const response = await api.get(`/services/${serviceId}/previews/`);
    return extractDataList(response);
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
  getMetrics: async (serviceId: string, duration: string = '1h') => {
    const response = await api.get(`/services/${serviceId}/metrics/`, { params: { duration } });
    return response.data;
  },
  // Traffic Geo
  getTrafficGeo: async (serviceId: string) => {
    const response = await api.get(`/services/${serviceId}/traffic-geo/`, {
      _skipRemoteProxy: true,
    } as AxiosRequestConfigProxy);
    return response.data;
  },
  recheckHealth: async (serviceId: string, reset_backoff: boolean = true): Promise<{ status: string; health_status?: string }> => {
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

  toggleWildcardUrl: async (serviceId: string, enabled: boolean): Promise<{ wildcard_url_enabled: boolean }> => {
      const response = await api.post(`/services/${serviceId}/toggle-wildcard-url/`, { enabled });
      return response.data;
  },

  toggleNodeUrl: async (serviceId: string, enabled: boolean): Promise<{ node_url_enabled: boolean }> => {
      const response = await api.post(`/services/${serviceId}/toggle-node-url/`, { enabled });
      return response.data;
  },

  // Volume Browser
  browseVolume: async (serviceId: string, volumeId: string, path?: string): Promise<{ path: string; files: { name: string; type: string; size?: number; modified?: string }[] }> => {
      const res = await api.get(`/services/${serviceId}/volumes/${volumeId}/browse/`, { params: { path } });
      return res.data;
  },
  deleteVolumeFile: async (serviceId: string, volumeId: string, path: string): Promise<void> => {
      await api.post(`/services/${serviceId}/volumes/${volumeId}/delete-file/`, { path });
  },
  createVolumeFolder: async (serviceId: string, volumeId: string, path: string): Promise<void> => {
      await api.post(`/services/${serviceId}/volumes/${volumeId}/mkdir/`, { path });
  },
  readVolumeFile: async (serviceId: string, volumeId: string, path: string): Promise<{ path: string; content: string }> => {
      const res = await api.get(`/services/${serviceId}/volumes/${volumeId}/file-read/`, { params: { path } });
      return res.data;
  },
  writeVolumeFile: async (serviceId: string, volumeId: string, path: string, content: string): Promise<{ message: string; path: string }> => {
      const res = await api.post(`/services/${serviceId}/volumes/${volumeId}/file-write/`, { path, content });
      return res.data;
  },
  downloadVolumeFile: async (serviceId: string, volumeId: string, path: string) => {
      const response = await api.get(`/services/${serviceId}/volumes/${volumeId}/download-file/`, {
          params: { path },
          responseType: 'blob',
      });
      downloadBlob(response.data, path);
  },
  uploadVolumeFile: async (serviceId: string, volumeId: string, path: string, file: File): Promise<{ message: string; path: string }> => {
      const formData = new FormData();
      formData.append('path', path);
      formData.append('file', file);
      const response = await api.post(`/services/${serviceId}/volumes/${volumeId}/upload/`, formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
      });
      return response.data;
  },
  browseFiles: async (serviceId: string, path: string = '/app'): Promise<{ path: string; files: { name: string; type: string; size?: number; modified?: string }[] }> => {
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
  downloadFile: async (serviceId: string, path: string) => {
      const response = await api.get(`/services/${serviceId}/file-download/`, {
          params: { path },
          responseType: 'blob',
      });
      downloadBlob(response.data, path);
  },
  uploadFile: async (serviceId: string, path: string, file: File): Promise<{ message: string; path: string }> => {
      const formData = new FormData();
      formData.append('path', path);
      formData.append('file', file);
      const response = await api.post(`/services/${serviceId}/file-upload/`, formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
      });
      return response.data;
  }
};

export const platformApi = {
  resources: async (): Promise<{ cpu_cores: number; ram_mb: number; disk_gb: number }> => {
    const response = await api.get('/platform/resources/');
    return response.data;
  },
};

export interface Blueprint {
  id: string;
  name: string;
  slug: string;
  description: string;
  category: string;
  icon: string;
  color: string;
  repository_url?: string;
  documentation_url?: string;
  tags?: string[];
  is_featured: boolean;
  is_official: boolean;
  min_resources?: { cpu_cores: number; memory_mb: number; storage_gb: number };
  config_schema?: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export const blueprintsApi = {
  list: async (): Promise<Blueprint[]> => {
    const response = await api.get('/blueprints/');
    return extractDataList(response);
  },
  get: async (id: string): Promise<Blueprint> => {
    const response = await api.get(`/blueprints/${id}/`);
    return response.data;
  },
  deploy: async (id: string, providerId?: string): Promise<{ id: string; status: string; message?: string }> => {
    const response = await api.post('/blueprints/deploy/', { blueprint_id: id, provider_id: providerId || "" });
    return response.data;
  },
};

export const templatesApi = {
  list: async (): Promise<Blueprint[]> => {
    const response = await api.get('/templates/');
    // Handle pagination if present, or raw list. Safely fallback to empty array.
    return extractDataList(response);
  },
  get: async (id: string): Promise<Blueprint> => {
    const response = await api.get(`/templates/${id}/`);
    return response.data;
  }
};

export const githubApi = {
  repos: async (params?: Record<string, unknown>): Promise<{ name: string; full_name: string; private: boolean }[]> => {
    const response = await api.get('/integrations/github/repos/', { params });
    return Array.isArray(response.data) ? response.data : response.data?.results || [];
  },
  branches: async (repo: string): Promise<{ name: string; commit?: { sha: string } }[]> => {
    const response = await api.get('/integrations/github/branches/', { params: { repo } });
    return Array.isArray(response.data) ? response.data : response.data?.results || [];
  },
  commits: async (repo: string, branch: string): Promise<{ sha: string; commit: { message: string; author?: { name: string; date: string } } }[]> => {
    const response = await api.get('/integrations/github/commits/', { params: { repo, branch } });
    return Array.isArray(response.data) ? response.data : response.data?.results || [];
  },
  defaultBranch: async (repo: string): Promise<string> => {
    const response = await api.get('/integrations/github/default-branch/', { params: { repo } });
    return response.data?.default_branch || 'main';
  },
};

export const gitlabApi = {
  repos: async (params?: Record<string, unknown>): Promise<{ name: string; path_with_namespace: string; visibility: string }[]> => {
    const response = await api.get('/integrations/gitlab/repos/', { params });
    return Array.isArray(response.data) ? response.data : response.data?.results || [];
  },
  branches: async (repo: string): Promise<{ name: string; commit?: { id: string } }[]> => {
    const response = await api.get('/integrations/gitlab/branches/', { params: { repo } });
    return Array.isArray(response.data) ? response.data : response.data?.results || [];
  },
  commits: async (repo: string, branch: string): Promise<{ id: string; title: string; author_name?: string; authored_date?: string }[]> => {
    const response = await api.get('/integrations/gitlab/commits/', { params: { repo, branch } });
    return Array.isArray(response.data) ? response.data : response.data?.results || [];
  }
};

export const bitbucketApi = {
  repos: async (params?: Record<string, unknown>): Promise<{ name: string; full_name: string; is_private: boolean }[]> => {
    const response = await api.get('/integrations/bitbucket/repos/', { params });
    return Array.isArray(response.data) ? response.data : response.data?.results || [];
  },
  branches: async (repo: string): Promise<{ name: string; target?: { hash: string } }[]> => {
    const response = await api.get('/integrations/bitbucket/branches/', { params: { repo } });
    return Array.isArray(response.data) ? response.data : response.data?.results || [];
  },
  commits: async (repo: string, branch: string): Promise<{ hash: string; message: string; author?: { raw: string } }[]> => {
    const response = await api.get('/integrations/bitbucket/commits/', { params: { repo, branch } });
    return Array.isArray(response.data) ? response.data : response.data?.results || [];
  }
};

export const systemApi = {
  health: async (): Promise<{ status: string }> => {
    const response = await api.get('/system/health/');
    return response.data;
  },
  
  routeRecheck: (): Promise<{ status: string; message: string }> =>
    api.post('/system/route-recheck/').then(r => r.data),

  resources: async (): Promise<{ cpu_cores: number; ram_mb: number; swap_mb: number }> => {
    const response = await api.get('/system/resources/');
    return response.data;
  },

  config: async (): Promise<{ ALLOW_REGISTRATION: boolean; require_email_verification: boolean }> => {
    const response = await api.get('/system/config/');
    return response.data;
  },
  getConfig: async (): Promise<{ ALLOW_REGISTRATION: boolean; require_email_verification: boolean; [key: string]: unknown }> => {
    const response = await api.get('/system/config/');
    return response.data;
  },
  updateConfig: async (data: Record<string, unknown>): Promise<Record<string, unknown>> => {
    const response = await api.patch('/system/config/', data);
    return response.data;
  },
  runMaintenance: async (action: 'clear' | 'refresh' | 'update' | 'registry_gc' | 'build_cache') => {
    const response = await api.post('/system/config/', { action });
    return response.data;
  },
  getMaintenanceTask: async (taskId: string) => {
    const response = await api.get('/system/config/', {
      params: { maintenance_task_id: taskId },
    });
    return response.data;
  },
  getPlatformUpdate: async (updateId: string) => {
    const response = await api.get(`/platform-updates/${updateId}/`);
    return response.data;
  },
  getDomainConfig: async () => {
    const response = await api.get('/system/domain-config/');
    return response.data;
  },
  updateDomainConfig: async (data: Record<string, unknown>) => {
    const response = await api.put('/system/domain-config/', data);
    return response.data;
  },
  toggleDbHa: async (enabled: boolean) => {
    const response = await api.post('/system/db-ha-toggle/', { enabled });
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
  base_url?: string;
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
    } as AxiosRequestConfigProxy);
    return response.data;
  },

  /** Update AI provider settings (admin only). */
  updateProviders: async (data: Record<string, string>): Promise<{ status: string; message?: string }> => {
    const response = await api.post('/ai/providers/update/', data, {
      _skipRemoteProxy: true,
    });
    return response.data;
  },

  /** Fetch available models from a provider's /v1/models endpoint. */
  fetchModels: async (providerId: string, apiKey?: string, baseUrl?: string): Promise<{ models: string[] }> => {
    const response = await api.post('/ai/providers/fetch-models/', {
      provider_id: providerId,
      api_key: apiKey || '',
      base_url: baseUrl || '',
    }, {
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
  getReport: async (): Promise<{ summary: string; issues?: string[]; recommendations?: string[] }> => {
    const res = await api.get('/ai/report/', {
      _skipRemoteProxy: true,
    } as AxiosRequestConfigProxy);
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
    } as AxiosRequestConfigProxy);
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
    } as AxiosRequestConfigProxy);
    return res.data;
  },
};

// ─── Preview Environments API ───────────────────────────────────────────────

export interface PreviewEnvironment {
  id: string;
  service?: string;
  name?: string;
  branch?: string;
  branch_name?: string;
  commit_sha?: string;
  pr_number?: number | null;
  preview_url?: string;
  health_status?: string;
  status?: string;
  migration_validation?: Record<string, any>;
  created_at: string;
  updated_at?: string;
  latest_deployment?: {
    id: string;
    status: string;
    created_at: string;
  } | null;
}

export const previewApi = {
  /** Create a preview environment for a service */
  create: async (serviceId: string, branch: string, prNumber?: number): Promise<PreviewEnvironment> => {
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
    return extractDataList(response);
  },
  inviteMember: async (teamId: string, email: string, role: string): Promise<{ message: string }> => {
    const response = await api.post(`/teams/${teamId}/invite_member/`, { email, role });
    return response.data;
  },
  removeMember: async (teamId: string, userId: number): Promise<{ message: string }> => {
    const response = await api.post(`/teams/${teamId}/remove_member/`, { user_id: userId });
    return response.data;
  },
};

// ─── Deployment Approvals API ───────────────────────────────────────────────

export interface DeploymentApproval {
  id: string;
  requester: string;
  requested_at: string;
  status: 'pending' | 'approved' | 'rejected';
  environment?: string;
  reason?: string;
  approved_at?: string;
  rejected_at?: string;
  approved_by?: string;
  rejected_by?: string;
}

export const deploymentApprovalApi = {
  list: (serviceId: string): Promise<DeploymentApproval[]> =>
    api.get(`/services/${serviceId}/approvals/`).then(r => {
      const data = r.data;
      if (Array.isArray(data)) return data;
      if (data && Array.isArray(data.results)) return data.results;
      return [];
    }),
  create: (serviceId: string, data: Partial<DeploymentApproval>): Promise<DeploymentApproval> =>
    api.post(`/services/${serviceId}/approvals/`, data).then(r => r.data),
  approve: (serviceId: string, approvalId: string): Promise<DeploymentApproval> =>
    api.post(`/services/${serviceId}/approvals/${approvalId}/approve/`).then(r => r.data),
  reject: (serviceId: string, approvalId: string, reason?: string): Promise<DeploymentApproval> =>
    api.post(`/services/${serviceId}/approvals/${approvalId}/reject/`, { reason }).then(r => r.data),
};

// ─── Servers API ────────────────────────────────────────────────────────────

export type ServerStatus = 'ONLINE' | 'OFFLINE' | 'UNKNOWN' | 'DEGRADED';
export type ProvisionStatus = 'NONE' | 'PENDING' | 'PROVISIONING' | 'DONE' | 'FAILED';
export type ServerRole = 'LEADER' | 'FOLLOWER' | 'CANDIDATE';

export interface ManagedServerRuntimeInfo {
  node_id?: string;
  ts?: string;
  platform?: string;
  python?: string;
  docker_version?: string;
  smsly_images?: Array<{ repo: string; tag: string; id: string; size: string }>;
  host_uptime_s?: number;
  disk_used_pct?: number;
  mem_used_pct?: number;
  registrar_version?: string;
}

export interface ManagedServer {
  id: string;
  name: string;
  host: string;
  private_ip?: string | null;
  api_url: string;
  api_token?: string;
  ssh_port: number;
  ssh_user?: string;
  provider_metadata?: Record<string, any>;
  has_ssh_credentials?: boolean;
  is_primary: boolean;
  is_lite_agent?: boolean;
  node_type?: 'master' | 'node' | 'agent-lite' | 'media';
  node_components?: { observability: boolean; security: boolean; crowdsec: boolean; falco: boolean; spire: boolean; log_shipping: boolean };
  allow_user_workloads: boolean;
  status: 'ONLINE' | 'OFFLINE' | 'UNKNOWN' | 'DEGRADED';
  last_health_check: string | null;
  server_version: string;
  services_count: number;
  provision_status?: 'NONE' | 'PENDING' | 'PROVISIONING' | 'DONE' | 'FAILED';
  provision_logs?: string;
  created_at: string;
  // Agent self-registration signals. The registrar (a small
  // service inside the agent's docker-compose stack) reports
  // these to the master. See
  // backend/apps/deployments/views_servers.py:agent_ready for
  // the server-side handler.
  agent_ready?: boolean;
  last_agent_heartbeat_at?: string | null;
  agent_runtime_info?: ManagedServerRuntimeInfo;
  // WireGuard / mesh
  wg_address?: string | null;
  role?: 'LEADER' | 'FOLLOWER' | 'CANDIDATE';
  // TLS pinning
  verify_tls?: boolean;
  tls_cert_sha256_set?: boolean;
  // Node domain
  node_number?: number | null;
  node_domain?: string | null;
}

const proxiedRequestConfig = (): AxiosRequestConfigProxy => ({ _isProxied: true });

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
  proxy: async (id: string, method: string, path: string, body?: Record<string, unknown>): Promise<unknown> => {
    const res = await api.post(`/servers/${id}/proxy/`, { method, path, body }, proxiedRequestConfig());
    return res.data;
  },
  remoteServices: async (id: string) => {
    const res = await api.get(`/servers/${id}/services/`);
    return res.data;
  },
  remoteDeployments: async (id: string) => {
    const res = await api.get(`/servers/${id}/deployments/`);
    return res.data;
  },
  remoteDomains: async (id: string) => {
    const res = await api.get(`/servers/${id}/domains/`);
    return res.data;
  },
  // Remote service management via proxy
  remoteDeployService: async (id: string, serviceId: string, ref: string = 'HEAD'): Promise<{ status: string; message?: string }> => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/deploy/`, body: { ref },
    }, proxiedRequestConfig());
    return res.data;
  },
  remoteStopService: async (id: string, serviceId: string): Promise<{ status: string; message?: string }> => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/stop/`,
    }, proxiedRequestConfig());
    return res.data;
  },
  remoteRestartService: async (id: string, serviceId: string): Promise<{ status: string; message?: string }> => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/restart/`,
    }, proxiedRequestConfig());
    return res.data;
  },
  // Remote domain management via proxy
  remoteAddDomain: async (id: string, serviceId: string, domain: string) => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/add-domain/`, body: { domain },
    }, proxiedRequestConfig());
    return res.data;
  },
  remoteDeleteDomain: async (id: string, serviceId: string, domain: string) => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/delete-domain/`, body: { domain },
    }, proxiedRequestConfig());
    return res.data;
  },
  remoteVerifyDomain: async (id: string, serviceId: string, domain: string) => {
    const res = await api.post(`/servers/${id}/proxy/`, {
      method: 'POST', path: `/api/v1/services/${serviceId}/verify-domain/`, body: { domain },
    }, proxiedRequestConfig());
    return res.data;
  },
  provision: async (data: Record<string, unknown>): Promise<{ status: string; message?: string }> => {
    const res = await api.post('/servers/provision/', data);
    return res.data;
  },
  provisionLogs: async (id: string) => {
    const res = await api.get(`/servers/${id}/provision-logs/`);
    return res.data;
  },
  updateServer: async (id: string) => {
    const res = await api.post(`/servers/${id}/update-server/`);
    return res.data;
  },
  runDiagnostics: (id: string) => api.post(`/servers/${id}/diagnostics/`),
  triggerHealing: (id: string, payload?: Record<string, unknown>) => api.post(`/servers/${id}/heal/`, payload || {}),
  getIncidentReport: async (serverId: string) => {
    const response = await api.get(`/servers/${serverId}/incident-report/`, {
      _skipRemoteProxy: true,
    } as AxiosRequestConfigProxy);
    return response.data;
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
    requestConfig?: AxiosRequestConfigProxy,
    registry?: { url?: string; username?: string; password?: string },
  ): Promise<{ status: string; message?: string; task_id?: string }> => {
    const payload: Record<string, unknown> = {
      ref,
      server_ids: serverIds,
      include_local: includeLocal,
    };
    if (registry?.url) {
      payload.registry_url = registry.url;
      if (registry.username) payload.registry_username = registry.username;
      if (registry.password) payload.registry_password = registry.password;
    }
    const res = await api.post(`/services/${serviceId}/multi-deploy/`, payload, requestConfig);
    return res.data;
  },
  agentReady: async (id: string): Promise<{ status: string; message?: string }> => {
    const res = await api.post(`/servers/${id}/agent-ready/`);
    return res.data;
  },
  agentHeartbeat: async (id: string, payload?: Record<string, unknown>): Promise<{ status: string; message?: string }> => {
    const res = await api.post(`/servers/${id}/agent-heartbeat/`, payload || {});
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

function normalizeTunnel(raw: Record<string, unknown>): Tunnel {
  return {
    tunnel_id: String(raw?.tunnel_id ?? raw?.tunnelId ?? raw?.id ?? ''),
    subdomain: String(raw?.subdomain ?? ''),
    public_url: String(raw?.public_url ?? raw?.publicUrl ?? ''),
    local_port: Number(raw?.local_port ?? raw?.localPort ?? 0),
    type: raw?.type === 'tcp' ? 'tcp' : 'http',
    is_active: Boolean(raw?.is_active ?? raw?.isActive ?? true),
    created_at: String(raw?.created_at ?? raw?.createdAt ?? new Date().toISOString()),
    expires_at: (raw?.expires_at as string | null) ?? null,
    request_count: Number(raw?.request_count ?? raw?.requestCount ?? 0),
    bandwidth_used: Number(raw?.bandwidth_used ?? raw?.bandwidthUsed ?? 0),
    shared_with: Array.isArray(raw?.shared_with)
      ? (raw.shared_with as string[])
      : (Array.isArray(raw?.sharedWith) ? (raw.sharedWith as string[]) : []),
    user_id: String(raw?.user_id ?? raw?.userId ?? ''),
    tier: String(raw?.tier ?? ''),
  };
}

function normalizeTunnelRequest(raw: Record<string, unknown>): TunnelRequest {
  return {
    id: String(raw?.id ?? ''),
    method: String(raw?.method ?? 'GET'),
    path: String(raw?.path ?? '/'),
    status: Number(raw?.status ?? 0),
    duration: Number(raw?.duration ?? raw?.response_time_ms ?? raw?.responseTimeMs ?? 0),
    timestamp: String(raw?.timestamp ?? new Date().toISOString()),
    headers: raw?.headers && typeof raw.headers === 'object' ? (raw.headers as Record<string, string>) : {},
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
  replay: async (tunnelId: string, requestId: string): Promise<{ status: string; message?: string }> => {
    const res = await api.post(`/tunnels/${tunnelId}/replay/${requestId}/`);
    return res.data;
  },

  /** Share tunnel with team member */
  share: async (tunnelId: string, email: string): Promise<{ message: string }> => {
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

export interface ResourcePrice {
  id: number;
  resource_type: string;
  name: string;
  description: string;
  price_per_unit: number;
  unit: string;
  currency: string;
  is_active: boolean;
  tier: string;
  created_at?: string;
  updated_at?: string;
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

  subscribe: async (planId: number, cycle: 'MONTHLY' | 'YEARLY'): Promise<{ status: string; message?: string }> => {
    // This is a placeholder for the actual subscribe flow
    const res = await api.post('/billing/subscription/subscribe/', { plan_id: planId, cycle });
    return res.data;
  },

  cancelSubscription: async (): Promise<{ status: string; message?: string }> => {
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
  adminGetOverview: async (): Promise<Record<string, unknown>[]> => {
    const res = await api.get('/billing/admin/analytics/');
    return res.data;
  },
  adminGetRevenue: async (): Promise<Record<string, unknown>[]> => {
    const res = await api.get('/billing/admin/analytics/revenue/');
    return res.data;
  },
  adminGetPlanBreakdown: async (): Promise<Record<string, unknown>[]> => {
    const res = await api.get('/billing/admin/analytics/plans/');
    return res.data;
  },
  adminGetCustomers: async (): Promise<Record<string, unknown>[]> => {
    const res = await api.get('/billing/admin/analytics/customers/');
    return res.data;
  },
  adminGetCosts: async () => {
    const res = await api.get('/billing/admin/analytics/costs/');
    return res.data;
  },

};

export const resourcePriceApi = {
  list: (params?: Record<string, unknown>): Promise<ResourcePrice[] | { results: ResourcePrice[] }> => api.get('/billing/admin/resource-prices/', { params }).then(r => r.data),
  create: (data: Partial<ResourcePrice>): Promise<ResourcePrice> => api.post('/billing/admin/resource-prices/', data).then(r => r.data),
  detail: (id: string): Promise<ResourcePrice> => api.get(`/billing/admin/resource-prices/${id}/`).then(r => r.data),
  update: (id: string, data: Partial<ResourcePrice>): Promise<ResourcePrice> => api.put(`/billing/admin/resource-prices/${id}/`, data).then(r => r.data),
  delete: (id: string): Promise<void> => api.delete(`/billing/admin/resource-prices/${id}/`).then(() => undefined),
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
  system_usage: {
    ram_used_mb: number;
    ram_total_mb: number;
    storage_used_gb: number;
    storage_total_gb: number;
    cpu_percent: number;
  };
  recent_activity: Record<string, unknown>[];
  alerts: Record<string, unknown>[];
}

export const coreApi = {
  getDashboardOverview: async (): Promise<DashboardOverview> => {
    const res = await api.get('/dashboard/overview/');
    return res.data;
  },

  // API Keys
  getApiKeys: async (): Promise<{ id: number; name: string; prefix: string; created_at: string; last_used_at: string | null }[]> => {
    const res = await api.get('/api-keys/');
    return Array.isArray(res.data) ? res.data : res.data.results || [];
  },
  createApiKey: async (name: string): Promise<{ id: number; name: string; key: string; prefix: string }> => {
    const res = await api.post('/api-keys/', { name });
    return res.data;
  },
  revokeApiKey: async (id: number): Promise<void> => {
    await api.delete(`/api-keys/${id}/`);
  },

  // Admin Users Management
  adminGetUsers: async (): Promise<{ id: number; username: string; email: string; is_active: boolean; is_staff: boolean }[]> => {
    const res = await api.get('/admin/users/');
    return Array.isArray(res.data) ? res.data : res.data.results || [];
  },
  adminUpdateUser: async (id: number, data: Record<string, unknown>): Promise<Record<string, unknown>> => {
    const res = await api.patch(`/admin/users/${id}/`, data);
    return res.data;
  },

  // Notifications
  getNotifications: async (): Promise<{ id: string; message: string; read: boolean; created_at: string }[]> => {
    const res = await api.get('/notifications/');
    return Array.isArray(res.data) ? res.data : res.data.results || [];
  },
  markAllNotificationsRead: async (): Promise<void> => {
    await api.post('/notifications/mark_all_read/');
  },
  getNotificationPreferences: async (): Promise<{ id: number; key: string; value: boolean }[]> => {
    const res = await api.get('/preferences/');
    return Array.isArray(res.data) ? res.data : res.data.results || [];
  },
  updateNotificationPreference: async (id: number, data: Record<string, unknown>): Promise<Record<string, unknown>> => {
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
    ha_enabled?: boolean;
    ha_status?: string;
    replica_container_name?: string;
    ha_topology?: Record<string, any>;
}

export const backupsApi = {
  importKey: async (scope: 'service' | 'server', payload: { key_id: string; key_material: string; label?: string }): Promise<{ key_id: string; fingerprint: string; source: string; created: boolean }> => {
    const prefix = scope === 'server' ? '/server/backups' : '/backups';
    const res = await api.post(`${prefix}/import-key/`, payload);
    return res.data;
  },
  getHeader: async (scope: 'service' | 'server', backupId: string): Promise<{ magic: string; key_id: string; fingerprint: string }> => {
    const prefix = scope === 'server' ? '/server/backups' : '/backups';
    const res = await api.get(`${prefix}/${backupId}/header/`);
    return res.data;
  },
  list: async (scope: 'service' | 'server'): Promise<any[]> => {
    const prefix = scope === 'server' ? '/server/backups' : '/backups';
    const res = await api.get(`${prefix}/`);
    return Array.isArray(res.data) ? res.data : (res.data?.results || []);
  },
  listKeys: async (scope: 'service' | 'server'): Promise<any[]> => {
    const prefix = scope === 'server' ? '/server/backups' : '/backups';
    const res = await api.get(`${prefix}/list-keys/`);
    return Array.isArray(res.data) ? res.data : [];
  },
  deleteKey: async (scope: 'service' | 'server', keyId: string): Promise<any> => {
    const prefix = scope === 'server' ? '/server/backups' : '/backups';
    const res = await api.post(`${prefix}/delete-key/`, { id: keyId });
    return res.data;
  },
};

export interface AddonHaStatus {
    ha_enabled: boolean;
    ha_status: string;
    mode: string;
    topology: Record<string, any>;
    master_container: string | null;
}

export interface AddonHaEnableResponse {
    status: string;
    mode: string;
    topology: Record<string, any>;
    warning?: string;
}

export const addonsApi = {
  togglePublicBucket: async (id: string) => { const response = await api.post(`/addons/${id}/toggle_bucket_public/`); return response.data; },

    list: async (): Promise<Addon[]> => {
        const res = await api.get('/addons/');
        const data = res.data;
        if (Array.isArray(data)) return data;
        if (data && Array.isArray(data.results)) return data.results;
        return [];
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
    expose: async (id: string): Promise<{ status: string; message?: string }> => {
        const res = await api.post(`/addons/${id}/expose/`);
        return res.data;
    },
    deprovision: async (id: string): Promise<{ status: string; message?: string }> => {
        const res = await api.post(`/addons/${id}/deprovision/`);
        return res.data;
    },
    enableHa: async (id: string, opts?: { placement?: 'local' | 'remote'; server_id?: string }): Promise<AddonHaEnableResponse> => {
        const res = await api.post(`/addons/${id}/enable-ha/`, opts ?? {});
        return res.data;
    },
    promoteHa: async (id: string): Promise<Record<string, any>> => {
        const res = await api.post(`/addons/${id}/promote-ha/`);
        return res.data;
    },
    disableHa: async (id: string): Promise<{ status: string; removed: string[] }> => {
        const res = await api.post(`/addons/${id}/disable-ha/`);
        return res.data;
    },
    haStatus: async (id: string): Promise<AddonHaStatus> => {
        const res = await api.get(`/addons/${id}/ha-status/`);
        return res.data;
    },
    retryDelete: async (id: string): Promise<{ status: string; message?: string }> => {
        const res = await api.post(`/addons/${id}/retry-delete/`);
        return res.data;
    },
    reprovision: async (id: string): Promise<{ status: string; message?: string }> => {
        const res = await api.post(`/addons/${id}/reprovision/`);
        return res.data;
    },
    rotateCredentials: async (id: string): Promise<{ connection_url: string }> => {
        const res = await api.post(`/addons/${id}/rotate-credentials/`);
        return res.data;
    },
    getMetrics: async (id: string): Promise<{ cpu: number; memory: number; timestamp: string }[]> => {
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
    statusCheck: async (addonId: string): Promise<{ status: string; message?: string }> => {
      const res = await api.get(`/addons/${addonId}/status_check/`);
      return res.data;
    },
    backup: async (addonId: string): Promise<{ status: string; backup_id?: string }> => {
      const res = await api.post(`/addons/${addonId}/backup/`);
      return res.data;
    },
    restore: async (addonId: string, backupId: string): Promise<{ status: string; message?: string }> => {
      const res = await api.post(`/addons/${addonId}/restore/`, { backup_id: backupId });
      return res.data;
    },
    backups: async (addonId: string) => {
      const res = await api.get(`/addons/${addonId}/backups/`);
      return Array.isArray(res.data) ? res.data : [];
    },
    toggleBucketPublic: async (addonId: string, isPublic: boolean): Promise<{ is_public: boolean }> => {
      const res = await api.post(`/addons/${addonId}/toggle_bucket_public/`, { is_public: isPublic });
      return res.data;
    },
    getLogs: async (addonId: string, tail: number = 200): Promise<{ id: string; addon_type: string; container_name: string; status: string; logs: string; message?: string }> => {
      const res = await api.get(`/addons/${addonId}/logs/?tail=${tail}`);
      return res.data;
    },
};

export const addonMaintenanceApi = {
  tables: (addonId: string) => api.get(`/addons/maintenance/${addonId}/tables/`).then(r => r.data),
  query: (addonId: string, query: string) => api.post(`/addons/maintenance/${addonId}/query/`, { sql: query }).then(r => r.data),
  stats: (addonId: string) => api.get(`/addons/maintenance/${addonId}/stats/`).then(r => r.data),
  vacuum: (addonId: string) => api.post(`/addons/maintenance/${addonId}/vacuum/`).then(r => r.data),
  rotateCredentials: (addonId: string) => api.post(`/addons/maintenance/${addonId}/rotate-credentials/`).then(r => r.data),
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
  syncEnvs: async (id: string): Promise<{ synced: number; message?: string }> => {
    const response = await api.post(`/projects/${id}/sync-envs/`);
    return response.data;
  },
  getInternalNetwork: async (id: string): Promise<{
    status: string;
    exists: boolean;
    network_name: string;
    subnet: string;
    isolated: boolean;
    services_running: number;
    services_attached: number;
  }> => {
    const response = await api.get(`/projects/${id}/internal-network/`);
    return response.data;
  },
  provisionInternalNetwork: async (id: string, body?: { dual_platform?: boolean }): Promise<{
    status: string;
    exists: boolean;
    network_name: string;
    subnet: string;
    isolated: boolean;
    services_running: number;
    services_attached: number;
  }> => {
    const response = await api.post(`/projects/${id}/internal-network/`, body || {});
    return response.data;
  },
};

export interface ProjectMember {
  id: string;
  user: number;
  username: string;
  email: string;
  role: 'ADMIN' | 'MEMBER' | 'VIEWER';
  permissions: string[];
  expires_at: string | null;
  joined_at: string;
}

export const projectMembersApi = {
  list: async (projectId: string): Promise<ProjectMember[]> => {
    const response = await api.get(`/projects/${projectId}/members/`);
    return Array.isArray(response.data) ? response.data : (response.data?.results || []);
  },
  invite: async (projectId: string, email: string, role: string = 'MEMBER'): Promise<ProjectMember> => {
    const response = await api.post(`/projects/${projectId}/members/invite/`, { email, role });
    return response.data;
  },
  remove: async (projectId: string, memberId: string): Promise<void> => {
    await api.post(`/projects/${projectId}/members/${memberId}/remove/`);
  },
  changeRole: async (projectId: string, memberId: string, role: string): Promise<void> => {
    await api.post(`/projects/${projectId}/members/${memberId}/change-role/`, { role });
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
  updateConfig: async (config: Record<string, unknown>): Promise<Record<string, unknown>> => {
    const { data } = await api.post('/autoscaler/config/', config);
    return data;
  },
  trigger: async (): Promise<AutoscalerStatus> => {
    const { data } = await api.post('/autoscaler/trigger/');
    return data;
  },
};

// ─── Scaling API ────────────────────────────────────────────────────────────

export interface Replica {
  id: string;
  service: string;
  status: 'RUNNING' | 'SPAWNING' | 'DESTROYED';
  node_name: string;
  created_at: string;
}

export const scalingApi = {
  getReplicas: async (serviceId: string): Promise<Replica[]> => {
    const response = await api.get('/scaling/replicas/', { params: { service: serviceId } });
    return extractDataList(response);
  },
  spawnReplica: async (serviceId: string, mode: 'horizontal' | 'vertical' = 'horizontal'): Promise<Replica> => {
    const response = await api.post(`/scaling/${serviceId}/spawn/`, null, { params: { mode } });
    return response.data;
  },
  destroyReplica: async (replicaId: string): Promise<{ status: string; message?: string }> => {
    const response = await api.delete('/scaling/destroy_replica/', { params: { id: replicaId } });
    return response.data;
  },
  updateAlertConfig: async (serviceId: string, config: Record<string, unknown>): Promise<{ status: string; message?: string }> => {
    const response = await api.put(`/scaling/${serviceId}/alert_config/`, config);
    return response.data;
  },
  getAlertConfig: async (serviceId: string): Promise<Record<string, unknown>> => {
    const response = await api.get(`/scaling/${serviceId}/alert_config/`);
    return response.data;
  },
  applyVpa: async (serviceId: string): Promise<{ status: string; node?: string; container?: string }> => {
    const response = await api.post(`/scaling/${serviceId}/apply_vpa/`);
    return response.data;
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
  activate: async (license_key: string): Promise<{ status: string; message?: string }> => {
    const { data } = await api.post('/licensing/activate/', { license_key });
    return data;
  },
  deactivate: async (): Promise<{ status: string; message?: string }> => {
    const { data } = await api.post('/licensing/deactivate/');
    return data;
  },
};

// ─── Cloud Resources API ───────────────────────────────────────────────────

export interface CloudProvider {
  id: string;
  name: string;
  provider_type: string;
  region?: string;
  project_id?: string;
  is_active: boolean;
  created_at: string;
}

export interface CloudResource {
  id: string;
  name: string;
  provider: string;
  region: string;
  type: string;
  status: string;
  config: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export const cloudResourceApi = {
  list: (): Promise<CloudResource[]> => api.get('/cloud/resources/').then(r => {
    const data = r.data;
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.results)) return data.results;
    return [];
  }),
  create: (data: Partial<CloudResource>): Promise<CloudResource> => api.post('/cloud/resources/', data).then(r => r.data),
  detail: (id: string): Promise<CloudResource> => api.get(`/cloud/resources/${id}/`).then(r => r.data),
  update: (id: string, data: Partial<CloudResource>): Promise<CloudResource> => api.put(`/cloud/resources/${id}/`, data).then(r => r.data),
  delete: (id: string): Promise<void> => api.delete(`/cloud/resources/${id}/`).then(() => undefined),
};

export interface Domain {
  id: string;
  name: string;
  service?: string;
  service_name?: string;
  dns_managed: boolean;
  ssl_enabled: boolean;
  status: string;
  target?: string;
  record_type?: string;
  created_at: string;
}

export const domainsApi = {
  list: (): Promise<Domain[]> => api.get('/domains/').then(r => {
    const data = r.data;
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.results)) return data.results;
    return [];
  }),
  create: (data: Partial<Domain>): Promise<Domain> => api.post('/domains/', data).then(r => r.data),
  detail: (id: string): Promise<Domain> => api.get(`/domains/${id}/`).then(r => r.data),
  update: (id: string, data: Partial<Domain>): Promise<Domain> => api.put(`/domains/${id}/`, data).then(r => r.data),
  delete: (id: string): Promise<void> => api.delete(`/domains/${id}/`).then(() => undefined),
};

export const cloudProviderApi = {
  list: async (): Promise<CloudProvider[]> => {
    const response = await api.get('/cloud/providers/');
    return extractDataList(response);
  },
};

// ─── Ecosystem API ────────────────────────────────────────────────────────────

export interface EcosystemPlanSummary {
  id: string;
  status: string;
  project: string | null;
  selected_repos: string[];
  ai_provider: string | null;
  use_shared_addons: boolean;
  cancel_others_on_failure: boolean;
  services_created: unknown[];
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface EcosystemPlanDetail extends EcosystemPlanSummary {
  plan: Record<string, unknown> | null;
  scan_progress: string | null;
  shared_addon_config: Record<string, unknown>;
  scan_task_id: string | null;
  deploy_task_id: string | null;
}

export const ecosystemApi = {
  // ── Existing ──
  bulkUpdateEnvironment: (data: { app_ids: string[]; env_vars: Record<string, string> }) =>
    api.post('/cloud/ecosystem/bulk-env/', data).then(r => r.data),
  cachedScan: () =>
    api.get('/cloud/ecosystem/cached-scan/').then(r => (r.data.has_cache ? r.data.plan : null)),

  // ── Scan / Deploy lifecycle ──
  getActivePlan: () =>
    api.get('/cloud/ecosystem/active-plan/').then(r => r.data),

  getTaskStatus: (taskId: string) =>
    api.get('/cloud/ecosystem/task_status/', { params: { task_id: taskId } }).then(r => r.data),

  getDeepScanStatus: (taskId: string) =>
    api.get('/cloud/ecosystem/deep_scan/status/', { params: { task_id: taskId } }).then(r => r.data),

  startScan: (data: { ai_provider: string; selected_repos: string[] }) =>
    api.post('/cloud/ecosystem/scan/', data).then(r => r.data),

  startDeepScan: (data: { ai_provider: string; repos_data: unknown[]; deploy_plan: unknown }) =>
    api.post('/cloud/ecosystem/deep_scan/', data).then(r => r.data),

  deploy: (data: {
    plan: unknown;
    plan_id: string;
    use_shared_addons: boolean;
    cancel_others_on_failure: boolean;
    shared_addon_config: Record<string, unknown>;
    mtls_config?: unknown;
    communication_rules?: unknown;
    env_scan_depth?: string;
  }) => api.post('/cloud/ecosystem/deploy/', data).then(r => r.data),

  downloadEnv: () =>
    api.get('/cloud/ecosystem/download-env/', { responseType: 'blob' }).then(r => r.data),

  // ── Plan history ──
  listPlans: (params?: { status?: string; page?: number }) =>
    api.get('/cloud/ecosystem/plans/', { params }).then(r => r.data),

  getPlan: (planId: string) =>
    api.get(`/cloud/ecosystem/plans/${planId}/`).then(r => r.data),

  restorePlanSnapshots: (planId: string, data: { confirm: true; service_ids?: string[]; redeploy?: boolean }) =>
    api.post(`/cloud/ecosystem/plans/${planId}/restore-snapshots/`, data).then(r => r.data),
};

// ─── Database Replicas API ───────────────────────────────────────────────────
//
// Manages PostgreSQL read-replica endpoints that pgcat can route SELECTs to.
// The password field is write-only: it is never returned in any response,
// so callers must PATCH it to rotate.

export type DatabaseReplicaKind = 'local' | 'remote';

export type DatabaseReplicaSslMode =
  | 'disable' | 'allow' | 'prefer' | 'require' | 'verify-ca' | 'verify-full';

export type DatabaseReplicaStatus = 'unknown' | 'ok' | 'warn' | 'error';

export interface DatabaseReplica {
  id: string;
  name: string;
  kind: DatabaseReplicaKind;
  host: string;
  port: number;
  database: string;
  username: string;
  // password is write-only; the server never returns it.
  ssl_mode: DatabaseReplicaSslMode;
  ssl_ca_path?: string;
  is_active: boolean;
  last_status: DatabaseReplicaStatus;
  last_checked_at?: string | null;
  last_error: string;
  lag_seconds?: number | null;
  application_name: string;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface DatabaseReplicaCreateInput {
  name: string;
  kind: DatabaseReplicaKind;
  host: string;
  port?: number;
  database?: string;
  username: string;
  password: string;
  ssl_mode?: DatabaseReplicaSslMode;
  ssl_ca_path?: string;
  is_active?: boolean;
  application_name?: string;
  notes?: string;
}

export interface DatabaseReplicaTestResult {
  ok: boolean;
  error: string;
  lag_seconds: number | null;
  endpoint: string;
}

export interface DatabaseReplicaSyncResult {
  replica_count: number;
  endpoints: string;
  pgcat_container: string | null;
  config_written: boolean;
  reloaded: boolean;
  error: string | null;
}

export interface DatabaseReplicaEndpointsResult {
  endpoints: string;
  count: number;
}

export const databaseReplicasApi = {
  list: async (): Promise<DatabaseReplica[]> => {
    const res = await api.get('/database-replicas/');
    const data = res.data;
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.results)) return data.results;
    return [];
  },
  get: async (id: string): Promise<DatabaseReplica> => {
    const res = await api.get(`/database-replicas/${id}/`);
    return res.data;
  },
  create: async (data: DatabaseReplicaCreateInput): Promise<DatabaseReplica> => {
    const res = await api.post('/database-replicas/', data);
    return res.data;
  },
  update: async (
    id: string,
    data: Partial<DatabaseReplicaCreateInput>,
  ): Promise<DatabaseReplica> => {
    const res = await api.patch(`/database-replicas/${id}/`, data);
    return res.data;
  },
  remove: async (id: string): Promise<void> => {
    await api.delete(`/database-replicas/${id}/`);
  },
  test: async (id: string): Promise<DatabaseReplicaTestResult> => {
    const res = await api.post(`/database-replicas/${id}/test/`);
    return res.data;
  },
  sync: async (): Promise<DatabaseReplicaSyncResult> => {
    const res = await api.post('/database-replicas/sync/');
    return res.data;
  },
  endpoints: async (): Promise<DatabaseReplicaEndpointsResult> => {
    const res = await api.get('/database-replicas/endpoints/');
    return res.data;
  },
};



export const organizationsApi = {
  list: async () => {
    const response = await api.get('/organizations/');
    return Array.isArray(response.data) ? response.data : (response.data?.results || []);
  },
  create: async (name: string) => {
    const response = await api.post('/organizations/', { name });
    return response.data;
  },
  update: async (id: string, data: Record<string, unknown>) => {
    const response = await api.patch(`/organizations/${id}/`, data);
    return response.data;
  },
  members: async (id: string) => {
    const response = await api.get(`/organizations/${id}/members/`);
    return response.data;
  },
  invite: async (id: string, email: string, role: string) => {
    const response = await api.post(`/organizations/${id}/invite/`, { email, role });
    return response.data;
  },
  getSSO: async () => {
    const response = await api.get('/organizations/sso/');
    return Array.isArray(response.data) ? response.data : (response.data?.results || []);
  },
  createSSO: async (data: Record<string, unknown>) => {
    const response = await api.post('/organizations/sso/', data);
    return response.data;
  },
  updateSSO: async (id: string, data: Record<string, unknown>) => {
    const response = await api.patch(`/organizations/sso/${id}/`, data);
    return response.data;
  },
  deleteSSO: async (id: string) => {
    const response = await api.delete(`/organizations/sso/${id}/`);
    return response.data;
  }
};

export const notificationsApi = {
  list: async () => {
    const response = await api.get('/notifications/');
    return Array.isArray(response.data) ? response.data : (response.data?.results || []);
  },
  markRead: async (id: string) => {
    const response = await api.post(`/notifications/${id}/mark_read/`);
    return response.data;
  },
  preferences: async () => {
    const response = await api.get('/preferences/');
    return Array.isArray(response.data) ? response.data : (response.data?.results || []);
  },
  updatePreference: async (id: string, data: Record<string, unknown>) => {
    const response = await api.patch(`/preferences/${id}/`, data);
    return response.data;
  }
};

export const aiAdminApi = { getProviders: async () => { const response = await api.get('/ai/providers/'); return response.data; }, updateProvider: async (provider: string, data: Record<string, unknown>) => { const response = await api.post('/ai/providers/update/', { provider, ...data }); return response.data; } };

export const registryCredentialsApi = {
  list: async () => {
    const response = await api.get('/registry-credentials/');
    const data = response.data;
    return Array.isArray(data) ? data : (data?.results || []);
  },
  create: async (data: Record<string, unknown>) => {
    const response = await api.post('/registry-credentials/', data);
    return response.data;
  },
  update: async (id: string, data: Record<string, unknown>) => {
    const response = await api.patch(`/registry-credentials/${id}/`, data);
    return response.data;
  },
  delete: async (id: string) => {
    await api.delete(`/registry-credentials/${id}/`);
  },
  testConnection: async (id: string) => {
    const response = await api.post(`/registry-credentials/${id}/test_connection/`);
    return response.data;
  }
};

export const scopedRegistryApi = {
  list: async (params?: { scope_type?: string; scope_id?: string }) => {
    const response = await api.get('/registry-scopes/', { params });
    const data = response.data;
    return Array.isArray(data) ? data : (data?.results || []);
  },
  get: async (id: string) => {
    const response = await api.get(`/registry-scopes/${id}/`);
    return response.data;
  },
  create: async (data: { scope_type: string; scope_id: string; registry_url: string; username?: string; password?: string; is_internal?: boolean; allowed_registry_hosts?: string[]; is_active?: boolean }) => {
    const response = await api.post('/registry-scopes/', data);
    return response.data;
  },
  update: async (id: string, data: Record<string, unknown>) => {
    const response = await api.patch(`/registry-scopes/${id}/`, data);
    return response.data;
  },
  delete: async (id: string) => {
    await api.delete(`/registry-scopes/${id}/`);
  },
  resolve: async (params: { scope_type: string; scope_id: string }) => {
    const response = await api.get('/registry-scopes/resolve/', { params });
    return response.data;
  }
};

export interface ProjectRegistryAuth {
  username: string;
  password: string;
  per_project: boolean;
  urls: string[];
  node_url: string;
}

export interface ProjectRegistryInfo {
  effective_url: string;
  has_username: boolean;
  has_password: boolean;
  is_scoped: boolean;
  hierarchy: string[];
  auth?: ProjectRegistryAuth;
}

export const projectRegistryApi = {
  get: async (projectId: string): Promise<ProjectRegistryInfo> => {
    const response = await api.get(`/projects/${projectId}/registry/`);
    return response.data;
  },
  rotate: async (projectId: string) => {
    const response = await api.post(`/projects/${projectId}/registry/rotate/`);
    return response.data;
  },
};

export const networkScopesApi = {
  list: async () => {
    const res = (await api.get('/network-scopes/')).data;
    return Array.isArray(res) ? res : (res?.results || res || []);
  },
  create: async (data: Record<string, unknown>) => (await api.post('/network-scopes/', data)).data,
  delete: async (id: string) => (await api.delete(`/network-scopes/${id}/`)).data,
};

export const infisicalApi = {
  sync: async (data?: { direction?: 'push' | 'pull'; workspace?: string }) => {
    const response = await api.post('/platform-config/sync-infisical/', data || {});
    return response.data;
  },
};

// ── Alert Rules & Notification Channels ──────────────────────────────

export interface NotificationChannel {
  id: string;
  name: string;
  channel_type: 'email' | 'slack' | 'sms' | 'webhook';
  target: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AlertRule {
  id: string;
  name: string;
  enabled: boolean;
  metric: string;
  operator: string;
  threshold: number;
  severity: 'info' | 'warning' | 'critical';
  channels: string[];
  cooldown_minutes: number;
  message_template: string;
  created_at: string;
  updated_at: string;
}

export const alertsApi = {
  // Notification Channels
  listChannels: async (): Promise<NotificationChannel[]> => {
    const res = await api.get('/notifications/channels/');
    return Array.isArray(res.data) ? res.data : (res.data?.results || []);
  },
  createChannel: async (data: Partial<NotificationChannel>): Promise<NotificationChannel> => {
    const res = await api.post('/notifications/channels/', data);
    return res.data;
  },
  updateChannel: async (id: string, data: Partial<NotificationChannel>): Promise<NotificationChannel> => {
    const res = await api.patch(`/notifications/channels/${id}/`, data);
    return res.data;
  },
  deleteChannel: async (id: string): Promise<void> => {
    await api.delete(`/notifications/channels/${id}/`);
  },
  testChannel: async (id: string): Promise<{ status: string; message?: string; error?: string }> => {
    const res = await api.post(`/notifications/channels/${id}/test/`);
    return res.data;
  },

  // Alert Rules
  listRules: async (): Promise<AlertRule[]> => {
    const res = await api.get('/notifications/rules/');
    return Array.isArray(res.data) ? res.data : (res.data?.results || []);
  },
  createRule: async (data: Partial<AlertRule>): Promise<AlertRule> => {
    const res = await api.post('/notifications/rules/', data);
    return res.data;
  },
  updateRule: async (id: string, data: Partial<AlertRule>): Promise<AlertRule> => {
    const res = await api.patch(`/notifications/rules/${id}/`, data);
    return res.data;
  },
  deleteRule: async (id: string): Promise<void> => {
    await api.delete(`/notifications/rules/${id}/`);
  },
  toggleRule: async (id: string): Promise<{ enabled: boolean }> => {
    const res = await api.post(`/notifications/rules/${id}/toggle/`);
    return res.data;
  },

  // SMTP
  testSmtp: async (toEmail: string): Promise<{ status: string; message?: string; error?: string }> => {
    const res = await api.post('/notifications/test-smtp/', { to_email: toEmail });
    return res.data;
  },
};

// ─── MCP Server API ─────────────────────────────────────────────────────────

export interface McpStatus {
  exists: boolean;
  running: boolean;
  status?: string;
  container_id?: string;
  image?: string;
  started_at?: string;
  endpoint?: string;
  port?: number;
  networks?: string[];
  tools_count?: number;
  fastmcp_available?: boolean;
  sdk_available?: boolean;
  error?: string;
}

export interface McpToolParam {
  name: string;
  type: string;
  required: boolean;
  default?: unknown;
}

export interface McpTool {
  name: string;
  description: string;
  params: McpToolParam[];
}

export const mcpApi = {
  status: async (): Promise<McpStatus> => {
    const res = await api.get('/mcp/status/');
    return res.data;
  },
  control: async (action: 'start' | 'stop' | 'restart'): Promise<McpStatus> => {
    const res = await api.post('/mcp/control/', { action });
    return res.data;
  },
  tools: async (): Promise<{ tools: McpTool[]; count: number }> => {
    const res = await api.get('/mcp/tools/');
    return res.data;
  },
  callTool: async (name: string, args: Record<string, unknown>): Promise<{ ok: boolean; result?: unknown; error?: string }> => {
    const res = await api.post(`/mcp/tools/${encodeURIComponent(name)}/call/`, { args });
    return res.data;
  },
};
