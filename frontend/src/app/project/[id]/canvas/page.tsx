"use client";

/**
 * CanvasPage — 2D interactive canvas with draggable service cards
 * and SVG edge connections. Pan, zoom, drag nodes.
 * Uses the same data as the topology page (/api/v1/topology/).
 */

import { useEffect, useState, useRef, useCallback, MouseEvent as ReactMouseEvent } from 'react';
import api from '@/lib/api';
import { useRouter, useParams } from 'next/navigation';
import {
    Server, Database, HardDrive, Activity,
    ExternalLink, RefreshCw, ZoomIn, ZoomOut,
    Maximize2, Wifi, X, Loader2
} from 'lucide-react';


/* ── Types ──────────────────────────────────────────────── */
interface CanvasNode {
    id: string;
    name: string;
    type: 'service' | 'addon' | 'volume';
    data?: any;
    x: number;
    y: number;
}

interface CanvasEdge {
    source: string;
    target: string;
    type?: string;
}

/* ── Colors ─────────────────────────────────────────────── */
const CARD_THEMES: Record<string, {
    bg: string; border: string; glow: string; icon: string; accent: string;
}> = {
    service:      { bg: '#18181b', border: '#3b82f6', glow: '#3b82f620', icon: '#3b82f6', accent: '#3b82f6' },
    POSTGRES:     { bg: '#18181b', border: '#6366f1', glow: '#6366f120', icon: '#6366f1', accent: '#6366f1' },
    REDIS:        { bg: '#18181b', border: '#22c55e', glow: '#22c55e20', icon: '#22c55e', accent: '#22c55e' },
    MYSQL:        { bg: '#18181b', border: '#f59e0b', glow: '#f59e0b20', icon: '#f59e0b', accent: '#f59e0b' },
    MONGODB:      { bg: '#18181b', border: '#a855f7', glow: '#a855f720', icon: '#a855f7', accent: '#a855f7' },
    ELASTICSEARCH:{ bg: '#18181b', border: '#06b6d4', glow: '#06b6d420', icon: '#06b6d4', accent: '#06b6d4' },
    RABBITMQ:     { bg: '#18181b', border: '#f97316', glow: '#f9731620', icon: '#f97316', accent: '#f97316' },
    volume:       { bg: '#18181b', border: '#eab308', glow: '#eab30820', icon: '#eab308', accent: '#eab308' },
    default:      { bg: '#18181b', border: '#3b82f6', glow: '#3b82f620', icon: '#3b82f6', accent: '#3b82f6' },
};

const EDGE_COLORS: Record<string, string> = {
    API: '#60a5fa', DATABASE: '#818cf8', CACHE: '#34d399',
    QUEUE: '#fb923c', SEARCH: '#22d3ee', STORAGE: '#fbbf24', ADDON: '#64748b',
};

const HEALTH_COLORS: Record<string, string> = {
    healthy: '#22c55e', unhealthy: '#ef4444', starting: '#f59e0b', unknown: '#52525b',
};

const DEPLOY_LABELS: Record<string, { color: string; label: string }> = {
    ACTIVE:    { color: '#22c55e', label: 'Active' },
    BUILDING:  { color: '#f59e0b', label: 'Building' },
    DEPLOYING: { color: '#3b82f6', label: 'Deploying' },
    FAILED:    { color: '#ef4444', label: 'Failed' },
    QUEUED:    { color: '#8b5cf6', label: 'Queued' },
    NONE:      { color: '#52525b', label: 'No Deploy' },
};

function getTheme(node: CanvasNode) {
    if (node.type === 'service') return CARD_THEMES.service;
    if (node.type === 'addon') {
        const t = (node.data?.addon_type || '').toUpperCase();
        return CARD_THEMES[t] || CARD_THEMES.default;
    }
    if (node.type === 'volume') return CARD_THEMES.volume;
    return CARD_THEMES.default;
}

/* ── Card dimensions ────────────────────────────────────── */
const CARD_W = 220;
const CARD_H_SERVICE = 120;
const CARD_H_OTHER = 80;

function getCardH(node: CanvasNode) {
    return node.type === 'service' ? CARD_H_SERVICE : CARD_H_OTHER;
}

