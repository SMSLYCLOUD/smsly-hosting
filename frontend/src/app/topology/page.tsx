'use client';

import { useState } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Topology3D } from '@/components/topology/Topology3D';
import { CanvasSchematic } from '@/components/topology/CanvasSchematic';
import { SolarSystemView } from '@/components/topology/SolarSystemView';
import { Network, Map as MapIcon, Orbit } from 'lucide-react';

export default function TopologyPage() {
  const [view, setView] = useState<'3d' | '2d' | 'solar'>('3d');

  return (
    <DashboardShell>
       <div className="flex flex-col h-[calc(100vh-6rem)] md:h-[calc(100vh-4rem)]">
          <div className="border-b border-zinc-800 bg-[#04070f] px-4 py-3 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
             <div>
                <h1 className="text-lg font-semibold text-white tracking-tight">Infrastructure Topology</h1>
                <p className="text-sm text-zinc-500">Visualize your services and their dependencies</p>
             </div>

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
             </div>
          </div>

          <div className="flex-1 relative overflow-hidden bg-[#04070f]">
             {view === '3d' && <Topology3D />}
             {view === '2d' && <CanvasSchematic />}
             {view === 'solar' && <SolarSystemView />}
          </div>
       </div>
    </DashboardShell>
  );
}
