'use client';

import { useEffect, useState } from 'react';
import axios from 'axios';
import dynamic from 'next/dynamic';
import { Navbar } from '@/components/layout/Navbar';

// ForceGraph3D is client-side only
const ForceGraph3D = dynamic(() => import('react-force-graph-3d'), { ssr: false });

export default function TopologyPage() {
  const [graphData, setGraphData] = useState<any>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // In a real app, use the API. For now, we mock if no backend.
    const API_URL = process.env.NEXT_PUBLIC_API_URL || '';
    if (API_URL) {
        axios.get(API_URL + '/topology/')
        .then(res => setGraphData(res.data))
        .catch(console.error)
        .finally(() => setLoading(false));
    } else {
        // Mock data for UI preview
         setGraphData({
             nodes: [
                 { id: '1', name: 'frontend-svc', type: 'SERVICE' },
                 { id: '2', name: 'api-svc', type: 'SERVICE' },
                 { id: '3', name: 'db-primary', type: 'POSTGRES' },
                 { id: '4', name: 'cache', type: 'REDIS' }
             ],
             links: [
                 { source: '1', target: '2' },
                 { source: '2', target: '3' },
                 { source: '2', target: '4' }
             ]
         });
         setLoading(false);
    }
  }, []);

  return (
    <main className="flex min-h-screen flex-col bg-background">
      <Navbar />

      <div className="relative flex-1 bg-black overflow-hidden">
        <div className="absolute top-4 left-4 z-10 pointer-events-none">
            <h1 className="text-2xl font-bold text-white mb-2 drop-shadow-md">Infrastructure Topology</h1>
            <div className="flex gap-4 text-sm text-gray-300 drop-shadow-sm">
                <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-blue-500"></span> Service</div>
                <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-red-500"></span> Database</div>
                <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-green-500"></span> Redis</div>
            </div>
        </div>

        {loading ? (
            <div className="text-white p-24 flex items-center justify-center h-full">Loading topology...</div>
        ) : (
            <div className="w-full h-full min-h-[500px]">
                <ForceGraph3D
                    graphData={graphData}
                    nodeLabel="name"
                    nodeColor={(node: any) => {
                        if (node.type === 'SERVICE') return '#3b82f6'; // blue
                        if (node.type === 'POSTGRES') return '#ef4444'; // red
                        if (node.type === 'REDIS') return '#22c55e'; // green
                        return '#ffffff';
                    }}
                    nodeRelSize={6}
                    linkColor={() => '#ffffff'}
                    linkWidth={2}
                    linkDirectionalParticles={4}
                    linkDirectionalParticleSpeed={(d: any) => 0.005}
                    backgroundColor="#000000"
                />
            </div>
        )}
      </div>
    </main>
  );
}