/* ── Auto-layout: hub-spoke pattern ────────────────────── */
function autoLayout(nodes: CanvasNode[], edges: CanvasEdge[]): CanvasNode[] {
    const services = nodes.filter(n => n.type === 'service');
    const others = nodes.filter(n => n.type !== 'service');

    const serviceChildren: Record<string, string[]> = {};
    services.forEach(s => { serviceChildren[s.id] = []; });
    edges.forEach(e => { if (serviceChildren[e.source]) serviceChildren[e.source].push(e.target); });

    const GAP = 340;
    const startX = -(services.length - 1) * GAP / 2;
    services.forEach((svc, i) => { svc.x = startX + i * GAP; svc.y = 0; });

    const CHILD_GAP = 250;
    const CHILD_Y = 200;
    services.forEach(svc => {
        const children = serviceChildren[svc.id] || [];
        const sx = svc.x - (children.length - 1) * CHILD_GAP / 2;
        children.forEach((childId, j) => {
            const child = others.find(n => n.id === childId);
            if (child) { child.x = sx + j * CHILD_GAP; child.y = svc.y + CHILD_Y; }
        });
    });

    // Orphans
    const placed = new Set([...services.map(s => s.id), ...Object.values(serviceChildren).flat()]);
    let orphanX = services.length > 0 ? services[services.length - 1].x + GAP : 0;
    others.forEach(n => { if (!placed.has(n.id)) { n.x = orphanX; n.y = 200; orphanX += CHILD_GAP; } });

    return nodes;
}

/* ── SVG curve ────────────────────────────────────────── */
function buildCurve(src: CanvasNode, tgt: CanvasNode): string {
    const sx = src.x + CARD_W / 2;
    const sy = src.y + getCardH(src);
    const tx = tgt.x + CARD_W / 2;
    const ty = tgt.y;
    const midY = (sy + ty) / 2;
    return `M ${sx} ${sy} C ${sx} ${midY}, ${tx} ${midY}, ${tx} ${ty}`;
}

