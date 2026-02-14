'use client';

import { useEffect, useState, useRef } from 'react';
import dynamic from 'next/dynamic';
import { DashboardShell } from '@/components/layout/DashboardShell';
import api from '@/lib/api';
import { useRouter } from 'next/navigation';

const ForceGraph3D = dynamic(() => import('react-force-graph-3d'), { ssr: false });

interface GraphNode {
    id: string;
    name: string;
    type: string;
    data?: any;
}

interface GraphLink {
    source: string;
    target: string;
}

export default function TopologyPage() {
    const router = useRouter();
    const [graphData, setGraphData] = useState<{ nodes: GraphNode[], links: GraphLink[] }>({ nodes: [], links: [] });
    const [loading, setLoading] = useState(true);
    const graphRef = useRef<any>(null);

    useEffect(() => {
        async function loadTopology() {
            try {
                const res = await api.get('/topology/');
                const nodesIn = Array.isArray(res?.data?.nodes) ? res.data.nodes : [];
                const edgesIn = Array.isArray(res?.data?.edges) ? res.data.edges : [];

                if (nodesIn.length === 0) {
                    setGraphData({ nodes: [{ id: 'empty', name: 'No services deployed', type: 'INFO' }], links: [] });
                    return;
                }

                const nodes: GraphNode[] = nodesIn.map((n: any) => ({
                    id: String(n?.id || ''),
                    name: String(n?.data?.name || n?.data?.mount_path || n?.id || 'node'),
                    type: String(n?.type || 'node'),
                    data: n?.data || {},
                })).filter((n: GraphNode) => Boolean(n.id));

                const links: GraphLink[] = edgesIn.map((e: any) => ({
                    source: String(e?.source || ''),
                    target: String(e?.target || ''),
                })).filter((l: GraphLink) => Boolean(l.source && l.target));

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
        <DashboardShell>

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
                            nodeLabel={(node: any) => {
                                const n = node as GraphNode;
                                const parts: string[] = [n.name];
                                if (n.type === 'service') {
                                    if (n.data?.port) parts.push(`port: ${n.data.port}`);
                                    if (n.data?.replicas) parts.push(`replicas: ${n.data.replicas}`);
                                }
                                if (n.type === 'addon') {
                                    if (n.data?.addon_type) parts.push(String(n.data.addon_type));
                                    if (n.data?.status) parts.push(String(n.data.status));
                                }
                                if (n.type === 'volume') {
                                    if (n.data?.size_gb) parts.push(`${n.data.size_gb}GB`);
                                }
                                return parts.join(' • ');
                            }}
                            nodeColor={(node: any) => {
                                if (node.type === 'service') return '#3b82f6'; // blue
                                if (node.type === 'addon') {
                                    const t = String(node?.data?.addon_type || '').toUpperCase();
                                    if (t === 'POSTGRES') return '#ef4444';
                                    if (t === 'REDIS') return '#22c55e';
                                    if (t === 'MYSQL') return '#f59e0b';
                                    if (t === 'MONGODB') return '#a855f7';
                                    return '#06b6d4'; // cyan
                                }
                                if (node.type === 'volume') return '#eab308'; // yellow
                                if (node.type === 'ERROR') return '#f97316'; // orange
                                return '#ffffff';
                            }}
                            nodeRelSize={6}
                            linkColor={() => '#ffffff'}
                            linkWidth={2}
                            linkDirectionalParticles={4}
                            linkDirectionalParticleSpeed={() => 0.005}
                            backgroundColor="#000000"
                            onNodeClick={(node: any) => {
                                if (node?.type === 'service' && node?.id) {
                                    router.push(`/services/${node.id}`);
                                }
                            }}
                        />
                    </div>
                )}
            </div>

        </DashboardShell>
    );
}
