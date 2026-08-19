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

// useGraphData removed as it's passed as prop
import { TopologyNode, TopologyEdge } from '@/types/topology';
import { Loader2, Server, Database, Activity, Globe, Box, DatabaseZap, HardDrive } from 'lucide-react';
import { ServiceSidePanel } from './ServiceSidePanel';
import { ErrorBoundary } from '@/components/ErrorBoundary';

// --- Custom Node Component ---
const CustomNode = ({ data }: { data: any }) => {
  // Safe destructuring
  const { label = 'Unknown', kind = 'UNKNOWN', status = 'UNKNOWN', subtype = 'generic', url = '' } = data || {};
  const nodeType = (data as any)?.nodeType || '';

  const statusColor = useMemo(() => {
    switch (status) {
      case 'ACTIVE': return 'bg-emerald-500';
      case 'RUNNING': return 'bg-emerald-500';
      case 'BUILDING': return 'bg-blue-500';
      case 'FAILED': return 'bg-red-500';
      default: return 'bg-zinc-500';
    }
  }, [status]);

  const Icon = useMemo(() => {
    switch (kind) {
      case 'COMPUTE': return Server;
      case 'DATABASE': return Database;
      case 'CACHE': return DatabaseZap;
      case 'STORAGE': return HardDrive;
      case 'EXTERNAL': return Globe;
      default: return Box;
    }
  }, [kind]);

  // Replica nodes: compact, smaller style
  if (nodeType === 'replica') {
    return (
      <div className="min-w-[140px] rounded-md border border-zinc-700/60 bg-zinc-900/80 shadow-sm transition-all hover:border-zinc-500 hover:shadow-md cursor-pointer opacity-80">
        <Handle type="target" position={Position.Left} className="!bg-zinc-500" />
        <div className="flex items-center gap-2 px-3 py-2">
          <div className={`h-2 w-2 rounded-full ${statusColor}`} title={status} />
          <span className="text-[10px] font-medium text-zinc-400 truncate max-w-[100px]">{label}</span>
        </div>
        <Handle type="source" position={Position.Right} className="!bg-zinc-500" />
      </div>
    );
  }

  return (
    <div className="min-w-[180px] rounded-md border border-zinc-700 bg-zinc-900 shadow-sm transition-all hover:border-zinc-500 hover:shadow-md cursor-pointer">
      <Handle type="target" position={Position.Left} className="!bg-zinc-500" />

      <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-950/50 px-3 py-2">
        <div className="flex items-center gap-2">
           <Icon className="h-4 w-4 text-zinc-400" />
           <span className="text-xs font-semibold text-zinc-200 truncate max-w-[120px]" title={label}>{label}</span>
        </div>
        <div className={`h-2 w-2 rounded-full ${statusColor}`} title={status} />
      </div>

      <div className="p-3 space-y-1">
        <div className="text-[10px] text-zinc-500 uppercase tracking-wider">{kind} / {subtype}</div>
        {url && <div className="text-[10px] text-blue-400 truncate max-w-[150px]">{url}</div>}
      </div>

      <Handle type="source" position={Position.Right} className="!bg-zinc-500" />
    </div>
  );
};

const nodeTypes = { custom: CustomNode };

// --- Layout Helper ---
const nodeWidth = 200;
const nodeHeight = 100;

const getLayoutedElements = (nodes: Node[], edges: Edge[], direction = 'LR') => {
  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setDefaultEdgeLabel(() => ({}));

  dagreGraph.setGraph({ rankdir: direction });

  nodes.forEach((node) => {
    dagreGraph.setNode(node.id, { width: nodeWidth, height: nodeHeight });
  });

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target);
  });

  dagre.layout(dagreGraph);

  const layoutedNodes = nodes.map((node) => {
    const nodeWithPosition = dagreGraph.node(node.id);
    node.targetPosition = direction === 'LR' ? Position.Left : Position.Top;
    node.sourcePosition = direction === 'LR' ? Position.Right : Position.Bottom;

    // Shift dagre center-point to top-left for ReactFlow
    node.position = {
      x: nodeWithPosition.x - nodeWidth / 2,
      y: nodeWithPosition.y - nodeHeight / 2,
    };

    return node;
  });

  return { nodes: layoutedNodes, edges };
};

