'use client';

import { useState } from 'react';
import { servicesApi } from '@/lib/api';
import { useRouter } from 'next/navigation';
import { Navbar } from '@/components/layout/Navbar';

import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';

function NewServiceForm() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [formData, setFormData] = useState({
    name: searchParams.get('name') || '',
    repository_url: searchParams.get('repo') || '',
    branch: 'main',
    internal_port: parseInt(searchParams.get('port') || '8000'),
    public_domain: ''
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      // 1. Create Service
      const service = await servicesApi.create(formData);

      // 2. Trigger Initial Deployment
      await servicesApi.deploy(service.id, 'HEAD');

      router.push(`/services/${service.id}`);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || 'Failed to deploy service');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen flex flex-col bg-background">
      <Navbar />
      <div className="flex-1 flex flex-col items-center justify-center p-4 md:p-8">
        <div className="w-full max-w-lg bg-card text-card-foreground border rounded-lg shadow-lg p-6 md:p-8">
          <h1 className="text-3xl font-bold mb-6 text-center">Deploy New Service</h1>

          <form onSubmit={handleSubmit} className="space-y-5">
            {error && (
              <div className="bg-destructive/15 border border-destructive/50 text-destructive text-sm px-4 py-3 rounded relative">
                {error}
              </div>
            )}

            <div>
              <label className="block text-sm font-medium mb-2 text-foreground">Service Name</label>
              <input
                type="text"
                required
                className="w-full p-2 border rounded bg-background text-foreground focus:ring-2 focus:ring-primary focus:outline-none"
                placeholder="my-awesome-app"
                value={formData.name}
                onChange={e => setFormData({...formData, name: e.target.value})}
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-2 text-foreground">GitHub Repository URL</label>
              <input
                type="url"
                required
                className="w-full p-2 border rounded bg-background text-foreground focus:ring-2 focus:ring-primary focus:outline-none"
                placeholder="https://github.com/user/repo"
                value={formData.repository_url}
                onChange={e => setFormData({...formData, repository_url: e.target.value})}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                  <label className="block text-sm font-medium mb-2 text-foreground">Branch</label>
                  <input
                  type="text"
                  className="w-full p-2 border rounded bg-background text-foreground focus:ring-2 focus:ring-primary focus:outline-none"
                  value={formData.branch}
                  onChange={e => setFormData({...formData, branch: e.target.value})}
                  />
              </div>
              <div>
                  <label className="block text-sm font-medium mb-2 text-foreground">Port</label>
                  <input
                  type="number"
                  className="w-full p-2 border rounded bg-background text-foreground focus:ring-2 focus:ring-primary focus:outline-none"
                  value={formData.internal_port}
                  onChange={e => setFormData({...formData, internal_port: parseInt(e.target.value)})}
                  />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium mb-2 text-foreground">Custom Domain (Optional)</label>
              <input
                type="text"
                className="w-full p-2 border rounded bg-background text-foreground focus:ring-2 focus:ring-primary focus:outline-none"
                placeholder="myapp.example.com"
                value={formData.public_domain}
                onChange={e => setFormData({...formData, public_domain: e.target.value})}
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-primary text-primary-foreground p-3 rounded font-bold hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {loading ? 'Deploying...' : 'Deploy Now'}
            </button>
          </form>
        </div>
      </div>
    </main>
  );
}

export default function NewServicePage() {
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <NewServiceForm />
    </Suspense>
  );
}
