'use client';

import { useState } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Topology3D } from '@/components/topology/Topology3D';
import { CanvasSchematic } from '@/components/topology/CanvasSchematic';
import { SolarSystemView } from '@/components/topology/SolarSystemView';
import CityTopologyView from '@/components/topology/CityTopologyView';
import { Network, Map as MapIcon, Orbit, Building, Trash2, Loader2 } from 'lucide-react';
import { RequiresTier } from '@/components/licensing/RequiresTier';
import { servicesApi } from '@/lib/api';
import { toast } from 'sonner';

export default function TopologyPage() {
  const [view, setView] = useState<'3d' | '2d' | 'solar' | 'city'>('3d');
  const [isPruning, setIsPruning] = useState(false);

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
              </div>
           </div>
        </div>

          <div className="flex-1 relative overflow-hidden bg-[#04070f]">
             {view === '3d' && <Topology3D />}
             {view === '2d' && <CanvasSchematic />}
             {view === 'solar' && <SolarSystemView />}
             {view === 'city' && <CityTopologyView />}
          </div>
       </div>
      </RequiresTier>
    </DashboardShell>
  );
}
