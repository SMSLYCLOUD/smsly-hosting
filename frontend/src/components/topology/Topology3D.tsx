'use client';

import { useMemo, useState, useRef, useCallback } from 'react';
import dynamic from 'next/dynamic';
import * as THREE from 'three';
import { useGraphData } from '@/hooks/useGraphData';
import { TopologyNode, TopologyNodeData } from '@/types/topology';
import { Loader2 } from 'lucide-react';
import { ServiceSidePanel } from './ServiceSidePanel';
import { ErrorBoundary } from '../ErrorBoundary';

const ForceGraph3D = dynamic(() => import('react-force-graph-3d'), {
  ssr: false,
  loading: () => <div className="flex items-center justify-center h-full text-zinc-500"><Loader2 className="w-6 h-6 animate-spin mr-2" /> Loading 3D Engine...</div>
});

const NODE_REL_SIZE = 6;

/* ── Status Colors ── */
const STATUS_COLORS: Record<string, string> = {
  ACTIVE: '#10b981',
  RUNNING: '#10b981',
  BUILDING: '#3b82f6',
  DEPLOYING: '#818cf8',
  PROVISIONING: '#818cf8',
  QUEUED: '#fbbf24',
  FAILED: '#ef4444',
  STOPPED: '#71717a',
  UNKNOWN: '#71717a',
};

/* ── Kind Colors (shapes already differ, but give unique tints) ── */
const KIND_COLORS: Record<string, string> = {
  COMPUTE: '#10b981',
  DATABASE: '#a78bfa',
  CACHE: '#f472b6',
  QUEUE: '#fb923c',
  STORAGE: '#eab308',
  SEARCH: '#38bdf8',
  EXTERNAL: '#6366f1',
};

/* ── Edge Colors by Type ── */
const EDGE_COLORS: Record<string, string> = {
  OWNS: '#52525b',
  CONNECTS_TO: '#3b82f6',
};

/* ── Geometries (created once) ── */
const boxGeometry = new THREE.BoxGeometry(10, 10, 10);
const cylinderGeometry = new THREE.CylinderGeometry(5, 5, 12, 16);
const sphereGeometry = new THREE.SphereGeometry(6, 16, 16);
const octahedronGeometry = new THREE.OctahedronGeometry(6);
const torusGeometry = new THREE.TorusGeometry(5, 2, 16, 32);

function getNodeColor(node: TopologyNodeData) {
  // Use status color first, fall back to kind color
  return STATUS_COLORS[node.status?.toUpperCase()] || KIND_COLORS[node.kind] || STATUS_COLORS.UNKNOWN;
}

