import axios from 'axios';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

const api = axios.create({
  baseURL: API_URL,
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
  status: string;
  build_logs?: string;
  ai_diagnosis?: string;
  created_at: string;
}

export interface EnvVar {
  key: string;
  value: string;
}

export const servicesApi = {
  list: async (): Promise<Service[]> => {
    const response = await api.get('/services/');
    return response.data;
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
  getDeployment: async (id: string): Promise<Deployment> => {
    const response = await api.get(`/deployments/${id}/`);
    return response.data;
  },
  updateEnv: async (id: string, envVars: EnvVar[]): Promise<any> => {
    const response = await api.post(`/services/${id}/env-vars/`, envVars);
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
