'use client';

import { useMemo, useState, useRef, useCallback } from 'react';
import dynamic from 'next/dynamic';
import * as THREE from 'three';
// useGraphData removed as it's passed as prop
import { TopologyNode, TopologyNodeData } from '@/types/topology';
import { Loader2 } from 'lucide-react';
import { ServiceSidePanel } from '../src/components/topology/ServiceSidePanel';
import { ErrorBoundary } from '../src/components/ErrorBoundary';

// Dynamically import ForceGraph3D to avoid SSR issues with window/canvas
const ForceGraph3D = dynamic(() => import('react-force-graph-3d'), {
  ssr: false,
  loading: () => <div className="flex items-center justify-center h-full text-zinc-500"><Loader2 className="w-6 h-6 animate-spin mr-2" /> Loading 3D Engine...</div>
});

const NODE_REL_SIZE = 6;

// Status colors
const STATUS_COLORS: Record<string, string> = {
  ACTIVE: '#10b981', // Emerald
  RUNNING: '#10b981',
  BUILDING: '#3b82f6', // Blue
  DEPLOYING: '#818cf8', // Indigo
  PROVISIONING: '#818cf8',
  QUEUED: '#fbbf24', // Amber
  FAILED: '#ef4444', // Red
  STOPPED: '#71717a', // Zinc
  UNKNOWN: '#71717a',
};

// Geometries
const boxGeometry = new THREE.BoxGeometry(10, 10, 10);
const cylinderGeometry = new THREE.CylinderGeometry(5, 5, 12, 16);
const sphereGeometry = new THREE.SphereGeometry(6, 16, 16);
const octahedronGeometry = new THREE.OctahedronGeometry(6);
const torusGeometry = new THREE.TorusGeometry(5, 2, 16, 32);

function getNodeColor(status: string) {
  return STATUS_COLORS[status?.toUpperCase()] || STATUS_COLORS.UNKNOWN;
}

export function Topology3D({ data, loading, error, refresh }: { data: any, loading: boolean, error: any, refresh: any }) {
  const [selectedNode, setSelectedNode] = useState<TopologyNode | null>(null);
  const fgRef = useRef<any>(null);

  // Camera focus on node click
  const handleNodeClick = useCallback((node: any) => {
    setSelectedNode(node);

    // Aim at node from outside it
    const distance = 40;
    const distRatio = 1 + distance/Math.hypot(node.x, node.y, node.z);

    if (fgRef.current) {
      fgRef.current.cameraPosition(
        { x: node.x * distRatio, y: node.y * distRatio, z: node.z * distRatio }, // new position
        node, // lookAt ({ x, y, z })
        3000  // ms transition duration
      );
    }
  }, []);

  const nodeThreeObject = useCallback((node: any) => {
    const data = node.data as TopologyNodeData;
    const color = getNodeColor(data.status);
    const material = new THREE.MeshLambertMaterial({
      color,
      transparent: true,
      opacity: 0.9,
    });

    const nodeType = (node.type || '').toLowerCase();
    let mesh;

    // Replica nodes: smaller, rounded cube
    if (nodeType === 'replica') {
      const replicaMaterial = new THREE.MeshLambertMaterial({
        color,
        transparent: true,
        opacity: 0.75,
      });
      mesh = new THREE.Mesh(boxGeometry, replicaMaterial);
      mesh.scale.set(0.5, 0.5, 0.5);
      return mesh;
    }

    switch (data.kind) {
      case 'COMPUTE':
        mesh = new THREE.Mesh(boxGeometry, material);
        break;
      case 'DATABASE':
        mesh = new THREE.Mesh(cylinderGeometry, material);
        break;
      case 'CACHE':
        mesh = new THREE.Mesh(sphereGeometry, material);
        break;
      case 'QUEUE':
        mesh = new THREE.Mesh(octahedronGeometry, material);
        break;
      case 'STORAGE':
        mesh = new THREE.Mesh(boxGeometry, material);
        mesh.scale.set(1, 0.5, 1); // Flat box
        break;
      case 'EXTERNAL':
        mesh = new THREE.Mesh(torusGeometry, material);
        break;
      default:
        mesh = new THREE.Mesh(sphereGeometry, material);
    }

    return mesh;
  }, []);

  // Prepare data for ForceGraph3D (needs 'links', not 'edges')
  const graphData = useMemo(() => {
    if (!data) return { nodes: [], links: [] };

    // Create deep copy to avoid mutating state
    const nodes = (data.nodes || []).map((n: TopologyNode) => ({ ...n }));
    // Map 'edges' to 'links' if present, otherwise look for 'links'
    const links = (data.edges || (data as any).links || []).map((e: any) => ({
      ...e,
      source: e.source, // Ensure source/target are preserved
      target: e.target
    }));

    return { nodes, links };
  }, [data]);

  if (loading && !data) return <div className="flex h-full items-center justify-center"><Loader2 className="w-8 h-8 animate-spin text-zinc-500" /></div>;
  if (error) return <div className="flex h-full items-center justify-center text-red-500">Error loading topology: {error.message}</div>;

  return (
    <ErrorBoundary fallback={<div className="flex items-center justify-center h-full text-red-500">Failed to render 3D Topology. Please try refreshing.</div>}>
      <div className="relative h-full w-full bg-[#04070f] overflow-hidden">
        <ForceGraph3D
          ref={fgRef}
          graphData={graphData}
          nodeLabel={(node: any) => `${node.data?.name || node.id} (${node.data?.kind || 'UNKNOWN'})`}
          nodeThreeObject={nodeThreeObject}
          nodeRelSize={NODE_REL_SIZE}
          linkColor={() => '#ffffff30'}
          linkDirectionalArrowLength={3.5}
          linkDirectionalArrowRelPos={1}
          onNodeClick={handleNodeClick}
          backgroundColor="#04070f"
          showNavInfo={false}
          cooldownTicks={100}
          onEngineStop={() => fgRef.current?.zoomToFit(400)}
        />

        {/* Overlay: Legend or Controls */}
      <div className="absolute top-4 left-4 p-4 bg-black/60 backdrop-blur-md rounded-lg border border-zinc-800 pointer-events-none">
        <h3 className="text-sm font-semibold text-zinc-300 mb-2">3D Topology</h3>
        <div className="space-y-1 text-xs text-zinc-500">
          <div className="flex items-center gap-2"><div className="w-3 h-3 bg-emerald-500 rounded-sm"></div> Active</div>
          <div className="flex items-center gap-2"><div className="w-3 h-3 bg-blue-500 rounded-sm"></div> Building</div>
          <div className="flex items-center gap-2"><div className="w-3 h-3 bg-red-500 rounded-sm"></div> Failed</div>
          <div className="flex items-center gap-2"><div className="w-3 h-3 bg-emerald-500 rounded-sm opacity-60" style={{width: 8, height: 8}}></div> Replica</div>
          <div className="mt-2 pt-2 border-t border-zinc-800">
            <p>Left-click: Rotate</p>
            <p>Right-click: Pan</p>
            <p>Scroll: Zoom</p>
            <p>Click Node: Focus</p>
          </div>
        </div>
      </div>

        {/* Side Panel for Selected Node */}
        {selectedNode && (
          <ServiceSidePanel node={selectedNode} onClose={() => setSelectedNode(null)} />
        )}
      </div>
    </ErrorBoundary>
  );
}
