import axios from 'axios';

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

// Interceptor to add auth token
api.interceptors.request.use((config) => {
  const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
  if (token) {
    config.headers.Authorization = `Token ${token}`;
  }
  return config;
});

export interface Service {
  id: string;
  name: string;
  repository_url?: string;
  branch?: string;
  internal_port?: number;
  public_domain?: string;
  created_at?: string;
  cpu_cores?: number;
  memory_mb?: number;
  deploy_type?: 'GIT' | 'DOCKER' | 'UPLOAD' | 'TEMPLATE';
  docker_image?: string;
  start_command?: string;
  template_id?: string;
  provider?: string;  // Cloud provider: 'local', 'aws', 'gcp', 'azure', 'digitalocean', etc.
  region?: string;    // Deployment region
  latest_deployment?: {
    id: string;
    status: string;
    commit_hash?: string;
    created_at: string;
  };
}

export interface Deployment {
  id: string;
  service: string;
  commit_hash: string;
  commit_message?: string;
  status: string;
  build_logs?: string;
  ai_diagnosis?: string;
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
  deploy: async (id: string, ref: string = 'HEAD') => {
    const response = await api.post(`/services/${id}/deploy/`, { ref });
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

export default api;
