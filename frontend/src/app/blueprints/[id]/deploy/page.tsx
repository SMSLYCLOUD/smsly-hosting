'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { blueprintsApi, type Blueprint } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { ArrowLeft, Rocket, AlertCircle, Cpu, HardDrive, MemoryStick, CheckCircle } from 'lucide-react';

export default function BlueprintDeployPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [blueprint, setBlueprint] = useState<Blueprint | null>(null);
  const [loading, setLoading] = useState(true);
  const [deploying, setDeploying] = useState(false);
  const [deployed, setDeployed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    blueprintsApi.get(id)
      .then(setBlueprint)
      .catch((err) => setError(err?.message || 'Failed to load blueprint'))
      .finally(() => setLoading(false));
  }, [id]);

  const handleDeploy = async () => {
    setDeploying(true);
    setError(null);
    try {
      await blueprintsApi.deploy(blueprint!.id);
      setDeployed(true);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Deployment failed');
    } finally {
      setDeploying(false);
    }
  };

  if (loading) {
    return (
      <main className="flex h-screen items-center justify-center">
        <div className="w-full max-w-lg space-y-4 p-4">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-32 w-full rounded-lg" />
          <Skeleton className="h-10 w-full rounded-lg" />
        </div>
      </main>
    );
  }

  if (error && !blueprint) {
    return (
      <main className="flex h-screen items-center justify-center">
        <div className="flex flex-col items-center gap-4 text-center">
          <AlertCircle className="h-10 w-10 text-red-400" />
          <p className="text-sm text-zinc-400">{error}</p>
          <Button variant="outline" onClick={() => router.push('/blueprints')}>
            Back to Blueprints
          </Button>
        </div>
      </main>
    );
  }

  if (deployed) {
    return (
      <main className="flex h-screen items-center justify-center">
        <div className="flex flex-col items-center gap-4 text-center">
          <CheckCircle className="h-12 w-12 text-emerald-400" />
          <h2 className="text-lg font-semibold text-zinc-100">Deployment Started</h2>
          <p className="text-sm text-zinc-400 max-w-sm">
            &quot;{blueprint?.name}&quot; is being deployed. You can track its progress in your services dashboard.
          </p>
          <div className="flex gap-3 mt-2">
            <Button onClick={() => router.push('/services')} className="bg-emerald-500 text-zinc-950 hover:bg-emerald-400">
              View Services
            </Button>
            <Button variant="outline" onClick={() => router.push('/blueprints')}>
              Browse More
            </Button>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="flex h-screen items-center justify-center p-4">
      <div className="w-full max-w-lg space-y-6">
        <button
          onClick={() => router.back()}
          className="flex items-center gap-1.5 text-xs text-zinc-500 hover:text-zinc-300 transition"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back
        </button>

        <Card className="border-zinc-800/60 bg-zinc-900/40 backdrop-blur-sm">
          <CardHeader className="p-5 pb-0">
            <div className="flex items-center gap-3">
              <div
                className="flex h-12 w-12 items-center justify-center rounded-xl text-xl font-bold"
                style={{ backgroundColor: blueprint?.color || 'rgb(39 39 42)', color: '#fff' }}
              >
                {blueprint?.icon || blueprint?.name.charAt(0).toUpperCase()}
              </div>
              <div>
                <h2 className="text-lg font-semibold text-zinc-100">{blueprint?.name}</h2>
                <p className="text-xs text-zinc-500">{blueprint?.category}</p>
              </div>
            </div>
          </CardHeader>

          <CardContent className="p-5 space-y-4">
            <p className="text-sm text-zinc-400 leading-relaxed">{blueprint?.description}</p>

            {blueprint?.tags && blueprint.tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {blueprint.tags.map((tag) => (
                  <Badge key={tag} variant="secondary" className="text-xs">{tag}</Badge>
                ))}
              </div>
            )}

            {blueprint?.min_resources && (
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
                <p className="mb-2 text-xs font-medium text-zinc-500 uppercase tracking-wider">Resource Requirements</p>
                <div className="flex items-center gap-4 text-sm text-zinc-300">
                  <span className="flex items-center gap-1.5">
                    <Cpu className="h-4 w-4 text-zinc-500" />
                    {blueprint.min_resources.cpu_cores} CPU Cores
                  </span>
                  <span className="flex items-center gap-1.5">
                    <MemoryStick className="h-4 w-4 text-zinc-500" />
                    {blueprint.min_resources.memory_mb} MB RAM
                  </span>
                  <span className="flex items-center gap-1.5">
                    <HardDrive className="h-4 w-4 text-zinc-500" />
                    {blueprint.min_resources.storage_gb} GB Storage
                  </span>
                </div>
              </div>
            )}

            {blueprint?.repository_url && (
              <p className="text-xs text-zinc-600">
                Source: <span className="text-zinc-500">{blueprint.repository_url}</span>
              </p>
            )}

            {error && (
              <div className="rounded-md bg-red-500/10 border border-red-500/20 p-3">
                <p className="text-xs text-red-400">{error}</p>
              </div>
            )}

            <Button
              onClick={handleDeploy}
              disabled={deploying}
              className="w-full h-10 bg-emerald-500 text-sm font-semibold text-zinc-950 hover:bg-emerald-400 disabled:opacity-50"
            >
              {deploying ? (
                <span className="flex items-center gap-2">
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-zinc-950 border-t-transparent" />
                  Deploying...
                </span>
              ) : (
                <span className="flex items-center gap-2">
                  <Rocket className="h-4 w-4" />
                  Deploy {blueprint?.name}
                </span>
              )}
            </Button>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
