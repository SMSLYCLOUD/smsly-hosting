'use client';

import React, { useCallback, useEffect, useState } from 'react';
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
import { ServiceNode, DatabaseNode, RedisNode } from '@/components/canvas/CustomNodes';
import { servicesApi, Service } from '@/lib/api';
import { useRouter } from 'next/navigation';
import { Plus } from 'lucide-react';
import { Navbar } from '@/components/layout/Navbar';
import { Button } from '@/components/ui/button';
import { motion } from 'framer-motion';

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

export default function ServicesCanvas() {
  const router = useRouter();
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  useEffect(() => {
    const fetchData = async () => {
      const services = await servicesApi.list();
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
    };
    fetchData();
  }, [setNodes, setEdges]);

  const onNodeClick = useCallback((event: any, node: Node) => {
    router.push(`/services/${node.id}`);
  }, [router]);

  return (
    <main className="h-screen flex flex-col bg-background transition-colors duration-500">
      <Navbar />
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.8 }}
        className="flex-1 relative overflow-hidden"
      >
        <div className="absolute inset-0 bg-grid-white/[0.02] bg-[length:50px_50px] pointer-events-none" />
        <div className="absolute top-6 left-6 z-10">
            <Button onClick={() => router.push('/new')} className="shadow-xl bg-primary hover:bg-primary/90 text-white font-bold rounded-full px-6">
                <Plus className="mr-2 h-4 w-4" /> New Service
            </Button>
        </div>

        <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            nodeTypes={nodeTypes}
            connectionLineType={ConnectionLineType.SmoothStep}
            fitView
            className="bg-background"
        >
            <Background color="currentColor" gap={30} size={1} className="text-muted-foreground/20" />
            <Controls className="!bg-card !border-border !fill-foreground !shadow-lg !rounded-xl m-4" />
        </ReactFlow>
      </motion.div>
    </main>
  );
}
