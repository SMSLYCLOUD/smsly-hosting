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
      <div className="border-b border-border bg-card/50 backdrop-blur px-6 py-3 flex justify-between items-center z-20">
        <div className="flex gap-2">
            <Button
                variant={viewMode === 'GRID' ? 'default' : 'ghost'}
                size="sm"
                onClick={() => setViewMode('GRID')}
                className="gap-2"
            >
                <LayoutGrid size={16} /> Grid
            </Button>
            <Button
                variant={viewMode === 'CANVAS' ? 'default' : 'ghost'}
                size="sm"
                onClick={() => setViewMode('CANVAS')}
                className="gap-2"
            >
                <Network size={16} /> Canvas
            </Button>
        </div>
        <div className="flex gap-2">
            <Button
                onClick={() => setViewMode('TOPOLOGY')}
                variant={viewMode === 'TOPOLOGY' ? 'default' : 'outline'}
                className="font-bold rounded-full px-6 h-8 text-xs gap-2"
            >
                <GitFork className="h-3 w-3" /> Topology
            </Button>
            <Button
                onClick={() => setViewMode('ADDONS')}
                variant={viewMode === 'ADDONS' ? 'default' : 'outline'}
                className="font-bold rounded-full px-6 h-8 text-xs gap-2"
            >
                <Puzzle className="h-3 w-3" /> Addons
            </Button>
            <Button onClick={() => router.push('/store')} variant="outline" className="font-bold rounded-full px-6 h-8 text-xs gap-2">
                <Store className="h-3 w-3" /> Templates
            </Button>
        </div>
        <Button onClick={() => router.push('/new')} className="shadow-lg bg-primary hover:bg-primary/90 text-white font-bold rounded-full px-6 h-8 text-xs">
            <Plus className="mr-2 h-3 w-3" /> New Service
        </Button>
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
