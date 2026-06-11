'use client';

import { useEffect, useState, useMemo } from 'react';
import { blueprintsApi, type Blueprint } from '@/lib/api';
import { BlueprintCard } from '@/components/blueprints/BlueprintCard';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Search, BookTemplate, AlertCircle } from 'lucide-react';

const CATEGORIES = ['All', 'AI', 'Database', 'Web App', 'API', 'CMS', 'Dev Tool', 'Other'];

export default function BlueprintsPage() {
  const [blueprints, setBlueprints] = useState<Blueprint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('All');

  useEffect(() => {
    let cancelled = false;
    const fetch = async () => {
      try {
        const data = await blueprintsApi.list();
        if (!cancelled) setBlueprints(data);
      } catch (err: any) {
        if (!cancelled) setError(err?.message || 'Failed to load blueprints');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetch();
    return () => { cancelled = true; };
  }, []);

  const filtered = useMemo(() => {
    return blueprints.filter((bp) => {
      const matchesSearch = !search ||
        bp.name.toLowerCase().includes(search.toLowerCase()) ||
        bp.description.toLowerCase().includes(search.toLowerCase()) ||
        bp.tags?.some(t => t.toLowerCase().includes(search.toLowerCase()));
      const matchesCategory = category === 'All' || bp.category === category;
      return matchesSearch && matchesCategory;
    });
  }, [blueprints, search, category]);

  const handleDeploy = (id: string) => {
    // deploy success — could show toast, for now just remove from stale list
  };

  if (error) {
    return (
      <main className="flex h-screen items-center justify-center">
        <div className="flex flex-col items-center gap-4 text-center">
          <AlertCircle className="h-10 w-10 text-red-400" />
          <p className="text-sm text-zinc-400">{error}</p>
          <button
            onClick={() => window.location.reload()}
            className="rounded-md bg-zinc-800 px-4 py-2 text-xs text-zinc-200 hover:bg-zinc-700"
          >
            Retry
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="h-screen min-h-0 flex flex-col">
      <div className="z-20 border-b border-zinc-800/60 bg-[#070a12]/85 backdrop-blur-xl">
        <div className="mx-auto w-full max-w-[1440px] px-4 py-4 space-y-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-500/20">
              <BookTemplate className="h-5 w-5 text-emerald-400" />
            </div>
            <div>
              <h1 className="text-lg font-bold text-zinc-100">Blueprints</h1>
              <p className="text-xs text-zinc-500">Pre-configured templates for one-click deployment</p>
            </div>
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="relative w-full max-w-xs">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500" />
              <Input
                placeholder="Search blueprints..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="h-9 rounded-lg border-zinc-700/60 bg-zinc-900/50 pl-9 text-sm text-zinc-100 placeholder:text-zinc-500"
              />
            </div>
            <div className="flex flex-wrap gap-1.5">
              {CATEGORIES.map((cat) => (
                <button
                  key={cat}
                  onClick={() => setCategory(cat)}
                  className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                    category === cat
                      ? 'bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/30'
                      : 'bg-zinc-800/50 text-zinc-400 hover:bg-zinc-700/50 hover:text-zinc-200'
                  }`}
                >
                  {cat}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2 text-xs text-zinc-500">
            {loading ? (
              <span>Loading...</span>
            ) : (
              <span>{filtered.length} of {blueprints.length} blueprints</span>
            )}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[1440px] px-4 py-6">
          {loading ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="rounded-lg border border-zinc-800/60 bg-zinc-900/40 p-4 space-y-3">
                  <div className="flex items-center gap-3">
                    <Skeleton className="h-10 w-10 rounded-lg" />
                    <div className="space-y-1.5">
                      <Skeleton className="h-4 w-28" />
                      <Skeleton className="h-3 w-16" />
                    </div>
                  </div>
                  <Skeleton className="h-8 w-full" />
                  <Skeleton className="h-4 w-3/4" />
                  <div className="flex gap-2">
                    <Skeleton className="h-5 w-14 rounded-full" />
                    <Skeleton className="h-5 w-14 rounded-full" />
                  </div>
                  <Skeleton className="h-8 w-full rounded-md" />
                </div>
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <BookTemplate className="mb-3 h-12 w-12 text-zinc-700" />
              <p className="text-sm text-zinc-500">No blueprints found</p>
              <p className="text-xs text-zinc-600 mt-1">Try adjusting your search or filter</p>
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {filtered.map((bp) => (
                <BlueprintCard key={bp.id} blueprint={bp} onDeploy={handleDeploy} />
              ))}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
