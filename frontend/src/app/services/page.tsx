'use client';

import React, { useEffect, useState } from 'react';
import { servicesApi, Service } from '@/lib/api';
import { useRouter } from 'next/navigation';
import { Plus, LayoutGrid, Network } from 'lucide-react';
import { Navbar } from '@/components/layout/Navbar';
import { Button } from '@/components/ui/button';
import { motion } from 'framer-motion';
import { ServicesGrid } from '@/components/dashboard/ServicesGrid';
import dynamic from 'next/dynamic';

const ServiceCanvas = dynamic(() => import('@/components/canvas/ServiceCanvas').then(mod => mod.ServiceCanvas), {
  loading: () => <div className="flex items-center justify-center h-full text-muted-foreground">Loading Canvas...</div>,
  ssr: false
});

export default function ServicesPage() {
  const router = useRouter();
  const [viewMode, setViewMode] = useState<'CANVAS' | 'GRID'>('GRID');
  const [services, setServices] = useState<Service[]>([]);

  useEffect(() => {
    const fetchData = async () => {
      const svcs = await servicesApi.list();
      setServices(svcs);
    };
    fetchData();
  }, []);

  return (
    <main className="h-screen flex flex-col premium-bg transition-colors duration-500">
      <Navbar />

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
        {viewMode === 'CANVAS' ? (
            <ServiceCanvas services={services} />
        ) : (
            <div className="h-full overflow-y-auto">
                <ServicesGrid services={services} />
            </div>
        )}
      </motion.div>
    </main>
  );
}
