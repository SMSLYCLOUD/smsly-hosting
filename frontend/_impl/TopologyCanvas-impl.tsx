import React, { useCallback, useEffect, useState } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType,
  Panel,
  Position
} from 'reactflow';
import 'reactflow/dist/style.css';
import dagre from 'dagre';
import { ServiceNode } from '../src/app/ecosystem/components/ServiceNode';

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

const nodeWidth = 220;
const nodeHeight = 110;

const getLayoutedElements = (nodes: any[], edges: any[], direction = 'LR') => {
  const isHorizontal = direction === 'LR';
  dagreGraph.setGraph({ rankdir: direction, ranksep: 100, nodesep: 30 });

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

    // Required for proper smoothstep edge routing
    newNode.targetPosition = isHorizontal ? Position.Left : Position.Top;
    newNode.sourcePosition = isHorizontal ? Position.Right : Position.Bottom;

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
          const targetSvc = plan.services.find((s: any) => s.repo.includes(dep));
          if (targetSvc) {
            initialEdges.push({
              id: `e-${targetSvc.repo}-${svc.repo}`,
              source: targetSvc.repo,
              target: svc.repo,
              type: 'smoothstep',
              animated: true,
              style: { stroke: '#3b82f6', strokeWidth: 2 },
              markerEnd: { type: MarkerType.ArrowClosed, color: '#3b82f6' },
            });
          }
        });
      }

      // Service-to-service edges from {{SERVICE:name}} env var references
      if (svc.env_vars) {
        const serviceRefRe = /\{\{SERVICE\s*:\s*(.+?)\s*\}\}/i;
        Object.entries(svc.env_vars).forEach(([key, val]: [string, any]) => {
          const valStr = String(val || '');
          const match = valStr.match(serviceRefRe);
          if (match) {
            const targetName = match[1].trim();
            const targetSvc = plan.services.find((s: any) => s.repo.includes(targetName) || s.name === targetName);
            if (targetSvc && targetSvc.repo !== svc.repo) {
              const edgeId = `e-svc-${svc.repo}-${targetSvc.repo}`;
              if (!initialEdges.some((e: any) => e.id === edgeId)) {
                initialEdges.push({
                  id: edgeId,
                  source: svc.repo,
                  target: targetSvc.repo,
                  type: 'smoothstep',
                  animated: true,
                  style: { stroke: '#10b981', strokeWidth: 2 },
                  markerEnd: { type: MarkerType.ArrowClosed, color: '#10b981' },
                  label: key,
                });
              }
            }
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
              type: 'smoothstep',
              animated: true,
              style: { stroke: '#a855f7', strokeWidth: 2 }, // purple for addons
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan, servers, setNodes, setEdges]); // Note: callbacks intentionally excluded – they are event handlers and must not trigger graph re-layout

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
    [nodes, edges, setNodes, setEdges],
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
        <MiniMap
            nodeColor={(node) => {
                if (node.type === 'addon') return '#a855f7';
                return '#10b981'; // emerald
            }}
            maskColor="#00000080"
            className="!bg-zinc-900 !border-zinc-800"
        />
        <Controls className="!bg-transparent [&_button]:!bg-zinc-900 [&_button]:!border-zinc-800 [&_button]:!fill-zinc-300 [&_button:hover]:!bg-zinc-800 [&_button]:!border-b [&_button:last-child]:!border-b-0 border border-zinc-800 rounded-md overflow-hidden shadow-lg" />
        <Background color="#27272a" gap={20} size={1} />
        <Panel position="top-right" className="bg-card/80 backdrop-blur border border-border rounded-lg p-2 flex gap-2">
            <button className="text-xs px-2 py-1 bg-muted rounded hover:bg-muted/80" onClick={() => onLayout('TB')}>Vertical</button>
            <button className="text-xs px-2 py-1 bg-muted rounded hover:bg-muted/80" onClick={() => onLayout('LR')}>Horizontal</button>
        </Panel>
      </ReactFlow>
    </div>
  );
}