export function Topology3D() {
  const { data, loading, error, refresh } = useGraphData(10000);
  const [selectedNode, setSelectedNode] = useState<TopologyNode | null>(null);
  const fgRef = useRef<any>(null);

  const handleNodeClick = useCallback((node: any) => {
    setSelectedNode(node);
    const distance = 40;
    const distRatio = 1 + distance / Math.hypot(node.x, node.y, node.z);
    if (fgRef.current) {
      fgRef.current.cameraPosition(
        { x: node.x * distRatio, y: node.y * distRatio, z: node.z * distRatio },
        node,
        3000
      );
    }
  }, []);

  const nodeThreeObject = useCallback((node: any) => {
    const data = node.data as TopologyNodeData;
    const color = getNodeColor(data);
    const material = new THREE.MeshLambertMaterial({
      color,
      transparent: true,
      opacity: 0.9,
    });

    let mesh;
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
        mesh.scale.set(1, 0.5, 1);
        break;
      case 'EXTERNAL':
        mesh = new THREE.Mesh(torusGeometry, material);
        break;
      default:
        mesh = new THREE.Mesh(sphereGeometry, material);
    }

    // Add glow
    const glowMaterial = new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.15,
    });
    const glowMesh = new THREE.Mesh(new THREE.SphereGeometry(10, 16, 16), glowMaterial);
    mesh.add(glowMesh);

    return mesh;
  }, []);

  const graphData = useMemo(() => {
    if (!data) return { nodes: [], links: [] };
    const nodes = (data.nodes || []).map(n => ({ ...n }));
    const links = (data.edges || (data as any).links || []).map((e: any) => ({
      ...e,
      source: e.source,
      target: e.target,
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
          nodeLabel={(node: any) => {
            const d = node.data as TopologyNodeData;
            return `<div style="background:#111;padding:6px 10px;border-radius:6px;border:1px solid #333;font-size:12px">
              <b style="color:${getNodeColor(d)}">${d?.name || node.id}</b><br/>
              <span style="color:#888">${d?.status || 'UNKNOWN'} • ${d?.kind || '?'}</span>
            </div>`;
          }}
          nodeThreeObject={nodeThreeObject}
          nodeRelSize={NODE_REL_SIZE}
          /* Bold connections */
          linkColor={(link: any) => EDGE_COLORS[link.type] || '#ffffff40'}
          linkWidth={2}
          linkOpacity={0.6}
          /* Directional particles */
          linkDirectionalParticles={4}
          linkDirectionalParticleWidth={2}
          linkDirectionalParticleSpeed={0.005}
          linkDirectionalParticleColor={(link: any) => EDGE_COLORS[link.type] || '#ffffff60'}
          /* Directional arrows */
          linkDirectionalArrowLength={4}
          linkDirectionalArrowRelPos={1}
          linkDirectionalArrowColor={(link: any) => EDGE_COLORS[link.type] || '#ffffff60'}
          onNodeClick={handleNodeClick}
          backgroundColor="#04070f"
          showNavInfo={false}
          cooldownTicks={100}
          onEngineStop={() => fgRef.current?.zoomToFit(400)}
        />

        {/* Legend */}
        <div className="absolute top-4 left-4 p-4 bg-black/60 backdrop-blur-md rounded-lg border border-zinc-800 pointer-events-none">
          <h3 className="text-sm font-semibold text-zinc-300 mb-3">3D Topology</h3>

          <div className="space-y-1 text-xs text-zinc-500 mb-3">
            <p className="text-zinc-400 font-semibold mb-1">STATUS</p>
            <div className="flex items-center gap-2"><div className="w-3 h-3 bg-emerald-500 rounded-sm"></div> Active</div>
            <div className="flex items-center gap-2"><div className="w-3 h-3 bg-blue-500 rounded-sm"></div> Building</div>
            <div className="flex items-center gap-2"><div className="w-3 h-3 bg-amber-400 rounded-sm"></div> Queued</div>
            <div className="flex items-center gap-2"><div className="w-3 h-3 bg-red-500 rounded-sm"></div> Failed</div>
          </div>

          <div className="space-y-1 text-xs text-zinc-500 mb-3 pt-2 border-t border-zinc-800">
            <p className="text-zinc-400 font-semibold mb-1">KIND</p>
            <div className="flex items-center gap-2"><div className="w-3 h-3 bg-violet-400 rounded-sm"></div> Database</div>
            <div className="flex items-center gap-2"><div className="w-3 h-3 bg-pink-400 rounded-sm"></div> Cache</div>
            <div className="flex items-center gap-2"><div className="w-3 h-3 bg-orange-400 rounded-sm"></div> Queue</div>
            <div className="flex items-center gap-2"><div className="w-3 h-3 bg-yellow-400 rounded-sm"></div> Storage</div>
          </div>

          <div className="space-y-1 text-xs text-zinc-500 pt-2 border-t border-zinc-800">
            <p className="text-zinc-400 font-semibold mb-1">CONNECTIONS</p>
            <div className="flex items-center gap-2"><div className="w-6 h-0.5 bg-zinc-600"></div> Owns</div>
            <div className="flex items-center gap-2"><div className="w-6 h-0.5 bg-blue-500"></div> Connects To</div>
          </div>

          <div className="mt-3 pt-2 border-t border-zinc-800 text-xs text-zinc-600">
            <p>Left-click: Rotate</p>
            <p>Right-click: Pan</p>
            <p>Scroll: Zoom</p>
            <p>Click Node: Focus</p>
          </div>
        </div>

        {selectedNode && (
          <ServiceSidePanel node={selectedNode} onClose={() => setSelectedNode(null)} />
        )}
      </div>
    </ErrorBoundary>
  );
}
