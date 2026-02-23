'use client';

/**
 * TopologyPage — Full 3D force graph of ALL services + addons + volumes.
 * Powered by react-force-graph-3d (Three.js / WebGL).
 * Full 360° orbit, zoom, pan. Click node to navigate.
 */

import { useEffect, useState, useRef, useCallback } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import api from '@/lib/api';
import { useRouter } from 'next/navigation';
import dynamic from 'next/dynamic';
import {
    Server, Database, HardDrive, Activity, Wifi,
    ExternalLink, RefreshCw, ZoomIn, ZoomOut, Maximize2, X, Loader2
} from 'lucide-react';

// @ts-ignore — dynamic import for SSR-safe WebGL
const ForceGraph3D = dynamic(() => import('react-force-graph-3d'), { ssr: false });

/* ── Types ──────────────────────────────────────────────── */
interface TopoNode {
    id: string;
    name: string;
    nodeType: 'service' | 'addon' | 'volume';
    subType?: string;
    status?: string;
    health?: string;
    detail?: string;
    data?: any;
}

interface TopoLink {
    source: string;
    target: string;
    linkType: string;
}

/* ── Colors ─────────────────────────────────────────────── */
const NODE_COLORS: Record<string, string> = {
    service: '#3b82f6',
    POSTGRES: '#818cf8',
    REDIS: '#22c55e',
    MYSQL: '#f59e0b',
    MONGODB: '#a855f7',
    ELASTICSEARCH: '#06b6d4',
    RABBITMQ: '#f97316',
    MINIO: '#f472b6',
    QDRANT: '#a78bfa',
    MEMCACHED: '#94a3b8',
    CLICKHOUSE: '#facc15',
    volume: '#eab308',
    default: '#6366f1',
};

const LINK_COLORS: Record<string, string> = {
    API: '#60a5fa',
    DATABASE: '#818cf8',
    CACHE: '#34d399',
    QUEUE: '#fb923c',
    SEARCH: '#22d3ee',
    STORAGE: '#fbbf24',
    ADDON: '#64748b',
};

const HEALTH_COLORS: Record<string, string> = {
    healthy: '#22c55e',
    unhealthy: '#ef4444',
    starting: '#f59e0b',
    unknown: '#52525b',
};

const DEPLOY_LABELS: Record<string, { color: string; label: string }> = {
    ACTIVE:    { color: '#22c55e', label: 'Active' },
    BUILDING:  { color: '#f59e0b', label: 'Building' },
    DEPLOYING: { color: '#3b82f6', label: 'Deploying' },
    FAILED:    { color: '#ef4444', label: 'Failed' },
    QUEUED:    { color: '#8b5cf6', label: 'Queued' },
    NONE:      { color: '#52525b', label: 'No Deploy' },
};

function getNodeColor(node: TopoNode): string {
    if (node.nodeType === 'service') return NODE_COLORS.service;
    if (node.nodeType === 'addon') return NODE_COLORS[node.subType?.toUpperCase() || ''] || NODE_COLORS.default;
    return NODE_COLORS.volume;
}

