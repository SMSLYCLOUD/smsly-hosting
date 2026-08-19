'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { platformApi, servicesApi, addonsApi, Service, Addon } from '@/lib/api';
import { useRouter } from 'next/navigation';
import { Plus, LayoutGrid, Radar, Puzzle, Store } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { motion } from 'framer-motion';
import { ServicesGrid } from '@/components/dashboard/ServicesGrid';
import { AddonsTab } from '@/components/addons/AddonsTab';
import { EcosystemSuggestion } from '@/components/dashboard/EcosystemSuggestion';
import dynamic from 'next/dynamic';


const FleetRadar = dynamic(() => import('@/components/canvas/FleetRadar').then(mod => mod.FleetRadar), {
  loading: () => <div className="flex items-center justify-center h-full text-muted-foreground">Loading Radar...</div>,
  ssr: false
});

function buildServiceFingerprint(services: Service[]): string {
  if (!Array.isArray(services)) return '';
  return services
    .map((service) => [
      service.id,
      service.name,
      service.public_domain || '',
      service.branch || '',
      service.repository_url || '',
      service.latest_deployment?.id || '',
      service.latest_deployment?.status || '',
    ].join(':'))
    .sort()
    .join('|');
}

export default function ServicesPage() {
  const router = useRouter();
  const [viewMode, setViewMode] = useState<'GRID' | 'RADAR' | 'ADDONS'>('GRID');
  const [services, setServices] = useState<Service[]>([]);
  const [addons, setAddons] = useState<Addon[]>([]);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [pollIntervalMs, setPollIntervalMs] = useState(5000);
  const [resourceData, setResourceData] = useState<any>(null);
  const fingerprintRef = useRef('');
  const consecutiveFailuresRef = useRef(0);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const viewTabs: Array<{ id: 'GRID' | 'RADAR' | 'ADDONS'; label: string; icon: React.ComponentType<{ size?: number; className?: string }> }> = [
    { id: 'GRID', label: 'Grid', icon: LayoutGrid },
    { id: 'RADAR', label: 'Radar', icon: Radar },
    { id: 'ADDONS', label: 'Addons', icon: Puzzle },
  ];

  const fetchData = useCallback(async () => {
    try {
      const nextServices = await servicesApi.list();
      const nextAddons = await addonsApi.list().catch(() => []);
      
      // Transform addons into service-like objects for the grid
      const addonServices = nextAddons.map(a => ({
        ...a,
        isAddon: true,
        health_status: a.status === 'ACTIVE' || a.status === 'RUNNING' ? 'healthy' : 'unhealthy'
      })) as any;

      const combined = [...nextServices, ...addonServices];
      const nextFingerprint = buildServiceFingerprint(combined);
      
      if (nextFingerprint !== fingerprintRef.current) {
        fingerprintRef.current = nextFingerprint;
        setServices(combined);
        setAddons(nextAddons);
      }
      consecutiveFailuresRef.current = 0;
      setFetchError(null);
    } catch (error) {
      const nextFailures = consecutiveFailuresRef.current + 1;
      consecutiveFailuresRef.current = nextFailures;
      const statusCode = (error as any)?.response?.status;
      const rawMessage = String((error as any)?.message || '');
      if (rawMessage.includes('Switched to Local')) {
        setFetchError('Remote server unreachable. Switched to Local mode.');
      } else if (statusCode === 502) {
        setFetchError('Upstream unavailable (502). Retrying with backoff.');
      } else {
        setFetchError('Failed to load services. Retrying automatically.');
      }
      if (nextFailures === 1 || nextFailures % 6 === 0) {
        console.error('Failed to fetch services:', error);
      }
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    const getBaseInterval = () => (
      viewMode === 'GRID' ? 5000 : 15000
    );

    const getNextInterval = () => {
      const baseInterval = getBaseInterval();
      const failures = consecutiveFailuresRef.current;
      if (failures <= 0) return baseInterval;
      const multiplier = 2 ** Math.min(failures - 1, 4); // 1x, 2x, 4x, 8x, 16x
      return Math.min(baseInterval * multiplier, 60000);
    };

    const tick = async () => {
      await fetchData();
      if (cancelled) return;

      const nextInterval = getNextInterval();
      setPollIntervalMs(nextInterval);
      pollTimerRef.current = setTimeout(tick, nextInterval);
    };

    setPollIntervalMs(getBaseInterval());
    tick();

    return () => {
      cancelled = true;
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [fetchData, viewMode]);

  const primaryServices = services.filter(s => !(s as any).isAddon);

  return (
    <main className="h-screen min-h-0 flex flex-col premium-bg transition-colors duration-500">

      {/* View Toggle Bar */}
      <div className="z-20 border-b border-zinc-800/60 bg-[#070a12]/85 backdrop-blur-xl">
        <div className="mx-auto grid w-full max-w-[1440px] grid-cols-1 gap-3 px-4 py-3 md:grid-cols-[1fr_auto_1fr] md:items-center">
          <div className="flex items-center justify-center gap-2 md:justify-start">
            <span className="rounded-full border border-zinc-700/70 bg-zinc-900/70 px-3 py-1 text-[11px] font-medium text-zinc-300">
               {viewMode === 'ADDONS' ? services.length - primaryServices.length : primaryServices.length} {viewMode === 'ADDONS' ? 'addon' : 'service'}{(viewMode === 'ADDONS' ? (services.length - primaryServices.length) : primaryServices.length) === 1 ? '' : 's'}
            </span>
            <span className="hidden text-[11px] text-zinc-500 lg:inline">
              Auto-refresh every {Math.max(1, Math.round(pollIntervalMs / 1000))}s
            </span>
            {fetchError && (
              <span className="hidden rounded-full border border-amber-500/40 bg-amber-500/10 px-3 py-1 text-[11px] font-medium text-amber-200 xl:inline">
                {fetchError}
              </span>
            )}
          </div>

          <div className="flex justify-center">
            <div className="inline-flex items-center gap-1 rounded-full border border-zinc-700/70 bg-black/35 p-1 shadow-[0_0_0_1px_rgba(255,255,255,0.02)]">
              {viewTabs.map((tab) => {
                const Icon = tab.icon;
                const active = viewMode === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setViewMode(tab.id)}
                    className={`inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-xs font-semibold transition-all ${
                      active
                        ? 'bg-emerald-500/20 text-emerald-300 shadow-[inset_0_0_0_1px_rgba(52,211,153,0.35)]'
                        : 'text-zinc-400 hover:bg-zinc-800/70 hover:text-zinc-200'
                    }`}
                  >
                    <Icon size={14} />
                    {tab.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="flex items-center justify-center gap-2 md:justify-end">
            <Button
              onClick={() => router.push('/store')}
              variant="outline"
              className="h-8 rounded-full border-zinc-700 bg-zinc-900/80 px-4 text-xs font-semibold text-zinc-200 hover:bg-zinc-800 hover:text-white"
            >
              <Store className="mr-1.5 h-3.5 w-3.5" /> Templates
            </Button>
            <Button
              onClick={() => router.push('/new')}
              className="h-8 rounded-full bg-emerald-500 px-4 text-xs font-semibold text-zinc-950 shadow-lg shadow-emerald-900/30 hover:bg-emerald-400"
            >
              <Plus className="mr-1.5 h-3.5 w-3.5" /> New Service
            </Button>
          </div>
         </div>
         {resourceData?.summary && (
           <div className="mx-auto w-full max-w-[1440px] px-4 pb-3">
             <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-[11px]">
               <div className="rounded border border-zinc-700 px-2 py-1">Nodes: {resourceData.summary.total_nodes}</div>
               <div className="rounded border border-zinc-700 px-2 py-1">RAM: {Math.round(resourceData.summary.used_ram_mb)}/{Math.round(resourceData.summary.total_ram_mb)} MB</div>
               <div className="rounded border border-zinc-700 px-2 py-1">Disk: {Math.round(resourceData.summary.used_disk_gb)}/{Math.round(resourceData.summary.total_disk_gb)} GB</div>
               <div className="rounded border border-zinc-700 px-2 py-1">Healthy: {resourceData.summary.healthy_nodes}</div>
               <div className="rounded border border-zinc-700 px-2 py-1">Est. monthly: ${services.reduce((n, s) => n + Number(s.estimated_cost?.monthly || 0), 0).toFixed(2)}</div>
             </div>
           </div>
         )}
       </div>

        {/* SMSLY Ecosystem Cross-Sell */}
        <div className="mx-auto w-full max-w-[1440px] px-4">
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4 }}
          >
            <EcosystemSuggestion context="services" dismissible={true} />
          </motion.div>
        </div>

       <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5 }}
        className="relative flex-1 min-h-0 overflow-hidden bg-dot-pattern"
      >

        {viewMode === 'GRID' && (
            <div className="h-full overflow-y-auto">
                <ServicesGrid services={primaryServices} addons={addons} />
            </div>
        )}
        {viewMode === 'RADAR' && (
            <div className="h-full min-h-0">
                <FleetRadar services={primaryServices} />
            </div>
        )}
        {viewMode === 'ADDONS' && (
            <div className="h-full overflow-y-auto scrollbar-hide">
                <AddonsTab />
            </div>
        )}
      </motion.div>
    </main>
  );
}
