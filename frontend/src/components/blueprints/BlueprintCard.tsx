'use client';

import React, { useState } from 'react';
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { blueprintsApi, type Blueprint } from '@/lib/api';
import { cn } from '@/lib/utils';
import { Rocket, Cpu, HardDrive, MemoryStick } from 'lucide-react';

interface BlueprintCardProps {
  blueprint: Blueprint;
  onDeploy?: (id: string) => void;
}

export const BlueprintCard = React.memo(function BlueprintCard({ blueprint, onDeploy }: BlueprintCardProps) {
  const [deploying, setDeploying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDeploy = async () => {
    setDeploying(true);
    setError(null);
    try {
      await blueprintsApi.deploy(blueprint.id);
      onDeploy?.(blueprint.id);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Deployment failed');
    } finally {
      setDeploying(false);
    }
  };

  return (
    <Card className="group relative overflow-hidden border-zinc-800/60 bg-zinc-900/40 backdrop-blur-sm transition-all hover:border-zinc-700/60 hover:bg-zinc-900/60">
      <CardHeader className="p-4 pb-0">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div
              className="flex h-10 w-10 items-center justify-center rounded-lg text-lg font-bold"
              style={{ backgroundColor: blueprint.color || 'rgb(39 39 42)', color: '#fff' }}
            >
              {blueprint.icon || blueprint.name.charAt(0).toUpperCase()}
            </div>
            <div>
              <h3 className="text-sm font-semibold text-zinc-100">{blueprint.name}</h3>
              <p className="text-xs text-zinc-500">{blueprint.category}</p>
            </div>
          </div>
          {blueprint.is_official && (
            <Badge variant="success" className="text-[10px] px-1.5 py-0">Official</Badge>
          )}
        </div>
      </CardHeader>

      <CardContent className="p-4 space-y-3">
        <p className="text-xs text-zinc-400 line-clamp-2 leading-relaxed">
          {blueprint.description}
        </p>

        {blueprint.min_resources && (
          <div className="flex items-center gap-3 text-xs text-zinc-500">
            <span className="flex items-center gap-1">
              <Cpu className="h-3 w-3" />
              {blueprint.min_resources.cpu_cores} CPU
            </span>
            <span className="flex items-center gap-1">
              <MemoryStick className="h-3 w-3" />
              {blueprint.min_resources.memory_mb} MB
            </span>
            <span className="flex items-center gap-1">
              <HardDrive className="h-3 w-3" />
              {blueprint.min_resources.storage_gb} GB
            </span>
          </div>
        )}

        {blueprint.tags && blueprint.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {blueprint.tags.map((tag) => (
              <Badge key={tag} variant="secondary" className="text-[10px] px-1.5 py-0">
                {tag}
              </Badge>
            ))}
          </div>
        )}
      </CardContent>

      <CardFooter className="p-4 pt-0 flex-col items-stretch gap-1">
        <Button
          onClick={handleDeploy}
          disabled={deploying}
          className="h-8 w-full rounded-md bg-emerald-500 text-xs font-semibold text-zinc-950 hover:bg-emerald-400 disabled:opacity-50"
        >
          {deploying ? (
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-zinc-950 border-t-transparent" />
              Deploying...
            </span>
          ) : (
            <span className="flex items-center gap-1.5">
              <Rocket className="h-3.5 w-3.5" />
              Deploy
            </span>
          )}
        </Button>
        {error && (
          <p className="text-[11px] text-red-400 text-center">{error}</p>
        )}
      </CardFooter>
    </Card>
  );
});
