import axios from 'axios';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

const api = axios.create({
  baseURL: API_URL,
});

export interface Service {
  id: string;
  name: string;
  repository_url: string;
  branch: string;
  build_command?: string;
  start_command?: string;
  internal_port: number;
  cpu_cores: number;
  memory_mb: number;
  min_replicas: number;
  max_replicas: number;
  autoscale_cpu_target: number;
  use_blue_green: boolean;
  is_preview: boolean;
  pr_number?: number;
  public_domain?: string;
  created_at?: string;
  latest_deployment?: {
    id: string;
    status: string;
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

export default api;
