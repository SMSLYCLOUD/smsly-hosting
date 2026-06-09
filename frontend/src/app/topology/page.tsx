'use client';

import { useState, useMemo, useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Topology3D } from '@/components/topology/Topology3D';
import { CanvasSchematic } from '@/components/topology/CanvasSchematic';
import { SolarSystemView } from '@/components/topology/SolarSystemView';
import CityTopologyView from '@/components/topology/CityTopologyView';
import { EcosystemTopology } from '@/components/topology/EcosystemTopology';
import { Network, Map as MapIcon, Orbit, Building, Trash2, Loader2, Layers } from 'lucide-react';
import { RequiresTier } from '@/components/licensing/RequiresTier';
import { servicesApi } from '@/lib/api';
import { toast } from 'sonner';
import { useGraphData } from '@/hooks/useGraphData';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

export default function TopologyPage({
    searchParams,
}: {
    searchParams?: { service?: string };
}) {
  const [view, setView] = useState<'3d' | '2d' | 'solar' | 'city' | 'ecosystem'>('3d');
  const [isPruning, setIsPruning] = useState(false);
  const [selectedProject, setSelectedProject] = useState<string>('all');
  const [selectedService, setSelectedService] = useState<string>('all');

  const { data, loading, error, refresh } = useGraphData();

  // Resolve ?service=UUID to service name for filtering
  useEffect(() => {
    const raw = searchParams?.service;
    if (!raw) return;
    const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
    fetch(`/api/v1/services/${encodeURIComponent(raw)}/`, {
      headers: token ? { 'Authorization': `Token ${token}` } : {},
    })
      .then(r => r.ok ? r.json() : null)
      .then(svc => { if (svc?.name) setSelectedService(svc.name); })
      .catch(() => {});
  }, [searchParams?.service]);

  const uniqueProjects = useMemo(() => {
    return Array.from(new Set(data?.nodes.map(n => n.data?.project_name).filter(Boolean))) as string[];
  }, [data]);

  const uniqueServices = useMemo(() => {
    return Array.from(new Set(data?.nodes.filter(n => n.type?.toUpperCase() === 'SERVICE').map(n => n.data?.name).filter(Boolean))) as string[];
  }, [data]);

  const filteredData = useMemo(() => {
    if (!data) return null;
    let nodes = data.nodes;
    let edges = data.edges;

    if (selectedProject !== 'all') {
      const projNodes = nodes.filter(n => n.data?.project_name === selectedProject);
      const projNodeIds = new Set(projNodes.map(n => n.id));
      const connectedNodeIds = new Set([
        ...edges.filter(e => projNodeIds.has(e.source)).map(e => e.target),
        ...edges.filter(e => projNodeIds.has(e.target)).map(e => e.source)
      ]);
      
      nodes = nodes.filter(n => projNodeIds.has(n.id) || connectedNodeIds.has(n.id));
      const allowedIds = new Set(nodes.map(n => n.id));
      edges = edges.filter(e => allowedIds.has(e.source) && allowedIds.has(e.target));
    }

    if (selectedService !== 'all') {
      const srvNodes = nodes.filter(n => n.data?.name === selectedService);
      const srvNodeIds = new Set(srvNodes.map(n => n.id));
      const connectedNodeIds = new Set([
        ...edges.filter(e => srvNodeIds.has(e.source)).map(e => e.target),
        ...edges.filter(e => srvNodeIds.has(e.target)).map(e => e.source)
      ]);
      
      nodes = nodes.filter(n => srvNodeIds.has(n.id) || connectedNodeIds.has(n.id));
      const allowedIds = new Set(nodes.map(n => n.id));
      edges = edges.filter(e => allowedIds.has(e.source) && allowedIds.has(e.target));
    }

    return { nodes, edges };
  }, [data, selectedProject, selectedService]);

  const handlePrune = async () => {
    if (!confirm('Are you sure you want to clear all failed deployments and containers? This will also attempt to free up disk space on the VPS.')) {
      return;
    }
    
    setIsPruning(true);
    try {
      const res = await servicesApi.pruneDeployments();
      toast.success(
        `Cleanup complete: ${res.deployments_deleted} deployments cleared, ${res.containers_removed} containers removed. Reclaimed ${res.space_reclaimed_mb}MB.`
      );
      // Trigger global refresh for all topology views
      window.dispatchEvent(new CustomEvent('smsly:topology-refresh'));
    } catch (err) {
      toast.error('Failed to prune deployments');
      console.error(err);
    } finally {
      setIsPruning(false);
    }
  };

  return (
    <DashboardShell>
      <RequiresTier tier="pro">
       <div className="flex flex-col h-[calc(100vh-6rem)] md:h-[calc(100vh-4rem)]">
          <div className="border-b border-zinc-800 bg-[#04070f] px-4 py-3 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
             <div>
                <h1 className="text-lg font-semibold text-white tracking-tight">Infrastructure Topology</h1>
                <p className="text-sm text-zinc-500">Visualize your services and their dependencies</p>
             </div>

             <div className="flex items-center gap-4">
                <button
                  onClick={handlePrune}
                  disabled={isPruning}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium transition-all bg-red-950/30 text-red-400 border border-red-900/30 hover:bg-red-900/40 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isPruning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                  {isPruning ? 'Pruning...' : 'Clear Failed'}
                </button>

                {/* Filters */}
                {view !== 'ecosystem' && (
                  <div className="flex items-center gap-2">
                    <Select value={selectedProject} onValueChange={(v) => { setSelectedProject(v); setSelectedService('all'); }}>
                      <SelectTrigger className="w-[140px] h-8 text-xs bg-black/40 border-zinc-800 text-zinc-300">
                        <SelectValue placeholder="Project" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All Projects</SelectItem>
                        {uniqueProjects.map(p => (
                          <SelectItem key={p} value={p}>{p}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    
                    <Select value={selectedService} onValueChange={setSelectedService}>
                      <SelectTrigger className="w-[140px] h-8 text-xs bg-black/40 border-zinc-800 text-zinc-300">
                        <SelectValue placeholder="Service" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All Services</SelectItem>
                        {uniqueServices.map(s => (
                          <SelectItem key={s} value={s}>{s}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}

                <div className="flex bg-zinc-900/50 rounded-lg p-1 border border-zinc-800 backdrop-blur-sm">
                <button
                  onClick={() => setView('3d')}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${view === '3d' ? 'bg-zinc-800 text-white shadow-sm border border-zinc-700/50' : 'text-zinc-400 hover:text-zinc-200'}`}
                >
                   <Network className="w-3.5 h-3.5" /> 3D Graph
                </button>
                <button
                  onClick={() => setView('2d')}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${view === '2d' ? 'bg-zinc-800 text-white shadow-sm border border-zinc-700/50' : 'text-zinc-400 hover:text-zinc-200'}`}
                >
                   <MapIcon className="w-3.5 h-3.5" /> Schematic
                </button>
                <button
                  onClick={() => setView('solar')}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${view === 'solar' ? 'bg-zinc-800 text-white shadow-sm border border-zinc-700/50' : 'text-zinc-400 hover:text-zinc-200'}`}
                >
                   <Orbit className="w-3.5 h-3.5" /> Solar System
                </button>
                <button
                  onClick={() => setView('city')}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${view === 'city' ? 'bg-zinc-800 text-white shadow-sm border border-zinc-700/50' : 'text-zinc-400 hover:text-zinc-200'}`}
                >
                   <Building className="w-3.5 h-3.5" /> City
                 </button>
                <button
                  onClick={() => setView('ecosystem')}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${view === 'ecosystem' ? 'bg-zinc-800 text-white shadow-sm border border-zinc-700/50' : 'text-zinc-400 hover:text-zinc-200'}`}
                >
                   <Layers className="w-3.5 h-3.5" /> Ecosystem
                 </button>
              </div>
           </div>
        </div>

          <div className="flex-1 relative overflow-hidden bg-[#04070f]">
             {view === '3d' && <Topology3D data={filteredData} loading={loading} error={error} refresh={refresh} />}
             {view === '2d' && <CanvasSchematic data={filteredData} loading={loading} error={error} />}
             {view === 'solar' && <SolarSystemView data={filteredData} loading={loading} error={error} />}
             {view === 'city' && <CityTopologyView data={filteredData} loading={loading} error={error} />}
             {view === 'ecosystem' && <EcosystemTopology />}
          </div>
       </div>
      </RequiresTier>
    </DashboardShell>
  );
}
