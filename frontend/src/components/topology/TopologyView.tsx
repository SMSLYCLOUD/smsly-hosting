'use client';

/**
 * TopologyView — reusable canvas topology graph.
 * Re-exports the page-level canvas view for embedding in other contexts.
 * The full implementation lives in app/topology/page.tsx.
 */

import { useEffect, useState, useRef, useCallback, MouseEvent as ReactMouseEvent } from 'react';
import api from '@/lib/api';
import { useRouter } from 'next/navigation';
import { Server, Database, HardDrive, X, ExternalLink } from 'lucide-react';

/* ── Types ── */
interface TopoNode {
    id: string; name: string; type: string; data?: any;
    x: number; y: number;
}
interface TopoEdge {
    source: string; target: string; type?: string;
}

const CARD_W = 200;
const CARD_H = 80;

const THEME_COLORS: Record<string, string> = {
    service: '#3b82f6', POSTGRES: '#6366f1', REDIS: '#22c55e',
    MYSQL: '#f59e0b', MONGODB: '#a855f7', volume: '#eab308',
};
const EDGE_COLORS: Record<string, string> = {
    API: '#60a5fa', DATABASE: '#818cf8', CACHE: '#34d399',
    STORAGE: '#fbbf24', ADDON: '#64748b',
};

function getColor(node: TopoNode) {
    if (node.type === 'service') return THEME_COLORS.service;
    if (node.type === 'addon') return THEME_COLORS[(node.data?.addon_type || '').toUpperCase()] || '#3b82f6';
    return THEME_COLORS.volume;
}

function autoLayout(nodes: TopoNode[], edges: TopoEdge[]) {
    const services = nodes.filter(n => n.type === 'service');
    const GAP = 280;
    const startX = -(services.length - 1) * GAP / 2;
    services.forEach((s, i) => { s.x = startX + i * GAP; s.y = 0; });

    const children: Record<string, string[]> = {};
    services.forEach(s => { children[s.id] = []; });
    edges.forEach(e => { if (children[e.source]) children[e.source].push(e.target); });

    services.forEach(svc => {
        const ch = children[svc.id] || [];
        const cx = svc.x - (ch.length - 1) * 220 / 2;
        ch.forEach((cid, j) => {
            const child = nodes.find(n => n.id === cid);
            if (child) { child.x = cx + j * 220; child.y = svc.y + 140; }
        });
    });
}

export function TopologyView() {
    const router = useRouter();
    const [nodes, setNodes] = useState<TopoNode[]>([]);
    const [edges, setEdges] = useState<TopoEdge[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        (async () => {
            try {
                const res = await api.get('/topology/');
                const ns = (res?.data?.nodes || []).map((n: any) => ({
                    id: String(n?.id || ''), name: String(n?.data?.name || n?.id || ''),
                    type: String(n?.type || 'node'), data: n?.data || {}, x: 0, y: 0,
                })).filter((n: TopoNode) => n.id);

                const es = (res?.data?.edges || []).map((e: any) => ({
                    source: String(e?.source || ''), target: String(e?.target || ''),
                    type: String(e?.type || 'ADDON'),
                })).filter((e: TopoEdge) => e.source && e.target);

                autoLayout(ns, es);
                setNodes(ns);
                setEdges(es);
            } catch { /* ignore */ }
            finally { setLoading(false); }
        })();
    }, []);

    if (loading) return <div className="flex items-center justify-center h-64 text-zinc-500">Loading...</div>;
    if (nodes.length === 0) return <div className="flex items-center justify-center h-64 text-zinc-500">No topology data</div>;

    return (
        <div className="relative w-full h-full min-h-[400px] bg-[#0a0a0b] rounded-xl overflow-hidden"
            style={{
                backgroundImage: 'radial-gradient(circle, #ffffff06 1px, transparent 1px)',
                backgroundSize: '20px 20px',
            }}>
            <svg className="absolute inset-0 w-full h-full pointer-events-none" style={{ overflow: 'visible' }}>
                {edges.map((e, i) => {
                    const s = nodes.find(n => n.id === e.source);
                    const t = nodes.find(n => n.id === e.target);
                    if (!s || !t) return null;
                    const sx = s.x + CARD_W / 2 + 400;
                    const sy = s.y + CARD_H + 100;
                    const tx = t.x + CARD_W / 2 + 400;
                    const ty = t.y + 100;
                    const mid = (sy + ty) / 2;
                    return (
                        <path key={i}
                            d={`M ${sx} ${sy} C ${sx} ${mid}, ${tx} ${mid}, ${tx} ${ty}`}
                            fill="none" stroke={EDGE_COLORS[e.type || 'ADDON'] || '#64748b'}
                            strokeWidth={2} strokeOpacity={0.4} />
                    );
                })}
            </svg>
            <div style={{ transform: 'translate(400px, 100px)' }}>
                {nodes.map(node => (
                    <div key={node.id} className="absolute rounded-lg border overflow-hidden cursor-pointer
                        hover:border-opacity-100 transition-all"
                        style={{
                            left: node.x, top: node.y, width: CARD_W, height: CARD_H,
                            backgroundColor: '#18181b',
                            borderColor: getColor(node) + '40',
                        }}
                        onClick={() => {
                            if (node.type === 'service') router.push(`/services/${node.id}`);
                        }}>
                        <div className="h-[2px] w-full" style={{ backgroundColor: getColor(node) }} />
                        <div className="p-2.5 flex items-center gap-2">
                            <div className="w-7 h-7 rounded-md flex items-center justify-center"
                                style={{ backgroundColor: getColor(node) + '15' }}>
                                {node.type === 'service'
                                    ? <Server className="w-3.5 h-3.5" style={{ color: getColor(node) }} />
                                    : node.type === 'addon'
                                        ? <Database className="w-3.5 h-3.5" style={{ color: getColor(node) }} />
                                        : <HardDrive className="w-3.5 h-3.5" style={{ color: getColor(node) }} />}
                            </div>
                            <div>
                                <div className="text-[12px] font-semibold text-white truncate">{node.name}</div>
                                <div className="text-[9px] text-zinc-500 uppercase">
                                    {node.type === 'service' ? 'Service' : node.data?.addon_type || node.type}
                                </div>
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
