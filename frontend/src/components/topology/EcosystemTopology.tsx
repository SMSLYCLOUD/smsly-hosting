'use client';

import React, { useCallback, useEffect, useState, useMemo } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType,
  Position,
  Node,
  Edge,
  Handle,
  NodeMouseHandler,
} from 'reactflow';
import 'reactflow/dist/style.css';
import dagre from 'dagre';
import {
  Globe, Shield, Server, Database, HardDrive, Zap, Box,
  Loader2, Activity, RefreshCw, Play, Pause, Layers,
} from 'lucide-react';
import { EcosystemNode, EcosystemEdge, EcosystemGraph } from '@/types/topology';
import { TrafficFlowAnimation } from './TrafficFlowAnimation';
import { ErrorBoundary } from '@/components/ErrorBoundary';

async function fetchEcosystem(): Promise<EcosystemGraph> {
  const res = await fetch('/api/v1/topology/ecosystem/', {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── Edge color map ──────────────────────────────────────────────────────────
const EDGE_COLORS: Record<string, string> = {
  PROXY_CHAIN: '#3b82f6',   // blue
  DATABASE: '#22c55e',      // green
  QUEUE: '#f97316',         // orange
  INTERNAL: '#a855f7',      // purple
  CACHE: '#06b6d4',         // cyan
  TUNNEL: '#ec4899',        // pink
};

// ── Status → dot color ──────────────────────────────────────────────────────
function statusColor(status?: string): string {
  switch (status) {
    case 'healthy': return 'bg-emerald-500';
    case 'degraded': return 'bg-yellow-500';
    case 'down': return 'bg-red-500';
    default: return 'bg-zinc-500';
  }
}

// ── Icon by kind ────────────────────────────────────────────────────────────
function kindIcon(kind: string) {
  switch (kind) {
    case 'EXTERNAL': return Globe;
    case 'PROXY': return Shield;
    case 'COMPUTE': return Server;
    case 'DATABASE': return Database;
    case 'CACHE': return Zap;
    case 'QUEUE': return Activity;
    case 'WORKER': return Box;
    case 'STORAGE': return HardDrive;
    default: return Box;
  }
}

// ── Custom Node Component ───────────────────────────────────────────────────
const EcosystemNodeComponent = ({ data }: { data: any }) => {
  const { label = 'Unknown', kind = 'UNKNOWN', status = 'healthy', ports = [], role = '' } = data || {};
  const Icon = useMemo(() => kindIcon(kind), [kind]);

  return (
    <div className="min-w-[180px] max-w-[220px] rounded-lg border border-zinc-700 bg-zinc-900 shadow-lg transition-all hover:border-zinc-500 hover:shadow-xl cursor-pointer">
      <Handle type="target" position={Position.Top} className="!bg-zinc-500" />

      {/* Header */}
      <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-950/50 px-3 py-2 rounded-t-lg">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 text-zinc-400" />
          <span className="text-xs font-semibold text-zinc-200 truncate max-w-[140px]" title={label}>
            {label}
          </span>
        </div>
        <div className={`h-2.5 w-2.5 rounded-full ${statusColor(status)}`} title={status} />
      </div>

      {/* Body */}
      <div className="p-2.5 space-y-1">
        <div className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium">{kind}</div>
        {ports.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {ports.map((p: string) => (
              <span key={p} className="text-[9px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 font-mono">
                :{p}
              </span>
            ))}
          </div>
        )}
        {role && (
          <div className="text-[10px] text-zinc-500 truncate" title={role}>{role}</div>
        )}
      </div>

      <Handle type="source" position={Position.Bottom} className="!bg-zinc-500" />
    </div>
  );
};

const nodeTypes = { ecosystem: EcosystemNodeComponent };

// ── Layout with dagre (top-to-bottom) ───────────────────────────────────────
const nodeWidth = 200;
const nodeHeight = 90;

function getLayoutedElements(nodes: Node[], edges: Edge[]) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', ranksep: 80, nodesep: 40 });

  nodes.forEach((node) => {
    g.setNode(node.id, { width: nodeWidth, height: nodeHeight });
  });

  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target);
  });

  dagre.layout(g);

  const layouted = nodes.map((node) => {
    const pos = g.node(node.id);
    node.targetPosition = Position.Top;
    node.sourcePosition = Position.Bottom;
    node.position = {
      x: pos.x - nodeWidth / 2,
      y: pos.y - nodeHeight / 2,
    };
    return node;
  });

  return { nodes: layouted, edges };
}

