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
import { Plus, LayoutGrid, Network } from 'lucide-react';
import { Navbar } from '@/components/layout/Navbar';
import { Button } from '@/components/ui/button';
import { motion } from 'framer-motion';
import { ServicesGrid } from '@/components/dashboard/ServicesGrid';

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

export default function ServicesPage() {
  const router = useRouter();
  const [viewMode, setViewMode] = useState<'CANVAS' | 'GRID'>('GRID');
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [services, setServices] = useState<Service[]>([]);

  useEffect(() => {
    const fetchData = async () => {
      const svcs = await servicesApi.list();
      setServices(svcs);

      const newNodes: Node[] = [];
      const newEdges: Edge[] = [];

      svcs.forEach((svc: Service) => {
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

      {/* View Toggle Bar */}
      <div className="border-b border-border bg-card/50 backdrop-blur px-6 py-3 flex justify-between items-center z-20">
        <div className="flex gap-2">
            <Button
                variant={viewMode === 'GRID' ? 'default' : 'ghost'}
                size="sm"
                onClick={() => setViewMode('GRID')}
                className="gap-2"
            >
                <LayoutGrid size={16} /> Grid
            </Button>
            <Button
                variant={viewMode === 'CANVAS' ? 'default' : 'ghost'}
                size="sm"
                onClick={() => setViewMode('CANVAS')}
                className="gap-2"
            >
                <Network size={16} /> Canvas
            </Button>
        </div>
        <Button onClick={() => router.push('/new')} className="shadow-lg bg-primary hover:bg-primary/90 text-white font-bold rounded-full px-6 h-8 text-xs">
            <Plus className="mr-2 h-3 w-3" /> New Service
        </Button>
      </div>

      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5 }}
        className="flex-1 relative overflow-hidden bg-dot-pattern"
      >
        {viewMode === 'CANVAS' ? (
            <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={onNodeClick}
                nodeTypes={nodeTypes}
                connectionLineType={ConnectionLineType.SmoothStep}
                fitView
                className="bg-background/50"
            >
                <Background color="currentColor" gap={30} size={1} className="text-muted-foreground/20" />
                <Controls className="!bg-card !border-border !fill-foreground !shadow-lg !rounded-xl m-4" />
            </ReactFlow>
        ) : (
            <div className="h-full overflow-y-auto">
                <ServicesGrid services={services} />
            </div>
        )}
      </motion.div>
    </main>
  );
}
