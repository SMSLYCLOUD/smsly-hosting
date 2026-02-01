'use client';

import { useEffect, useState, useRef } from 'react';
import dynamic from 'next/dynamic';
import { Navbar } from '@/components/layout/Navbar';
import { servicesApi, Service } from '@/lib/api';

const ForceGraph3D = dynamic(() => import('react-force-graph-3d'), { ssr: false });

interface GraphNode {
    id: string;
    name: string;
    type: string;
}

interface GraphLink {
    source: string;
    target: string;
}

export default function TopologyPage() {
    const [graphData, setGraphData] = useState<{ nodes: GraphNode[], links: GraphLink[] }>({ nodes: [], links: [] });
    const [loading, setLoading] = useState(true);
    const graphRef = useRef<any>(null);

    useEffect(() => {
        async function loadTopology() {
            try {
                const services = await servicesApi.list();

                if (!services || services.length === 0) {
                    // Show empty state
                    setGraphData({
                        nodes: [{ id: 'empty', name: 'No services deployed', type: 'INFO' }],
                        links: []
                    });
                    return;
                }

                // Convert services to graph nodes
                const nodes: GraphNode[] = services.map((service: Service) => ({
                    id: service.id.toString(),
                    name: service.name,
                    type: service.name.toLowerCase().includes('postgres') || service.name.toLowerCase().includes('db') ? 'POSTGRES' :
                        service.name.toLowerCase().includes('redis') || service.name.toLowerCase().includes('cache') ? 'REDIS' :
                            'SERVICE'
                }));

                // Create links between services (simplified: connect each to next)
                const links: GraphLink[] = [];
                for (let i = 0; i < nodes.length - 1; i++) {
                    links.push({
                        source: nodes[i].id,
                        target: nodes[i + 1].id
                    });
                }

                setGraphData({ nodes, links });
            } catch (error) {
                console.error('Failed to load topology:', error);
                setGraphData({
                    nodes: [{ id: 'error', name: 'Failed to load', type: 'ERROR' }],
                    links: []
                });
            } finally {
                setLoading(false);
            }
        }
        loadTopology();
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
                ) : graphData.nodes.length === 0 || graphData.nodes[0].id === 'empty' ? (
                    <div className="text-white p-24 flex flex-col items-center justify-center h-full">
                        <p className="text-xl mb-4">No services deployed yet</p>
                        <p className="text-gray-400">Deploy your first service to see the topology visualization.</p>
                    </div>
                ) : (
                    <div className="w-full h-full min-h-[500px]">
                        <ForceGraph3D
                            ref={graphRef}
                            graphData={graphData}
                            nodeLabel="name"
                            nodeColor={(node: any) => {
                                if (node.type === 'SERVICE') return '#3b82f6'; // blue
                                if (node.type === 'POSTGRES') return '#ef4444'; // red
                                if (node.type === 'REDIS') return '#22c55e'; // green
                                if (node.type === 'ERROR') return '#f97316'; // orange
                                return '#ffffff';
                            }}
                            nodeRelSize={6}
                            linkColor={() => '#ffffff'}
                            linkWidth={2}
                            linkDirectionalParticles={4}
                            linkDirectionalParticleSpeed={() => 0.005}
                            backgroundColor="#000000"
                        />
                    </div>
                )}
            </div>

        </main>
    );
}
