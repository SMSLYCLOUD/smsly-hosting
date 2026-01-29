'use client';

import { useState } from 'react';
import { servicesApi } from '@/lib/api';
import { useRouter } from 'next/navigation';

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
    <main className="flex min-h-screen flex-col items-center p-24">
      <h1 className="text-4xl font-bold mb-8">Deploy New Service</h1>
      <div className="w-full max-w-xl bg-white dark:bg-zinc-800 rounded-lg shadow p-8">
        <form onSubmit={handleSubmit} className="space-y-6">
          {error && (
            <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded relative">
              {error}
            </div>
          )}

          <div>
            <label className="block text-sm font-medium mb-2">Service Name</label>
            <input
              type="text"
              required
              className="w-full p-2 border rounded dark:bg-zinc-700 dark:border-zinc-600"
              placeholder="my-awesome-app"
              value={formData.name}
              onChange={e => setFormData({...formData, name: e.target.value})}
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-2">GitHub Repository URL</label>
            <input
              type="url"
              required
              className="w-full p-2 border rounded dark:bg-zinc-700 dark:border-zinc-600"
              placeholder="https://github.com/user/repo"
              value={formData.repository_url}
              onChange={e => setFormData({...formData, repository_url: e.target.value})}
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
                <label className="block text-sm font-medium mb-2">Branch</label>
                <input
                type="text"
                className="w-full p-2 border rounded dark:bg-zinc-700 dark:border-zinc-600"
                value={formData.branch}
                onChange={e => setFormData({...formData, branch: e.target.value})}
                />
            </div>
            <div>
                <label className="block text-sm font-medium mb-2">Port</label>
                <input
                type="number"
                className="w-full p-2 border rounded dark:bg-zinc-700 dark:border-zinc-600"
                value={formData.internal_port}
                onChange={e => setFormData({...formData, internal_port: parseInt(e.target.value)})}
                />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">Custom Domain (Optional)</label>
            <input
              type="text"
              className="w-full p-2 border rounded dark:bg-zinc-700 dark:border-zinc-600"
              placeholder="myapp.example.com"
              value={formData.public_domain}
              onChange={e => setFormData({...formData, public_domain: e.target.value})}
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-blue-600 text-white p-3 rounded font-bold hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? 'Deploying...' : 'Deploy Now'}
          </button>
        </form>
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