/* ── Node Card ────────────────────────────────────────── */
function NodeCard({
    node, selected, onSelect, onDragStart
}: {
    node: CanvasNode;
    selected: boolean;
    onSelect: (n: CanvasNode) => void;
    onDragStart: (e: ReactMouseEvent, n: CanvasNode) => void;
}) {
    const theme = getTheme(node);
    const h = getCardH(node);
    const isService = node.type === 'service';
    const health = node.data?.health || 'unknown';
    const healthColor = HEALTH_COLORS[health] || HEALTH_COLORS.unknown;
    const deploy = DEPLOY_LABELS[node.data?.deploy_status] || DEPLOY_LABELS.NONE;

    return (
        <div
            className="absolute select-none cursor-grab active:cursor-grabbing group"
            style={{ left: node.x, top: node.y, width: CARD_W, height: h, zIndex: selected ? 20 : 10 }}
            onClick={(e) => { e.stopPropagation(); onSelect(node); }}
            onMouseDown={(e) => onDragStart(e, node)}
        >
            {/* Glow */}
            <div className="absolute -inset-2 rounded-2xl blur-xl opacity-0 group-hover:opacity-100 transition-opacity duration-300"
                style={{ backgroundColor: theme.glow }} />

            {/* Card */}
            <div className="relative w-full h-full rounded-xl overflow-hidden transition-all duration-200"
                style={{
                    backgroundColor: theme.bg,
                    border: `1.5px solid ${selected ? theme.border : theme.border + '40'}`,
                    boxShadow: selected
                        ? `0 0 20px ${theme.border}30, 0 4px 12px rgba(0,0,0,0.4)` : '0 2px 8px rgba(0,0,0,0.3)',
                }}>
                <div className="h-[2px] w-full" style={{ backgroundColor: theme.border }} />
                <div className="p-3 h-full flex flex-col">
                    <div className="flex items-center gap-2.5">
                        <div className="w-8 h-8 rounded-lg flex-shrink-0 flex items-center justify-center"
                            style={{ backgroundColor: theme.icon + '15' }}>
                            {isService
                                ? <Server className="w-4 h-4" style={{ color: theme.icon }} />
                                : node.type === 'addon'
                                    ? <Database className="w-4 h-4" style={{ color: theme.icon }} />
                                    : <HardDrive className="w-4 h-4" style={{ color: theme.icon }} />}
                        </div>
                        <div className="flex-1 min-w-0">
                            <div className="text-[13px] font-semibold text-white truncate">{node.name}</div>
                            <div className="text-[10px] text-zinc-500 uppercase tracking-wider">
                                {isService ? 'Service' : node.data?.addon_type || node.type}
                            </div>
                        </div>
                        {isService && (
                            <div className="relative flex-shrink-0">
                                <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: healthColor }} />
                                {health === 'healthy' && (
                                    <div className="absolute inset-0 w-2.5 h-2.5 rounded-full animate-ping"
                                        style={{ backgroundColor: healthColor, opacity: 0.4 }} />
                                )}
                            </div>
                        )}
                        {node.type === 'addon' && (
                            <div className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                                style={{
                                    backgroundColor: node.data?.status === 'ACTIVE' ? '#22c55e'
                                        : node.data?.status === 'FAILED' ? '#ef4444' : '#52525b'
                                }} />
                        )}
                    </div>
                    {isService && (
                        <div className="mt-auto flex items-center gap-2 pt-2">
                            <span className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                                style={{
                                    backgroundColor: deploy.color + '15', color: deploy.color,
                                    border: `1px solid ${deploy.color}25`,
                                }}>
                                {deploy.label}
                            </span>
                            {node.data?.port && <span className="text-[10px] text-zinc-600 font-mono">:{node.data.port}</span>}
                            {node.data?.deploy_commit && (
                                <span className="text-[10px] text-zinc-600 font-mono ml-auto">
                                    {node.data.deploy_commit.substring(0, 7)}
                                </span>
                            )}
                        </div>
                    )}
                    {node.type === 'addon' && (
                        <div className="mt-auto flex items-center gap-2 pt-1">
                            <span className="text-[10px] text-zinc-500 uppercase">{node.data?.status || 'unknown'}</span>
                        </div>
                    )}
                    {node.type === 'volume' && (
                        <div className="mt-auto flex items-center gap-2 pt-1">
                            <code className="text-[10px] text-zinc-500 font-mono truncate">{node.data?.mount_path}</code>
                            {node.data?.size_gb && <span className="text-[10px] text-zinc-600 ml-auto">{node.data.size_gb}GB</span>}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

/* ── Main Page ──────────────────────────────────────────── */
export default function CanvasPage() {
    const router = useRouter();
    const params = useParams();
    const [nodes, setNodes] = useState<CanvasNode[]>([]);
    const [edges, setEdges] = useState<CanvasEdge[]>([]);
    const [loading, setLoading] = useState(true);
    const [selectedNode, setSelectedNode] = useState<CanvasNode | null>(null);
    const [autoRefresh, setAutoRefresh] = useState(true);

    // Pan & Zoom
    const [pan, setPan] = useState({ x: 0, y: 0 });
    const [zoom, setZoom] = useState(1);
    const isPanning = useRef(false);
    const panStart = useRef({ x: 0, y: 0 });
    const panOrigin = useRef({ x: 0, y: 0 });

    // Drag node
    const dragNode = useRef<CanvasNode | null>(null);
    const dragOffset = useRef({ x: 0, y: 0 });
    const canvasRef = useRef<HTMLDivElement>(null);

    /* ── Load ── */
    const loadData = useCallback(async () => {
        try {
            const res = await api.get('/topology/', { params: { project_id: params?.id } });
            const nodesIn = Array.isArray(res?.data?.nodes) ? res.data.nodes : [];
            const edgesIn = Array.isArray(res?.data?.edges) ? res.data.edges : [];

            const canvasNodes: CanvasNode[] = nodesIn
                .filter((n: any) => Boolean(n?.id))
                .map((n: any) => ({
                    id: String(n.id),
                    name: String(n.data?.name || n.data?.mount_path || n.id || 'node'),
                    type: String(n.type || 'service') as 'service' | 'addon' | 'volume',
                    data: n.data || {},
                    x: 0, y: 0,
                }));

            const canvasEdges: CanvasEdge[] = edgesIn
                .filter((e: any) => Boolean(e?.source && e?.target))
                .map((e: any) => ({
                    source: String(e.source),
                    target: String(e.target),
                    type: String(e.type || 'ADDON'),
                }));

            autoLayout(canvasNodes, canvasEdges);
            setNodes(canvasNodes);
            setEdges(canvasEdges);
        } catch (error) {
            console.error('Failed to load canvas data:', error);
        } finally {
            setLoading(false);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => { loadData(); }, [loadData]);

    useEffect(() => {
        if (!autoRefresh) return;
        const iv = setInterval(loadData, 15000);
        return () => clearInterval(iv);
    }, [autoRefresh, loadData]);

    /* ── Pan ── */
    const handleCanvasMouseDown = useCallback((e: ReactMouseEvent) => {
        if (dragNode.current) return;
        isPanning.current = true;
        panStart.current = { x: e.clientX, y: e.clientY };
        panOrigin.current = { ...pan };
    }, [pan]);

    const handleMouseMove = useCallback((e: ReactMouseEvent) => {
        if (dragNode.current) {
            const newX = (e.clientX - dragOffset.current.x - pan.x) / zoom;
            const newY = (e.clientY - dragOffset.current.y - pan.y) / zoom;
            setNodes(prev => prev.map(n =>
                n.id === dragNode.current!.id ? { ...n, x: newX, y: newY } : n
            ));
            return;
        }
        if (isPanning.current) {
            const dx = e.clientX - panStart.current.x;
            const dy = e.clientY - panStart.current.y;
            setPan({ x: panOrigin.current.x + dx, y: panOrigin.current.y + dy });
        }
    }, [pan, zoom]);

    const handleMouseUp = useCallback(() => {
        isPanning.current = false;
        dragNode.current = null;
    }, []);

    const handleNodeDragStart = useCallback((e: ReactMouseEvent, node: CanvasNode) => {
        e.stopPropagation();
        dragNode.current = node;
        dragOffset.current = {
            x: e.clientX - (node.x * zoom + pan.x),
            y: e.clientY - (node.y * zoom + pan.y),
        };
    }, [pan, zoom]);

    /* ── Zoom ── */
    const handleWheel = useCallback((e: WheelEvent) => {
        e.preventDefault();
        const delta = e.deltaY > 0 ? 0.9 : 1.1;
        const newZoom = Math.max(0.2, Math.min(3, zoom * delta));
        const rect = canvasRef.current?.getBoundingClientRect();
        if (rect) {
            const mx = e.clientX - rect.left;
            const my = e.clientY - rect.top;
            setPan({ x: mx - (mx - pan.x) * (newZoom / zoom), y: my - (my - pan.y) * (newZoom / zoom) });
        }
        setZoom(newZoom);
    }, [zoom, pan]);

    useEffect(() => {
        const el = canvasRef.current;
        if (!el) return;
        el.addEventListener('wheel', handleWheel, { passive: false });
        return () => el.removeEventListener('wheel', handleWheel);
    }, [handleWheel]);

    /* ── Fit to view ── */
    const fitToView = useCallback(() => {
        if (nodes.length === 0) return;
        const rect = canvasRef.current?.getBoundingClientRect();
        if (!rect) return;
        const minX = Math.min(...nodes.map(n => n.x));
        const minY = Math.min(...nodes.map(n => n.y));
        const maxX = Math.max(...nodes.map(n => n.x + CARD_W));
        const maxY = Math.max(...nodes.map(n => n.y + getCardH(n)));
        const graphW = maxX - minX + 100;
        const graphH = maxY - minY + 100;
        const scale = Math.min(rect.width / graphW, rect.height / graphH, 1.2);
        setZoom(scale);
        setPan({
            x: (rect.width - graphW * scale) / 2 - minX * scale + 50 * scale,
            y: (rect.height - graphH * scale) / 2 - minY * scale + 50 * scale,
        });
    }, [nodes]);

    useEffect(() => {
        if (nodes.length > 0 && !loading) setTimeout(fitToView, 100);
    }, [loading]); // eslint-disable-line react-hooks/exhaustive-deps

    const isEmpty = nodes.length === 0;

    return (
        <div className="relative h-[calc(100vh-6rem)] w-full bg-[#0a0a0b] overflow-hidden">
            {/* Dot grid background */}
            <div className="absolute inset-0 pointer-events-none"
                style={{
                    backgroundImage: `radial-gradient(circle, #ffffff08 1px, transparent 1px)`,
                    backgroundSize: `${24 * zoom}px ${24 * zoom}px`,
                    backgroundPosition: `${pan.x}px ${pan.y}px`,
                }} />

            {/* Header */}
            <div className="absolute top-4 left-4 z-30 pointer-events-none">
                <div className="flex items-center gap-3">
                    <div className="w-9 h-9 rounded-xl bg-blue-600/15 flex items-center justify-center border border-blue-600/20">
                        <Activity className="w-4.5 h-4.5 text-blue-400" />
                    </div>
                    <div>
                        <h1 className="text-lg font-bold text-white tracking-tight">Canvas</h1>
                        <p className="text-[11px] text-zinc-600">
                            {nodes.filter(n => n.type === 'service').length} services
                            {' · '}{edges.length} connections
                            {' · '}{Math.round(zoom * 100)}%
                        </p>
                    </div>
                </div>
            </div>

            {/* Controls */}
            <div className="absolute top-4 right-4 z-30 flex items-center gap-1.5">
                <button onClick={() => setAutoRefresh(!autoRefresh)}
                    className={`p-2 rounded-lg border text-xs transition-all ${autoRefresh
                        ? 'bg-emerald-950/50 border-emerald-800/40 text-emerald-400'
                        : 'bg-zinc-900/80 border-zinc-800/50 text-zinc-500'}`}
                    title={autoRefresh ? 'Live (15s)' : 'Paused'}>
                    <Wifi className={`w-3.5 h-3.5 ${autoRefresh ? 'animate-pulse' : ''}`} />
                </button>
                <button onClick={loadData}
                    className="p-2 rounded-lg bg-zinc-900/80 border border-zinc-800/50 text-zinc-500 hover:text-white transition-colors"
                    title="Refresh">
                    <RefreshCw className="w-3.5 h-3.5" />
                </button>
                <div className="w-px h-5 bg-zinc-800 mx-1" />
                <button onClick={() => setZoom(z => Math.min(3, z * 1.2))}
                    className="p-2 rounded-lg bg-zinc-900/80 border border-zinc-800/50 text-zinc-500 hover:text-white transition-colors">
                    <ZoomIn className="w-3.5 h-3.5" />
                </button>
                <button onClick={() => setZoom(z => Math.max(0.2, z * 0.8))}
                    className="p-2 rounded-lg bg-zinc-900/80 border border-zinc-800/50 text-zinc-500 hover:text-white transition-colors">
                    <ZoomOut className="w-3.5 h-3.5" />
                </button>
                <button onClick={fitToView}
                    className="p-2 rounded-lg bg-zinc-900/80 border border-zinc-800/50 text-zinc-500 hover:text-white transition-colors"
                    title="Fit to view">
                    <Maximize2 className="w-3.5 h-3.5" />
                </button>
            </div>

            {/* Main canvas */}
            {loading ? (
                <div className="flex items-center justify-center h-full gap-3">
                    <Loader2 className="w-5 h-5 animate-spin text-blue-500" />
                    <span className="text-zinc-500 text-sm">Loading canvas...</span>
                </div>
            ) : isEmpty ? (
                <div className="flex flex-col items-center justify-center h-full gap-4">
                    <div className="w-16 h-16 rounded-2xl bg-zinc-900 border border-zinc-800 flex items-center justify-center">
                        <Server className="w-8 h-8 text-zinc-700" />
                    </div>
                    <p className="text-base font-medium text-zinc-500">No services deployed</p>
                    <p className="text-sm text-zinc-700">Deploy a service to see it here.</p>
                </div>
            ) : (
                <div ref={canvasRef}
                    className="w-full h-full cursor-grab active:cursor-grabbing"
                    onMouseDown={handleCanvasMouseDown} onMouseMove={handleMouseMove}
                    onMouseUp={handleMouseUp} onMouseLeave={handleMouseUp}
                    onClick={() => setSelectedNode(null)}>
                    <div className="origin-top-left"
                        style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, willChange: 'transform' }}>
                        {/* SVG edges */}
                        <svg className="absolute top-0 left-0 pointer-events-none"
                            style={{ width: '10000px', height: '10000px', overflow: 'visible' }}>
                            <defs>
                                <style>{`@keyframes dashFlow { to { stroke-dashoffset: -20; } }`}</style>
                            </defs>
                            {edges.map((edge, i) => {
                                const src = nodes.find(n => n.id === edge.source);
                                const tgt = nodes.find(n => n.id === edge.target);
                                if (!src || !tgt) return null;
                                const color = EDGE_COLORS[edge.type || 'ADDON'] || EDGE_COLORS.ADDON;
                                const path = buildCurve(src, tgt);
                                return (
                                    <g key={`edge-${i}`}>
                                        <path d={path} fill="none" stroke={color} strokeWidth={6} strokeOpacity={0.08} />
                                        <path d={path} fill="none" stroke={color} strokeWidth={2} strokeOpacity={0.5}
                                            strokeDasharray={edge.type === 'API' ? '6 4' : 'none'} />
                                        <path d={path} fill="none" stroke={color} strokeWidth={2} strokeOpacity={0.8}
                                            strokeDasharray="4 16" style={{ animation: 'dashFlow 1.5s linear infinite' }} />
                                    </g>
                                );
                            })}
                        </svg>
                        {/* Node cards */}
                        {nodes.map(node => (
                            <NodeCard key={node.id} node={node}
                                selected={selectedNode?.id === node.id}
                                onSelect={setSelectedNode}
                                onDragStart={handleNodeDragStart} />
                        ))}
                    </div>
                </div>
            )}

            {/* Help */}
            <div className="absolute bottom-4 right-4 z-20 rounded-xl border border-zinc-800/80 bg-black/55 px-3 py-2 backdrop-blur-lg">
                <div className="text-[10px] text-zinc-400">Drag cards to reposition | Pan canvas | Scroll to zoom</div>
            </div>

            {/* Detail panel */}
            {selectedNode && (
                <div className="absolute top-14 right-4 z-30 w-72 bg-zinc-900/95 backdrop-blur-xl
                    border border-zinc-800/60 rounded-xl shadow-2xl shadow-black/50
                    animate-in slide-in-from-right-3 fade-in duration-200">
                    <div className="p-3 border-b border-zinc-800/40 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <div className="w-7 h-7 rounded-lg flex items-center justify-center"
                                style={{ backgroundColor: getTheme(selectedNode).icon + '15' }}>
                                {selectedNode.type === 'service'
                                    ? <Server className="w-3.5 h-3.5" style={{ color: getTheme(selectedNode).icon }} />
                                    : selectedNode.type === 'addon'
                                        ? <Database className="w-3.5 h-3.5" style={{ color: getTheme(selectedNode).icon }} />
                                        : <HardDrive className="w-3.5 h-3.5" style={{ color: getTheme(selectedNode).icon }} />}
                            </div>
                            <div>
                                <div className="text-xs font-semibold text-white">{selectedNode.name}</div>
                                <div className="text-[10px] text-zinc-600 uppercase">{selectedNode.type}</div>
                            </div>
                        </div>
                        <button onClick={() => setSelectedNode(null)}
                            className="w-6 h-6 rounded flex items-center justify-center hover:bg-zinc-800 text-zinc-600 hover:text-white transition-colors">
                            <X className="w-3.5 h-3.5" />
                        </button>
                    </div>
                    <div className="p-3 space-y-2 text-xs">
                        {selectedNode.type === 'service' && (
                            <>
                                <div className="flex justify-between">
                                    <span className="text-zinc-600">Health</span>
                                    <div className="flex items-center gap-1.5">
                                        <div className="w-1.5 h-1.5 rounded-full"
                                            style={{ backgroundColor: HEALTH_COLORS[selectedNode.data?.health] || HEALTH_COLORS.unknown }} />
                                        <span className="text-zinc-300 capitalize">{selectedNode.data?.health || 'unknown'}</span>
                                    </div>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-zinc-600">Status</span>
                                    <span style={{ color: (DEPLOY_LABELS[selectedNode.data?.deploy_status] || DEPLOY_LABELS.NONE).color }}>
                                        {(DEPLOY_LABELS[selectedNode.data?.deploy_status] || DEPLOY_LABELS.NONE).label}
                                    </span>
                                </div>
                                {selectedNode.data?.domain && (
                                    <div className="flex justify-between">
                                        <span className="text-zinc-600">Domain</span>
                                        <span className="text-blue-400 truncate max-w-[140px]">{selectedNode.data.domain}</span>
                                    </div>
                                )}
                                <div className="pt-2">
                                    <button onClick={() => router.push(`/services/${selectedNode.id}`)}
                                        className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5
                                            bg-blue-600/10 hover:bg-blue-600/20 text-blue-400
                                            text-xs font-medium rounded-lg transition-colors border border-blue-600/20">
                                        <ExternalLink className="w-3 h-3" /> View Service
                                    </button>
                                </div>
                            </>
                        )}
                        {selectedNode.type === 'addon' && (
                            <>
                                <div className="flex justify-between">
                                    <span className="text-zinc-600">Type</span>
                                    <span className="text-zinc-300">{selectedNode.data?.addon_type}</span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-zinc-600">Status</span>
                                    <span className="text-zinc-300">{selectedNode.data?.status}</span>
                                </div>
                            </>
                        )}
                        {selectedNode.type === 'volume' && (
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
    );
}
