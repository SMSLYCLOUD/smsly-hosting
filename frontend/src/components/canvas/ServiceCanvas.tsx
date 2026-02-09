'use client';

import React, { useCallback, useEffect } from 'react';
import ReactFlow, {
  Background,
  Controls,
  Edge,
  Node,
  useNodesState,
  useEdgesState,
  ConnectionLineType,
} from 'reactflow';
import 'reactflow/dist/style.css';
import dagre from 'dagre';
import { ServiceNode, DatabaseNode, RedisNode } from './CustomNodes';
import { Service } from '@/lib/api';
import { useRouter } from 'next/navigation';

interface ServiceCanvasProps {
  services: Service[];
}

const nodeTypes = {
  SERVICE: ServiceNode,
  POSTGRES: DatabaseNode,
  REDIS: RedisNode,
};

const getLayoutedElements = (nodes: Node[], edges: Edge[]) => {
  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setDefaultEdgeLabel(() => ({}));
  dagreGraph.setGraph({ rankdir: 'TB' });

  nodes.forEach((node) => {
    dagreGraph.setNode(node.id, { width: 300, height: 150 });
  });

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target);
  });

  dagre.layout(dagreGraph);

  nodes.forEach((node) => {
    const nodeWithPosition = dagreGraph.node(node.id);
    node.targetPosition = 'top' as any;
    node.sourcePosition = 'bottom' as any;
    node.position = {
      x: nodeWithPosition.x - 150,
      y: nodeWithPosition.y - 75,
    };
  });

  return { nodes, edges };
};

export function ServiceCanvas({ services }: ServiceCanvasProps) {
  const router = useRouter();
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  useEffect(() => {
    const newNodes: Node[] = [];
    const newEdges: Edge[] = [];

    services.forEach((svc: Service) => {
      newNodes.push({
        id: svc.id,
        type: 'SERVICE',
        data: { label: svc.name, subLabel: svc.repository_url, status: svc.latest_deployment?.status || 'UNKNOWN' },
        position: { x: 0, y: 0 }
      });
    });

    const layout = getLayoutedElements(newNodes, newEdges);
    setNodes(layout.nodes);
    setEdges(layout.edges);
  }, [services, setNodes, setEdges]);

  const onNodeClick = useCallback((event: any, node: Node) => {
    router.push(`/services/${node.id}`);
  }, [router]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={onNodeClick}
      nodeTypes={nodeTypes}
      connectionLineType={ConnectionLineType.SmoothStep}
      fitView
      className="bg-background/50 h-full"
    >
      <Background color="currentColor" gap={30} size={1} className="text-muted-foreground/20" />
      <Controls className="!bg-card !border-border !fill-foreground !shadow-lg !rounded-xl m-4" />
    </ReactFlow>
  );
}
