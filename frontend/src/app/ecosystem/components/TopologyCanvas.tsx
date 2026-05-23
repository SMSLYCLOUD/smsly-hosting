import React, { useCallback, useEffect, useState } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType,
  Panel
} from 'reactflow';
import 'reactflow/dist/style.css';
import dagre from 'dagre';
import { ServiceNode } from './ServiceNode';

const nodeTypes = {
  service: ServiceNode,
  addon: ({ data }: any) => (
    <div className="px-4 py-2 bg-purple-500/10 border border-purple-500/30 rounded-full shadow-lg flex items-center justify-center min-w-[120px]">
      <span className="font-bold text-purple-400 text-sm">{data.label}</span>
    </div>
  )
};

const dagreGraph = new dagre.graphlib.Graph();
dagreGraph.setDefaultEdgeLabel(() => ({}));

const nodeWidth = 260;
const nodeHeight = 130; // estimate height

const getLayoutedElements = (nodes: any[], edges: any[], direction = 'TB') => {
  const isHorizontal = direction === 'LR';
  dagreGraph.setGraph({ rankdir: direction, ranksep: 100, nodesep: 50 });

  nodes.forEach((node) => {
    // Treat addon nodes as smaller
    const width = node.type === 'addon' ? 120 : nodeWidth;
    const height = node.type === 'addon' ? 40 : nodeHeight;
    dagreGraph.setNode(node.id, { width, height });
  });

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target);
  });

  dagre.layout(dagreGraph);

  const newNodes = nodes.map((node) => {
    const nodeWithPosition = dagreGraph.node(node.id);
    const newNode = { ...node };

    // We are shifting the dagre node position (anchor=center center) to the top left
    // so it matches the React Flow node anchor point (top left).
    newNode.position = {
      x: nodeWithPosition.x - (node.type === 'addon' ? 120 : nodeWidth) / 2,
      y: nodeWithPosition.y - (node.type === 'addon' ? 40 : nodeHeight) / 2,
    };

    return newNode;
  });

  return { nodes: newNodes, edges };
};

export function TopologyCanvas({ plan, servers, callbacks }: any) {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  useEffect(() => {
    if (!plan) return;

    const initialNodes: any[] = [];
    const initialEdges: any[] = [];

    // 1. Create nodes for Services
    plan.services.forEach((svc: any, idx: number) => {
      initialNodes.push({
        id: svc.repo,
        type: 'service',
        data: {
          svc,
          idx,
          servers,
          ...callbacks
        },
        position: { x: 0, y: 0 }, // Will be overwritten by dagre
      });

      // Dependencies as edges
      if (svc.depends_on && svc.depends_on.length > 0) {
        svc.depends_on.forEach((dep: string) => {
          // Find the target repo matching the dependency string
          const targetSvc = plan.services.find((s: any) => s.repo.includes(dep));
          if (targetSvc) {
            initialEdges.push({
              id: `e-${targetSvc.repo}-${svc.repo}`,
              source: targetSvc.repo, // Dependency is source
              target: svc.repo,       // This service depends on it
              animated: true,
              style: { stroke: 'hsl(var(--primary))' },
              markerEnd: { type: MarkerType.ArrowClosed, color: 'hsl(var(--primary))' },
            });
          }
        });
      }
    });

    // 2. Create nodes for Addons
    if (plan.addons) {
      plan.addons.forEach((addon: any) => {
        const addonId = `addon-${addon.type}`;
        initialNodes.push({
          id: addonId,
          type: 'addon',
          data: { label: addon.type },
          position: { x: 0, y: 0 },
        });

        // Addon edges point from the addon to the services that share it
        addon.shared_by.forEach((repoStr: string) => {
          const targetSvc = plan.services.find((s: any) => s.repo.includes(repoStr));
          if (targetSvc) {
            initialEdges.push({
              id: `e-${addonId}-${targetSvc.repo}`,
              source: addonId,
              target: targetSvc.repo,
              animated: true,
              style: { stroke: '#a855f7' }, // purple for addons
              markerEnd: { type: MarkerType.ArrowClosed, color: '#a855f7' },
            });
          }
        });
      });
    }

    const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
      initialNodes,
      initialEdges
    );

    setNodes(layoutedNodes);
    setEdges(layoutedEdges);
  }, [plan, servers]); // callbacks omitted intentionally to avoid re-layouting constantly

  const onLayout = useCallback(
    (direction: string) => {
      const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
        nodes,
        edges,
        direction
      );

      setNodes([...layoutedNodes]);
      setEdges([...layoutedEdges]);
    },
    [nodes, edges]
  );

  return (
    <div className="w-full h-[600px] border border-border rounded-xl overflow-hidden bg-background">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        attributionPosition="bottom-right"
        className="dark" // Assuming dark mode by default for this app based on screenshot
      >
        <style>{`
            .react-flow__controls-button {
                background-color: #18181b !important;
                border-bottom: 1px solid #27272a !important;
                fill: #d4d4d8 !important;
            }
            .react-flow__controls-button:hover {
                background-color: #27272a !important;
            }
        `}</style>
        <MiniMap 
            nodeColor={(node) => {
                if (node.type === 'addon') return '#a855f7';
                return '#10b981'; // emerald
            }} 
            maskColor="rgba(0,0,0,0.4)" 
            className="bg-card" 
        />
        <Controls className="bg-zinc-900 border-zinc-800" />
        <Background color="#333" gap={16} size={1} />
        <Panel position="top-right" className="bg-card/80 backdrop-blur border border-border rounded-lg p-2 flex gap-2">
            <button className="text-xs px-2 py-1 bg-muted rounded hover:bg-muted/80" onClick={() => onLayout('TB')}>Vertical</button>
            <button className="text-xs px-2 py-1 bg-muted rounded hover:bg-muted/80" onClick={() => onLayout('LR')}>Horizontal</button>
        </Panel>
      </ReactFlow>
    </div>
  );
}
