"use client";

import React, { useCallback } from 'react';
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

// Custom Nodes would go here (ServiceNode, DatabaseNode) - using defaults for now

const initialNodes: Node[] = [
  { id: '1', position: { x: 250, y: 0 }, data: { label: 'Frontend (Next.js)' }, type: 'input' },
  { id: '2', position: { x: 100, y: 150 }, data: { label: 'Backend (Django)' } },
  { id: '3', position: { x: 400, y: 150 }, data: { label: 'Worker (Celery)' } },
  { id: '4', position: { x: 100, y: 300 }, data: { label: 'Postgres DB' }, type: 'output' },
  { id: '5', position: { x: 400, y: 300 }, data: { label: 'Redis Cache' }, type: 'output' },
];

const initialEdges: Edge[] = [
  { id: 'e1-2', source: '1', target: '2', animated: true },
  { id: 'e2-3', source: '2', target: '3', animated: true },
  { id: 'e2-4', source: '2', target: '4' },
  { id: 'e3-5', source: '3', target: '5' },
];

export default function CanvasPage() {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  const onConnect = useCallback((params: Connection) => setEdges((eds) => addEdge(params, eds)), [setEdges]);

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