/* ── 3D Node builder (lazy THREE) ── */
function createNode3D(node: TopoNode): any {
    const THREE = require('three');
    const group = new THREE.Group();
    const color = getNodeColor(node);

    // Main geometry per node type
    let geometry;
    if (node.nodeType === 'service') {
        // Nucleus: large glowing sphere
        geometry = new THREE.SphereGeometry(11, 32, 32);
    } else if (node.nodeType === 'addon') {
        // Electron: smaller sphere
        geometry = new THREE.SphereGeometry(6, 16, 16);
    } else {
        geometry = new THREE.BoxGeometry(6, 6, 6);
    }

    const material = new THREE.MeshPhongMaterial({
        color: new THREE.Color(color),
        emissive: new THREE.Color(color),
        emissiveIntensity: node.nodeType === 'service' ? 0.5 : 0.35,
        transparent: true,
        opacity: 0.9,
        shininess: 120,
    });
    group.add(new THREE.Mesh(geometry, material));

    // Outer glow shell
    const glowSize = node.nodeType === 'service' ? 16 : 8;
    const glowGeo = new THREE.SphereGeometry(glowSize, 16, 16);
    const glowMat = new THREE.MeshBasicMaterial({
        color: new THREE.Color(color), transparent: true, opacity: 0.08, side: THREE.BackSide,
    });
    group.add(new THREE.Mesh(glowGeo, glowMat));

    // Electron shell rings for services
    if (node.nodeType === 'service') {
        for (let i = 0; i < 3; i++) {
            const ringGeo = new THREE.RingGeometry(15 + i * 2.5, 15.5 + i * 2.5, 64);
            const ringMat = new THREE.MeshBasicMaterial({
                color: new THREE.Color(color), transparent: true, opacity: 0.12 - i * 0.03, side: THREE.DoubleSide,
            });
            const ring = new THREE.Mesh(ringGeo, ringMat);
            ring.rotation.x = Math.PI / 2 + (i * Math.PI / 4);
            ring.rotation.y = i * Math.PI / 3;
            group.add(ring);
        }
    }

    // Health/status indicator dot
    const healthColor = node.health ? (HEALTH_COLORS[node.health] || HEALTH_COLORS.unknown)
        : node.status === 'ACTIVE' ? '#22c55e'
        : node.status === 'FAILED' ? '#ef4444'
        : '#fbbf24';
    const dotGeo = new THREE.SphereGeometry(1.5, 8, 8);
    const dotMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(healthColor) });
    const dot = new THREE.Mesh(dotGeo, dotMat);
    dot.position.set(node.nodeType === 'service' ? 11 : 7, node.nodeType === 'service' ? 7 : 5, 0);
    group.add(dot);

    // Text label sprite
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d')!;
    canvas.width = 400;
    canvas.height = 100;
    ctx.fillStyle = 'transparent';
    ctx.fillRect(0, 0, 400, 100);

    // Name
    ctx.font = `bold ${node.nodeType === 'service' ? 30 : 24}px Inter, system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.fillStyle = '#ffffff';
    const displayName = node.name.length > 22 ? node.name.slice(0, 20) + '…' : node.name;
    ctx.fillText(displayName, 200, 36);

    // Type label
    ctx.font = '18px Inter, system-ui, sans-serif';
    ctx.fillStyle = color;
    const label = node.nodeType === 'service' ? 'SERVICE'
        : node.nodeType === 'addon' ? (node.subType || 'ADDON').toUpperCase()
        : 'VOLUME';
    ctx.fillText(label, 200, 60);

    // Detail / status
    const statusText = node.nodeType === 'service'
        ? (node.status || 'UNKNOWN')
        : node.detail || '';
    if (statusText) {
        ctx.font = '14px Inter, system-ui, sans-serif';
        ctx.fillStyle = '#a1a1aa';
        ctx.fillText(statusText.length > 30 ? statusText.slice(0, 28) + '…' : statusText, 200, 82);
    }

    const texture = new THREE.CanvasTexture(canvas);
    const spriteMat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
    const sprite = new THREE.Sprite(spriteMat);
    sprite.scale.set(40, 10, 1);
    sprite.position.set(0, node.nodeType === 'service' ? -17 : -13, 0);
    group.add(sprite);

    return group;
}

/* ── Main Page ──────────────────────────────────────────── */
export default function TopologyPage() {
    const router = useRouter();
    const containerRef = useRef<HTMLDivElement>(null);
    const fgRef = useRef<any>(null);
    const hasInitialFitRef = useRef(false);
    const nodeObjectCacheRef = useRef<Map<string, { key: string; object: any }>>(new Map());

    const [graphData, setGraphData] = useState<{ nodes: TopoNode[]; links: TopoLink[] }>({ nodes: [], links: [] });
    const [loading, setLoading] = useState(true);
    const [autoRefresh, setAutoRefresh] = useState(true);
    const [selectedNode, setSelectedNode] = useState<TopoNode | null>(null);
    const [dimensions, setDimensions] = useState({ width: 0, height: 0 });

    /* ── Resize observer ── */
    const measureContainer = useCallback(() => {
        const el = containerRef.current;
        if (!el) return;
        const rect = el.getBoundingClientRect();
        setDimensions(prev => {
            const w = Math.max(Math.floor(rect.width), 480);
            const h = Math.max(Math.floor(rect.height), 420);
            return (prev.width === w && prev.height === h) ? prev : { width: w, height: h };
        });
    }, []);

    useEffect(() => {
        measureContainer();
        const el = containerRef.current;
        if (!el) return;
        const obs = new ResizeObserver(() => measureContainer());
        obs.observe(el);
        window.addEventListener('resize', measureContainer);
        // Pulse measure a few frames for layout stabilization
        let rafId = 0;
        let runs = 0;
        const pulse = () => { measureContainer(); runs++; if (runs < 6) rafId = requestAnimationFrame(pulse); };
        rafId = requestAnimationFrame(pulse);
        return () => { cancelAnimationFrame(rafId); obs.disconnect(); window.removeEventListener('resize', measureContainer); };
    }, [measureContainer]);

    /* ── Load Data ── */
    const loadTopology = useCallback(async () => {
        try {
            const res = await api.get('/topology/');
            const nodesIn = Array.isArray(res?.data?.nodes) ? res.data.nodes : [];
            const edgesIn = Array.isArray(res?.data?.edges) ? res.data.edges : [];

            const nodes: TopoNode[] = nodesIn
                .filter((n: any) => n?.id)
                .map((n: any) => ({
                    id: String(n.id),
                    name: String(n.data?.name || n.data?.mount_path || n.id || 'node'),
                    nodeType: n.type === 'service' ? 'service' as const
                        : n.type === 'addon' ? 'addon' as const
                        : 'volume' as const,
                    subType: n.data?.addon_type,
                    status: n.data?.deploy_status || n.data?.status || 'UNKNOWN',
                    health: n.data?.health,
                    detail: n.type === 'volume' ? `${n.data?.mount_path || ''} · ${n.data?.size_gb || 0}GB` : undefined,
                    data: n.data || {},
                }));

            const links: TopoLink[] = edgesIn
                .filter((e: any) => e?.source && e?.target)
                .map((e: any) => ({
                    source: String(e.source),
                    target: String(e.target),
                    linkType: String(e.type || 'ADDON'),
                }));

            // Only update if data actually changed (prevents 3D graph from re-rendering)
            setGraphData(prev => {
                const prevKeys = prev.nodes.map(n => n.id + n.status).join(',');
                const nextKeys = nodes.map(n => n.id + n.status).join(',');
                if (prevKeys === nextKeys && prev.links.length === links.length) return prev;
                nodeObjectCacheRef.current.clear();
                hasInitialFitRef.current = false;
                return { nodes, links };
            });
        } catch (error) {
            console.error('Failed to load topology:', error);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadTopology(); }, [loadTopology]);

    useEffect(() => {
        if (!autoRefresh) return;
        const iv = setInterval(loadTopology, 15000);
        return () => clearInterval(iv);
    }, [autoRefresh, loadTopology]);

    /* ── Camera ── */
    useEffect(() => {
        if (fgRef.current && graphData.nodes.length > 0) {
            const fg = fgRef.current;
            const dist = 120 + graphData.nodes.length * 25;

            fg.cameraPosition({ x: dist * 0.6, y: dist * 0.4, z: dist }, { x: 0, y: 0, z: 0 }, 1500);
            fg.d3Force('charge')?.strength(-300);
            fg.d3Force('link')?.distance(50);
            fg.d3ReheatSimulation?.();

            // Auto-orbit
            let angle = 0;
            const radius = dist;
            const rotateInterval = setInterval(() => {
                angle += 0.003;
                fg.cameraPosition(
                    { x: radius * Math.sin(angle), y: 50, z: radius * Math.cos(angle) },
                    { x: 0, y: 0, z: 0 }
                );
            }, 30);

            // Stop orbit on interaction
            const container = containerRef.current;
            const stop = () => clearInterval(rotateInterval);
            container?.addEventListener('mousedown', stop, { once: true });
            container?.addEventListener('touchstart', stop, { once: true });

            // Fit after settle
            const fitTimer = setTimeout(() => {
                if (!hasInitialFitRef.current && typeof fg.zoomToFit === 'function') {
                    try { fg.zoomToFit(700, 100); } catch { /* ignore */ }
                    hasInitialFitRef.current = true;
                }
            }, 300);

            return () => {
                clearInterval(rotateInterval);
                clearTimeout(fitTimer);
                container?.removeEventListener('mousedown', stop);
                container?.removeEventListener('touchstart', stop);
            };
        }
    }, [graphData]);

    /* ── Zoom helpers ── */
    const zoomCamera = useCallback((factor: number) => {
        const fg = fgRef.current;
        if (!fg) return;
        const camera = fg.camera?.();
        if (!camera?.position) return;
        const { x, y, z } = camera.position;
        const dist = Math.sqrt(x * x + y * y + z * z) || 1;
        const next = Math.max(60, Math.min(2000, dist * factor));
        const s = next / dist;
        fg.cameraPosition({ x: x * s, y: y * s, z: z * s }, { x: 0, y: 0, z: 0 }, 300);
    }, []);

    const resetCamera = useCallback(() => {
        const fg = fgRef.current;
        if (!fg) return;
        const dist = 120 + graphData.nodes.length * 25;
        fg.cameraPosition({ x: dist * 0.6, y: dist * 0.4, z: dist }, { x: 0, y: 0, z: 0 }, 450);
    }, [graphData.nodes.length]);

    /* ── Node object with cache ── */
    const getNodeObject = useCallback((node: any) => {
        const n = node as TopoNode;
        const key = `${n.id}:${n.name}:${n.status}:${n.subType || ''}`;
        const cached = nodeObjectCacheRef.current.get(n.id);
        if (cached?.key === key) return cached.object;
        const obj = createNode3D(n);
        nodeObjectCacheRef.current.set(n.id, { key, object: obj });
        return obj;
    }, []);

    /* ── Node click ── */
    const handleNodeClick = useCallback((node: any) => {
        const n = node as TopoNode;
        setSelectedNode(n);
    }, []);

    /* ── Legend data ── */
    const legendItems = [
        { color: NODE_COLORS.service, label: 'Service', icon: Server },
        ...graphData.nodes
            .filter(n => n.nodeType === 'addon')
            .map(n => ({ color: getNodeColor(n), label: (n.subType || 'addon').toUpperCase(), icon: Database }))
            .filter((v, i, a) => a.findIndex(x => x.label === v.label) === i),
        ...(graphData.nodes.some(n => n.nodeType === 'volume')
            ? [{ color: NODE_COLORS.volume, label: 'Volume', icon: HardDrive }] : []),
    ];

    /* ── Render ── */
    const isEmpty = graphData.nodes.length === 0;

    return (
        <DashboardShell>
            <div ref={containerRef} className="relative flex-1 bg-[#04070f] overflow-hidden">
                {/* Ambient glow backgrounds */}
                <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(59,130,246,0.08),transparent_45%),radial-gradient(circle_at_80%_30%,rgba(99,102,241,0.09),transparent_42%),radial-gradient(circle_at_50%_85%,rgba(16,185,129,0.06),transparent_48%)]" />
                <div className="pointer-events-none absolute left-1/2 top-1/2 h-[34rem] w-[34rem] -translate-x-1/2 -translate-y-1/2 rounded-full border border-zinc-700/20" />
                <div className="pointer-events-none absolute left-1/2 top-1/2 h-[24rem] w-[24rem] -translate-x-1/2 -translate-y-1/2 rounded-full border border-zinc-700/10" />

                {/* Header */}
                <div className="absolute top-4 left-4 z-30 pointer-events-none">
                    <div className="flex items-center gap-3">
                        <div className="w-9 h-9 rounded-xl bg-indigo-600/15 flex items-center justify-center border border-indigo-600/20">
                            <Activity className="w-4.5 h-4.5 text-indigo-400" />
                        </div>
                        <div>
                            <h1 className="text-lg font-bold text-white tracking-tight">Topology</h1>
                            <p className="text-[11px] text-zinc-600">
                                {graphData.nodes.filter(n => n.nodeType === 'service').length} services
                                {' · '}
                                {graphData.links.length} connections
                                {' · 3D'}
                            </p>
                        </div>
                    </div>
                </div>

                {/* Legend */}
                <div className="absolute left-4 top-16 z-20 w-40 rounded-xl border border-zinc-800/80 bg-black/60 p-3 backdrop-blur-lg">
                    <div className="text-[9px] text-zinc-400 uppercase tracking-wider mb-1.5 font-semibold">Legend</div>
                    <div className="flex flex-col gap-1">
                        {legendItems.map(({ color, label }) => (
                            <div key={label} className="flex items-center gap-1.5">
                                <div className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
                                <span className="text-[10px] text-zinc-300">{label}</span>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Controls */}
                <div className="absolute top-4 right-4 z-30 flex items-center gap-1.5">
                    <button
                        onClick={() => setAutoRefresh(!autoRefresh)}
                        className={`p-2 rounded-lg border text-xs transition-all ${autoRefresh
                            ? 'bg-emerald-950/50 border-emerald-800/40 text-emerald-400'
                            : 'bg-zinc-900/80 border-zinc-800/50 text-zinc-500'
                        }`}
                        title={autoRefresh ? 'Live (15s)' : 'Paused'}
                    >
                        <Wifi className={`w-3.5 h-3.5 ${autoRefresh ? 'animate-pulse' : ''}`} />
                    </button>
                    <button onClick={loadTopology}
                        className="p-2 rounded-lg bg-zinc-900/80 border border-zinc-800/50 text-zinc-500 hover:text-white transition-colors"
                        title="Refresh">
                        <RefreshCw className="w-3.5 h-3.5" />
                    </button>
                    <div className="w-px h-5 bg-zinc-800 mx-1" />
                    <button onClick={() => zoomCamera(0.82)}
                        className="p-2 rounded-lg bg-zinc-900/80 border border-zinc-800/50 text-zinc-500 hover:text-white transition-colors"
                        title="Zoom in" aria-label="Zoom in">
                        <ZoomIn className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => zoomCamera(1.22)}
                        className="p-2 rounded-lg bg-zinc-900/80 border border-zinc-800/50 text-zinc-500 hover:text-white transition-colors"
                        title="Zoom out" aria-label="Zoom out">
                        <ZoomOut className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={resetCamera}
                        className="p-2 rounded-lg bg-zinc-900/80 border border-zinc-800/50 text-zinc-500 hover:text-white transition-colors"
                        title="Reset view" aria-label="Reset view">
                        <Maximize2 className="w-3.5 h-3.5" />
                    </button>
                </div>

                {/* Help */}
                <div className="absolute bottom-4 right-4 z-20 rounded-xl border border-zinc-800/80 bg-black/55 px-3 py-2 backdrop-blur-lg">
                    <div className="text-[10px] text-zinc-400">Drag to orbit | Scroll to zoom | Click node for details</div>
                </div>

                {/* Content */}
                {loading ? (
                    <div className="flex items-center justify-center h-full gap-3">
                        <Loader2 className="w-5 h-5 animate-spin text-blue-500" />
                        <span className="text-zinc-500 text-sm">Loading 3D topology...</span>
                    </div>
                ) : isEmpty ? (
                    <div className="flex flex-col items-center justify-center h-full gap-4">
                        <div className="w-16 h-16 rounded-2xl bg-zinc-900 border border-zinc-800 flex items-center justify-center">
                            <Server className="w-8 h-8 text-zinc-700" />
                        </div>
                        <p className="text-base font-medium text-zinc-500">No services deployed</p>
                        <p className="text-sm text-zinc-700">Deploy a service to see the 3D topology.</p>
                    </div>
                ) : (
                    <div className="absolute inset-0">
                        {dimensions.width > 0 && dimensions.height > 0 && (
                            <ForceGraph3D
                                ref={fgRef}
                                width={dimensions.width}
                                height={dimensions.height}
                                graphData={graphData}
                                backgroundColor="#04070f"
                                nodeThreeObject={getNodeObject}
                                nodeThreeObjectExtend={false}
                                onNodeClick={handleNodeClick}
                                linkColor={(link: any) => LINK_COLORS[link.linkType] || '#64748b'}
                                linkWidth={3.5}
                                linkOpacity={0.6}
                                linkCurvature={0.15}
                                linkDirectionalParticles={5}
                                linkDirectionalParticleWidth={3}
                                linkDirectionalParticleSpeed={0.008}
                                linkDirectionalParticleColor={(link: any) => LINK_COLORS[link.linkType] || '#64748b'}
                                enableNodeDrag={true}
                                enableNavigationControls={true}
                                showNavInfo={false}
                                warmupTicks={30}
                                cooldownTicks={60}
                            />
                        )}
                    </div>
                )}

                {/* Detail Panel */}
                {selectedNode && (
                    <div className="absolute top-14 right-4 z-30 w-72 bg-zinc-900/95 backdrop-blur-xl
                        border border-zinc-800/60 rounded-xl shadow-2xl shadow-black/50
                        animate-in slide-in-from-right-3 fade-in duration-200">

                        <div className="p-3 border-b border-zinc-800/40 flex items-center justify-between">
                            <div className="flex items-center gap-2">
                                <div
                                    className="w-7 h-7 rounded-lg flex items-center justify-center"
                                    style={{ backgroundColor: getNodeColor(selectedNode) + '15' }}
                                >
                                    {selectedNode.nodeType === 'service'
                                        ? <Server className="w-3.5 h-3.5" style={{ color: getNodeColor(selectedNode) }} />
                                        : selectedNode.nodeType === 'addon'
                                            ? <Database className="w-3.5 h-3.5" style={{ color: getNodeColor(selectedNode) }} />
                                            : <HardDrive className="w-3.5 h-3.5" style={{ color: getNodeColor(selectedNode) }} />
                                    }
                                </div>
                                <div>
                                    <div className="text-xs font-semibold text-white">{selectedNode.name}</div>
                                    <div className="text-[10px] text-zinc-600 uppercase">{selectedNode.nodeType}</div>
                                </div>
                            </div>
                            <button onClick={() => setSelectedNode(null)}
                                className="w-6 h-6 rounded flex items-center justify-center hover:bg-zinc-800 text-zinc-600 hover:text-white transition-colors">
                                <X className="w-3.5 h-3.5" />
                            </button>
                        </div>

                        <div className="p-3 space-y-2 text-xs">
                            {selectedNode.nodeType === 'service' && (
                                <>
                                    <div className="flex justify-between">
                                        <span className="text-zinc-600">Health</span>
                                        <div className="flex items-center gap-1.5">
                                            <div className="w-1.5 h-1.5 rounded-full"
                                                style={{ backgroundColor: HEALTH_COLORS[selectedNode.health || ''] || HEALTH_COLORS.unknown }} />
                                            <span className="text-zinc-300 capitalize">{selectedNode.health || 'unknown'}</span>
                                        </div>
                                    </div>
                                    <div className="flex justify-between">
                                        <span className="text-zinc-600">Status</span>
                                        <span style={{ color: (DEPLOY_LABELS[selectedNode.status || ''] || DEPLOY_LABELS.NONE).color }}>
                                            {(DEPLOY_LABELS[selectedNode.status || ''] || DEPLOY_LABELS.NONE).label}
                                        </span>
                                    </div>
                                    {selectedNode.data?.domain && (
                                        <div className="flex justify-between">
                                            <span className="text-zinc-600">Domain</span>
                                            <span className="text-blue-400 truncate max-w-[140px]">{selectedNode.data.domain}</span>
                                        </div>
                                    )}
                                    {selectedNode.data?.port && (
                                        <div className="flex justify-between">
                                            <span className="text-zinc-600">Port</span>
                                            <span className="text-zinc-300 font-mono">:{selectedNode.data.port}</span>
                                        </div>
                                    )}
                                    {selectedNode.data?.deploy_commit && (
                                        <div className="flex justify-between">
                                            <span className="text-zinc-600">Commit</span>
                                            <code className="text-zinc-400 font-mono">{selectedNode.data.deploy_commit.substring(0, 7)}</code>
                                        </div>
                                    )}
                                    <div className="pt-2">
                                        <button
                                            onClick={() => router.push(`/services/${selectedNode.id}`)}
                                            className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5
                                                bg-blue-600/10 hover:bg-blue-600/20 text-blue-400
                                                text-xs font-medium rounded-lg transition-colors border border-blue-600/20">
                                            <ExternalLink className="w-3 h-3" /> View Service
                                        </button>
                                    </div>
                                </>
                            )}
                            {selectedNode.nodeType === 'addon' && (
                                <>
                                    <div className="flex justify-between">
                                        <span className="text-zinc-600">Type</span>
                                        <span className="text-zinc-300">{selectedNode.subType?.toUpperCase()}</span>
                                    </div>
                                    <div className="flex justify-between">
                                        <span className="text-zinc-600">Status</span>
                                        <span className="text-zinc-300">{selectedNode.status}</span>
                                    </div>
                                </>
                            )}
                            {selectedNode.nodeType === 'volume' && (
                                <>
                                    <div className="flex justify-between">
                                        <span className="text-zinc-600">Mount</span>
                                        <code className="text-zinc-400 font-mono text-[10px]">{selectedNode.data?.mount_path}</code>
                                    </div>
                                    <div className="flex justify-between">
                                        <span className="text-zinc-600">Size</span>
                                        <span className="text-zinc-300">{selectedNode.data?.size_gb} GB</span>
                                    </div>
                                </>
                            )}
                        </div>
                    </div>
                )}
            </div>
        </DashboardShell>
    );
}
