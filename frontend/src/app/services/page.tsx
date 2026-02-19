'use client';

import React, { useEffect, useState } from 'react';
import { servicesApi, Service } from '@/lib/api';
import { useRouter } from 'next/navigation';
import { Plus, LayoutGrid, Network, Store, Puzzle, GitFork } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { motion } from 'framer-motion';
import { ServicesGrid } from '@/components/dashboard/ServicesGrid';
import { AddonsTab } from '@/components/addons/AddonsTab';
import dynamic from 'next/dynamic';

const ServiceCanvas = dynamic(() => import('@/components/canvas/ServiceCanvas').then(mod => mod.ServiceCanvas), {
  loading: () => <div className="flex items-center justify-center h-full text-muted-foreground">Loading Canvas...</div>,
  ssr: false
});

const TopologyView = dynamic(() => import('@/components/topology/TopologyView').then(mod => mod.TopologyView), {
  loading: () => <div className="flex items-center justify-center h-full text-muted-foreground">Loading Topology...</div>,
  ssr: false
});

export default function ServicesPage() {
  const router = useRouter();
  const [viewMode, setViewMode] = useState<'GRID' | 'CANVAS' | 'TOPOLOGY' | 'ADDONS'>('GRID');
  const [services, setServices] = useState<Service[]>([]);
  const viewTabs: Array<{ id: 'GRID' | 'CANVAS' | 'TOPOLOGY' | 'ADDONS'; label: string; icon: React.ComponentType<{ size?: number; className?: string }> }> = [
    { id: 'GRID', label: 'Grid', icon: LayoutGrid },
    { id: 'CANVAS', label: 'Canvas', icon: Network },
    { id: 'TOPOLOGY', label: 'Topology', icon: GitFork },
    { id: 'ADDONS', label: 'Addons', icon: Puzzle },
  ];

  useEffect(() => {
    const fetchData = async () => {
      try {
        const svcs = await servicesApi.list();
        setServices(svcs);
      } catch (e) { console.error('Failed to fetch services:', e); }
    };
    fetchData();
    // Auto-refresh every 5 seconds
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <main className="h-screen flex flex-col premium-bg transition-colors duration-500">

      {/* View Toggle Bar */}
      <div className="z-20 border-b border-zinc-800/60 bg-[#070a12]/85 backdrop-blur-xl">
        <div className="mx-auto grid w-full max-w-[1440px] grid-cols-1 gap-3 px-4 py-3 md:grid-cols-[1fr_auto_1fr] md:items-center">
          <div className="flex items-center justify-center gap-2 md:justify-start">
            <span className="rounded-full border border-zinc-700/70 bg-zinc-900/70 px-3 py-1 text-[11px] font-medium text-zinc-300">
              {services.length} service{services.length === 1 ? '' : 's'}
            </span>
            <span className="hidden text-[11px] text-zinc-500 lg:inline">Auto-refresh every 5s</span>
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
      </div>

      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5 }}
        className="flex-1 relative overflow-hidden bg-dot-pattern"
      >
        {viewMode === 'CANVAS' && (
            <ServiceCanvas services={services} />
        )}
        {viewMode === 'GRID' && (
            <div className="h-full overflow-y-auto">
                <ServicesGrid services={services} />
            </div>
        )}
        {viewMode === 'TOPOLOGY' && (
            <div className="h-full">
                <TopologyView />
            </div>
        )}
        {viewMode === 'ADDONS' && (
            <div className="h-full overflow-y-auto p-6">
                <AddonsTab />
            </div>
        )}
      </motion.div>
    </main>
  );
}
