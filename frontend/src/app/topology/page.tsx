'use client';

import { useEffect, useState, useRef } from 'react';
import axios from 'axios';
import dynamic from 'next/dynamic';

// ForceGraph3D is client-side only
const ForceGraph3D = dynamic(() => import('react-force-graph-3d'), { ssr: false });

export default function TopologyPage() {
  const [graphData, setGraphData] = useState({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    axios.get(process.env.NEXT_PUBLIC_API_URL + '/topology/')
      .then(res => setGraphData(res.data))
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="flex min-h-screen flex-col bg-black">
      <div className="absolute top-4 left-4 z-10">
        <h1 className="text-2xl font-bold text-white mb-2">Infrastructure Topology</h1>
        <div className="flex gap-4 text-sm text-gray-300">
            <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-blue-500"></span> Service</div>
            <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-red-500"></span> Database</div>
            <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-green-500"></span> Redis</div>
        </div>
      </div>

      {loading ? (
          <div className="text-white p-24">Loading topology...</div>
      ) : (
          <div className="w-full h-screen">
            <ForceGraph3D
                graphData={graphData}
                nodeLabel="name"
                nodeColor={node => {
                    if (node.type === 'SERVICE') return '#3b82f6'; // blue
                    if (node.type === 'POSTGRES') return '#ef4444'; // red
                    if (node.type === 'REDIS') return '#22c55e'; // green
                    return '#ffffff';
                }}
                nodeRelSize={6}
                linkColor={() => '#ffffff'}
                linkWidth={2}
                linkDirectionalParticles={4} // Traffic simulation
                linkDirectionalParticleSpeed={d => 0.005} // Consistent flow
                backgroundColor="#000000"
            />
          </div>
      )}
    </main>
  );
}
