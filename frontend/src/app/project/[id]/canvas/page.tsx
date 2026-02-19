"use client";

import React, { useCallback, useEffect, useState } from 'react';
import ReactFlow, {
  MiniMap,
  Controls,
  Background,
  useNodesState,
  useEdgesState,
  addEdge,
  Connection,
  Edge,
  Node,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { Card } from '@/components/ui/card';
import { servicesApi, Service } from '@/lib/api';

export default function CanvasPage() {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadServices() {
      try {
        const services = await servicesApi.list();

        if (!services || services.length === 0) {
          // Show empty state message as a node
          setNodes([{
            id: 'empty',
            position: { x: 200, y: 150 },
            data: { label: 'No services deployed yet. Deploy your first service to see it here.' },
            style: { width: 300, textAlign: 'center' as const }
          }]);
          return;
        }

        // Convert services to nodes
        const serviceNodes: Node[] = services.map((service: Service, index: number) => ({
          id: service.id.toString(),
          position: { x: 100 + (index % 3) * 200, y: 50 + Math.floor(index / 3) * 150 },
          data: { label: `${service.name}\n(${service.latest_deployment?.status || 'Unknown'})` },
          type: index === 0 ? 'input' : undefined,
          style: {
            background: service.latest_deployment?.status === 'ACTIVE' ? '#10b981' :
              service.latest_deployment?.status === 'FAILED' ? '#ef4444' : '#6366f1',
            color: 'white',
            borderRadius: 8
          }
        }));

        // Create edges between related services (for now, chain them)
        const serviceEdges: Edge[] = [];
        for (let i = 0; i < serviceNodes.length - 1; i++) {
          serviceEdges.push({
            id: `e${serviceNodes[i].id}-${serviceNodes[i + 1].id}`,
            source: serviceNodes[i].id,
            target: serviceNodes[i + 1].id,
            animated: true
          });
        }

        setNodes(serviceNodes);
        setEdges(serviceEdges);
      } catch (error) {
        console.error('Failed to load services for canvas:', error);
        setNodes([{
          id: 'error',
          position: { x: 200, y: 150 },
          data: { label: 'Failed to load services. Please try again.' },
          style: { width: 250, textAlign: 'center' as const, background: '#fecaca' }
        }]);
      } finally {
        setLoading(false);
      }
    }
    loadServices();
  }, [setNodes, setEdges]);

  const onConnect = useCallback((params: Connection) => setEdges((eds) => addEdge(params, eds)), [setEdges]);

  if (loading) {
    return (
      <div className="h-[calc(100vh-6rem)] w-full flex items-center justify-center">
        <div className="text-muted-foreground">Loading canvas...</div>
      </div>
    );
  }

  return (
    <div className="h-[calc(100vh-6rem)] w-full">
      <Card className="h-full w-full border-none shadow-none">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          fitView
          className="bg-slate-50 dark:bg-slate-900"
        >
          <Controls />
          <MiniMap />
          <Background gap={12} size={1} />
        </ReactFlow>
      </Card>
    </div>
  );
}
