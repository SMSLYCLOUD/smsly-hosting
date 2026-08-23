'use client';

import Image from 'next/image';
import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { addonsApi, Addon } from '@/lib/api';
import { ADDON_TYPES } from '@/lib/addonConstants';
import { MaintenanceTabs } from '@/components/addons/MaintenanceTabs';
import { AddonHaCard } from '@/components/addons/AddonHaCard';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/use-toast';
import { Button } from '@/components/ui/button';
import { ArrowLeft, Database, Loader2, Globe, Server, ExternalLink } from 'lucide-react';
import Link from 'next/link';

export default function AddonDetailPage() {
  const { toast } = useToast();
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;
  const [addon, setAddon] = useState<Addon | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    addonsApi.get(id)
      .then(setAddon)
      .catch(() => router.push('/dashboard'))
      .finally(() => setLoading(false));
  }, [id, router]);

  if (loading) {
    return (
      <main className="min-h-screen flex flex-col text-foreground relative">
        <div className="border-b border-border bg-card/60 backdrop-blur-md">
          <div className="container mx-auto py-6">
            <div className="flex items-center gap-4 mb-4">
              <Skeleton className="h-8 w-8 rounded-full" />
              <div className="space-y-2">
                <Skeleton className="h-6 w-48" />
                <Skeleton className="h-4 w-32" />
              </div>
            </div>
          </div>
        </div>
        <div className="flex-1 container mx-auto py-8">
          <div className="flex items-center justify-center h-64 text-muted-foreground gap-2">
            <Loader2 className="w-5 h-5 animate-spin" /> Loading addon...
          </div>
        </div>
      </main>
    );
  }

  if (!addon) return null;

  const handleTogglePublic = async () => {
    try {
      await addonsApi.togglePublicBucket(addon.id);
      toast({ title: 'Toggled bucket public access' });
      // reload
      const updated = await addonsApi.get(addon.id);
      setAddon(updated);
    } catch (e: unknown) {
      toast({ title: 'Error', description: e instanceof Error ? e.message : 'Unknown error', variant: 'destructive' });
    }
  };

  const handleDeprovision = async () => {
    if (!confirm('Are you sure you want to deprovision this addon? This cannot be undone.')) return;
    try {
      await addonsApi.deprovision(addon.id);
      toast({ title: 'Deprovisioning started' });
      router.push('/dashboard');
    } catch (e: unknown) {
      toast({ title: 'Error', description: e instanceof Error ? e.message : 'Unknown error', variant: 'destructive' });
    }
  };


  const meta = ADDON_TYPES.find(t => t.value === addon.addon_type);
  const statusColor = (s: string) => {
    switch (s) {
      case 'ACTIVE': return 'bg-emerald-500/10 text-emerald-500';
      case 'PROVISIONING': return 'bg-yellow-500/10 text-yellow-500';
      case 'FAILED': return 'bg-red-500/10 text-red-500';
      default: return 'bg-zinc-500/10 text-zinc-500';
    }
  };

  return (
    <main className="min-h-screen flex flex-col text-foreground relative">
      <div className="border-b border-border bg-card/60 backdrop-blur-md">
        <div className="container mx-auto py-6">
          <div className="flex items-center gap-4">
            <Link href="/dashboard" className="p-2 hover:bg-muted rounded-full transition-colors text-muted-foreground hover:text-foreground">
              <ArrowLeft size={20} />
            </Link>
            <span className="text-3xl block h-8 w-8 relative">
              {meta?.logo ? (
                <Image src={meta.logo} alt={meta?.label || ''} className="w-full h-full object-contain" unoptimized />
              ) : (
                <Database className="w-full h-full text-muted-foreground" />
              )}
            </span>
            <div>
              <div className="flex items-center gap-3">
                <h1 className="text-2xl font-bold tracking-tight">{addon.name}</h1>
                <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${statusColor(addon.status)}`}>
                  {addon.status}
                </span>
              </div>
              <div className="flex items-center gap-4 text-sm text-muted-foreground mt-1">
                <span className={`text-xs font-medium ${meta?.color || ''}`}>{meta?.label || addon.addon_type}</span>
                <span className="flex items-center gap-1.5">
                  <Server size={12} />
                  {addon.service ? `Service: ${addon.service}` : 'Standalone'}
                </span>
                {addon.public_domain && (
                  <a
                    href={`https://${addon.public_domain}`}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-1.5 text-blue-400 hover:text-blue-300"
                  >
                    <Globe size={12} />
                    {addon.public_domain}
                    <ExternalLink size={10} />
                  </a>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 container mx-auto py-8 space-y-6">
        <AddonHaCard addon={addon} onChanged={setAddon} />
        {addon.status === 'ACTIVE' ? (
          <MaintenanceTabs addonId={addon.id} />
        ) : (
          <div className="bg-card border border-border rounded-xl p-12 text-center">
            <Database className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
            <h3 className="font-semibold text-foreground mb-2">Addon is not active</h3>
            <p className="text-sm text-muted-foreground">
              Maintenance tools are available only when the addon status is ACTIVE.
              Current status: <span className={`font-bold uppercase ${statusColor(addon.status)}`}>{addon.status}</span>
            </p>
          </div>
        )}
      </div>
    </main>
  );
}