export function CanvasSchematic({ data, loading, error }: { data: any, loading: boolean, error: any }) {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedNode, setSelectedNode] = useState<TopologyNode | null>(null);

  // Transform data on load
  useEffect(() => {
    // Check if data exists and has nodes
    if (data && Array.isArray(data.nodes) && data.nodes.length > 0) {
      try {
        const initialNodes: any[] = data.nodes.map((n: TopologyNode) => ({
          id: n.id,
          type: 'custom', // custom node type
          data: {
              ...(n.data || {}),
              originalType: n.type,
              originalId: n.id,
          },
          position: { x: 0, y: 0 },
        }));

        const initialEdges: any[] = (data.edges || []).map((e: TopologyEdge, idx: number) => {
          // Color by edge type
          const edgeColors: Record<string, string> = {
            PROXY_CHAIN: '#3b82f6', DATABASE: '#10b981', QUEUE: '#fb923c',
            INTERNAL: '#a855f7', CACHE: '#06b6d4', TUNNEL: '#ec4899',
            STORAGE: '#fbbf24', API: '#3b82f6', DOMAIN: '#10b981', 
            CRON: '#a78bfa', SEARCH: '#38bdf8', ADDON: '#6366f1',
          };
          const color = edgeColors[e.type] || '#52525b';

          return {
            id: e.id || `edge-${idx}`,
            source: e.source,
            target: e.target,
            type: 'smoothstep',
            animated: e.type === 'API' || e.type === 'TUNNEL',
            label: e.label,
            style: { stroke: color, strokeWidth: 2 },
            markerEnd: {
                type: MarkerType.ArrowClosed,
                color,
            },
          };
        });

        const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
          initialNodes,
          initialEdges
        );

        setNodes(layoutedNodes);
        setEdges(layoutedEdges);
      } catch (err) {
        console.error("Failed to layout graph:", err);
      }
    }
  }, [data, setNodes, setEdges]);

  const onNodeClick: NodeMouseHandler = useCallback((event, node) => {
      // Reconstruct TopologyNode for SidePanel
      const topoNode: TopologyNode = {
          id: node.data.originalId || node.id,
          type: node.data.originalType || 'service',
          data: node.data
      };
      setSelectedNode(topoNode);
  }, []);

  if (loading) return <div className="flex h-full items-center justify-center"><Loader2 className="w-8 h-8 animate-spin text-zinc-500" /></div>;
  if (error) return <div className="flex h-full items-center justify-center text-red-500">Error: {error.message}</div>;

  return (
    <ErrorBoundary fallback={<div className="flex items-center justify-center h-full text-red-500">Failed to render Schematic View.</div>}>
      <div className="relative h-full w-full bg-[#04070f]">
        <div style={{ width: '100%', height: '100%' }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={nodeTypes}
            onNodeClick={onNodeClick}
            fitView
            attributionPosition="bottom-right"
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#27272a" gap={20} size={1} />
            <Controls className="!bg-transparent [&_button]:!bg-zinc-900 [&_button]:!border-zinc-800 [&_button]:!fill-zinc-300 [&_button:hover]:!bg-zinc-800 [&_button]:!border-b [&_button:last-child]:!border-b-0 border border-zinc-800 rounded-md overflow-hidden shadow-lg" />
            <MiniMap
                nodeColor={(n) => {
                    if (n.data?.status === 'ACTIVE') return '#10b981';
                    if (n.data?.status === 'FAILED') return '#ef4444';
                    return '#71717a';
                }}
                maskColor="#00000080"
                className="!bg-zinc-900 !border-zinc-800"
            />
          </ReactFlow>
        </div>

        {selectedNode && (
          <ServiceSidePanel node={selectedNode} onClose={() => setSelectedNode(null)} />
        )}
      </div>
    </ErrorBoundary>
  );
}