// ── Main Component ──────────────────────────────────────────────────────────
export function EcosystemTopology() {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [animate, setAnimate] = useState(true);
  const [rawEdges, setRawEdges] = useState<EcosystemEdge[]>([]);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const graph = await fetchEcosystem();
      setRawEdges(graph.edges);

      // Build ReactFlow nodes
      const rfNodes: Node[] = graph.nodes.map((n) => ({
        id: n.id,
        type: 'ecosystem',
        data: {
          label: n.label,
          kind: n.kind,
          status: n.status || 'healthy',
          ports: n.metadata?.ports || [],
          role: n.metadata?.role || '',
        },
        position: { x: 0, y: 0 },
      }));

      // Build ReactFlow edges
      const rfEdges: Edge[] = graph.edges.map((e, idx) => {
        const color = EDGE_COLORS[e.type] || '#52525b';
        return {
          id: `edge-${idx}`,
          source: e.source,
          target: e.target,
          type: 'smoothstep',
          animated: !!e.animated,
          label: e.label,
          style: { stroke: color, strokeWidth: 2 },
          markerEnd: { type: MarkerType.ArrowClosed, color },
          labelStyle: { fill: '#a1a1aa', fontSize: 10, fontWeight: 500 },
          labelBgStyle: { fill: '#18181b', fillOpacity: 0.9 },
          labelBgPadding: [4, 2] as [number, number],
          labelBgBorderRadius: 4,
        };
      });

      const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(rfNodes, rfEdges);
      setNodes(layoutedNodes);
      setEdges(layoutedEdges);
    } catch (err: any) {
      setError(err.message || 'Failed to load ecosystem topology');
    } finally {
      setLoading(false);
    }
  }, [setNodes, setEdges]);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 30000); // refresh every 30s
    return () => clearInterval(interval);
  }, [loadData]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-zinc-500" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-red-500">
        <p className="text-sm">{error}</p>
        <button onClick={loadData} className="text-xs text-zinc-400 hover:text-white flex items-center gap-1">
          <RefreshCw size={12} /> Retry
        </button>
      </div>
    );
  }

  return (
    <ErrorBoundary fallback={<div className="flex items-center justify-center h-full text-red-500">Failed to render Ecosystem View.</div>}>
      <div className="relative h-full w-full bg-[#04070f]">
        {/* Toolbar */}
        <div className="absolute top-3 left-3 z-20 flex items-center gap-2">
          <button
            onClick={() => setAnimate(!animate)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[10px] font-medium bg-black/60 backdrop-blur-md border border-zinc-800 text-zinc-300 hover:text-white transition-colors"
          >
            {animate ? <Pause size={12} /> : <Play size={12} />}
            {animate ? 'Pause Traffic' : 'Play Traffic'}
          </button>
          <button
            onClick={loadData}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[10px] font-medium bg-black/60 backdrop-blur-md border border-zinc-800 text-zinc-300 hover:text-white transition-colors"
          >
            <RefreshCw size={12} /> Refresh
          </button>
        </div>

        {/* Legend */}
        <div className="absolute top-3 right-3 z-20 bg-black/60 backdrop-blur-md rounded-lg p-3 border border-zinc-800">
          <div className="text-[9px] text-zinc-400 uppercase tracking-wider mb-2 font-semibold">Edge Types</div>
          <div className="flex flex-col gap-1.5">
            {Object.entries(EDGE_COLORS).map(([type, color]) => (
              <div key={type} className="flex items-center gap-2">
                <div className="w-4 h-0.5 rounded" style={{ backgroundColor: color }} />
                <span className="text-[10px] text-zinc-300">{type.replace(/_/g, ' ')}</span>
              </div>
            ))}
          </div>
          <div className="mt-3 pt-2 border-t border-zinc-800">
            <div className="text-[9px] text-zinc-400 uppercase tracking-wider mb-1.5 font-semibold">Status</div>
            <div className="flex flex-col gap-1">
              {[
                { label: 'Healthy', color: 'bg-emerald-500' },
                { label: 'Degraded', color: 'bg-yellow-500' },
                { label: 'Down', color: 'bg-red-500' },
              ].map(({ label, color }) => (
                <div key={label} className="flex items-center gap-2">
                  <div className={`h-2 w-2 rounded-full ${color}`} />
                  <span className="text-[10px] text-zinc-300">{label}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ReactFlow canvas */}
        <div style={{ width: '100%', height: '100%' }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            attributionPosition="bottom-right"
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#1a1a2e" gap={20} size={1} />
            <Controls className="!bg-transparent [&_button]:!bg-zinc-900 [&_button]:!border-zinc-800 [&_button]:!fill-zinc-300 [&_button:hover]:!bg-zinc-800 [&_button]:!border-b [&_button:last-child]:!border-b-0 border border-zinc-800 rounded-md overflow-hidden shadow-lg" />
            <MiniMap
              nodeColor={(n) => {
                const status = n.data?.status;
                if (status === 'healthy') return '#10b981';
                if (status === 'degraded') return '#fbbf24';
                if (status === 'down') return '#ef4444';
                return '#71717a';
              }}
              maskColor="#00000080"
              className="!bg-zinc-900 !border-zinc-800"
            />

            {/* Traffic flow animation overlay */}
            {animate && nodes.length > 0 && (
              <TrafficFlowAnimation edges={edges} />
            )}
          </ReactFlow>
        </div>
      </div>
    </ErrorBoundary>
  );
}
