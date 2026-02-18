'use client';

/**
 * TopologyView — 3D force-directed graph of services + addons.
 * Full 360° orbit, zoom, pan. Uses react-force-graph-3d (Three.js).
 */

import { useEffect, useState, useRef, useCallback } from 'react';
import api from '@/lib/api';
import { useRouter } from 'next/navigation';
import dynamic from 'next/dynamic';

// @ts-ignore — react-force-graph-3d has incomplete types
const ForceGraph3D = dynamic(() => import('react-force-graph-3d'), { ssr: false });

/* ── Types ── */
interface TopoNode {
  id: string;
  name: string;
  type: string;
  addonType?: string;
  status?: string;
  serviceId?: string;
  x?: number; y?: number; z?: number;
}

interface TopoLink {
  source: string;
  target: string;
  type?: string;
}

interface GraphData {
  nodes: TopoNode[];
  links: TopoLink[];
}

/* ── Color Palette ── */
const NODE_COLORS: Record<string, string> = {
  service: '#10b981',
  POSTGRES: '#818cf8',
  REDIS: '#f87171',
  MYSQL: '#fbbf24',
  MONGODB: '#a78bfa',
  ELASTICSEARCH: '#38bdf8',
  RABBITMQ: '#fb923c',
  volume: '#eab308',
  default: '#6366f1',
};

const LINK_COLORS: Record<string, string> = {
  API: '#60a5fa',
  DATABASE: '#818cf8',
  CACHE: '#f87171',
  STORAGE: '#fbbf24',
  ADDON: '#64748b',
};

function getNodeColor(node: TopoNode): string {
  if (node.type === 'service') return NODE_COLORS.service;
  if (node.type === 'addon') return NODE_COLORS[node.addonType?.toUpperCase() || ''] || NODE_COLORS.default;
  return NODE_COLORS.volume;
}

/* ── 3D Node rendering (lazy — only runs client-side) ── */
function createNodeObject(node: TopoNode): any {
  const THREE = require('three');
  const group = new THREE.Group();
  const color = getNodeColor(node);

  const geometry = node.type === 'service'
    ? new THREE.IcosahedronGeometry(6, 1)
    : new THREE.SphereGeometry(4, 16, 16);

  const material = new THREE.MeshPhongMaterial({
    color: new THREE.Color(color),
    emissive: new THREE.Color(color),
    emissiveIntensity: 0.3,
    transparent: true,
    opacity: 0.9,
    shininess: 80,
  });
  group.add(new THREE.Mesh(geometry, material));

  if (node.type === 'service') {
    const ringGeo = new THREE.RingGeometry(7.5, 9.0, 32);
    const ringMat = new THREE.MeshBasicMaterial({
      color: new THREE.Color(color), transparent: true, opacity: 0.2, side: THREE.DoubleSide,
    });
    group.add(new THREE.Mesh(ringGeo, ringMat));
  }

  // Text label
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d')!;
  canvas.width = 256;
  canvas.height = 64;
  ctx.fillStyle = 'transparent';
  ctx.fillRect(0, 0, 256, 64);

  ctx.font = 'bold 24px Inter, system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillStyle = '#ffffff';
  ctx.fillText(node.name.length > 18 ? node.name.slice(0, 16) + '…' : node.name, 128, 28);

  ctx.font = '16px Inter, system-ui, sans-serif';
  ctx.fillStyle = color;
  const label = node.type === 'service' ? 'SERVICE' : (node.addonType || node.type).toUpperCase();
  ctx.fillText(label, 128, 52);

  const texture = new THREE.CanvasTexture(canvas);
  const spriteMat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
  const sprite = new THREE.Sprite(spriteMat);
  sprite.scale.set(28, 7, 1);
  sprite.position.set(0, node.type === 'service' ? -12 : -9, 0);
  group.add(sprite);

  return group;
}

/* ── Main Component ── */
export function TopologyView() {
  const router = useRouter();
  const containerRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>(null);
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });

  useEffect(() => {
    if (!containerRef.current) return;
    const obs = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      setDimensions({ width: Math.max(width, 400), height: Math.max(height, 400) });
    });
    obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const res = await api.get('/topology/');
        const rawNodes = res?.data?.nodes || [];
        const rawEdges = res?.data?.edges || [];

        const nodes: TopoNode[] = rawNodes
          .map((n: any) => ({
            id: String(n?.id || ''),
            name: String(n?.data?.name || n?.id || ''),
            type: String(n?.type || 'node'),
            addonType: n?.data?.addon_type || '',
            status: n?.data?.status || '',
            serviceId: n?.data?.service_id || '',
          }))
          .filter((n: TopoNode) => n.id);

        const links: TopoLink[] = rawEdges
          .map((e: any) => ({
            source: String(e?.source || ''),
            target: String(e?.target || ''),
            type: String(e?.type || 'ADDON'),
          }))
          .filter((e: TopoLink) => e.source && e.target);

        setGraphData({ nodes, links });
      } catch (err) {
        console.error('Topology fetch failed:', err);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (fgRef.current && graphData.nodes.length > 0) {
      const fg = fgRef.current;
      const dist = 150 + graphData.nodes.length * 20;
      fg.cameraPosition({ x: dist * 0.7, y: dist * 0.5, z: dist }, { x: 0, y: 0, z: 0 }, 1500);
      fg.d3Force('charge')?.strength(-300);
      fg.d3Force('link')?.distance(80);

      let angle = 0;
      const radius = dist;
      const rotateInterval = setInterval(() => {
        angle += 0.002;
        fg.cameraPosition(
          { x: radius * Math.sin(angle), y: 60, z: radius * Math.cos(angle) },
          { x: 0, y: 0, z: 0 }
        );
      }, 30);

      const container = containerRef.current;
      const stopRotate = () => clearInterval(rotateInterval);
      container?.addEventListener('mousedown', stopRotate, { once: true });
      container?.addEventListener('touchstart', stopRotate, { once: true });

      return () => {
        clearInterval(rotateInterval);
        container?.removeEventListener('mousedown', stopRotate);
        container?.removeEventListener('touchstart', stopRotate);
      };
    }
  }, [graphData]);

  const handleNodeClick = useCallback((node: any) => {
    if (node.type === 'service') router.push(`/services/${node.id}`);
  }, [router]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-emerald-500/30 border-t-emerald-500 rounded-full animate-spin" />
          <span>Loading 3D Topology...</span>
        </div>
      </div>
    );
  }

  if (graphData.nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500">
        No topology data available. Deploy a service to begin.
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative w-full h-full min-h-[500px] bg-[#060609] rounded-xl overflow-hidden">
      <div className="absolute top-4 left-4 z-10 bg-black/60 backdrop-blur-md rounded-lg p-3 border border-zinc-800">
        <div className="text-[10px] text-zinc-400 uppercase tracking-wider mb-2 font-semibold">Legend</div>
        <div className="flex flex-col gap-1.5">
          {[
            { color: NODE_COLORS.service, label: 'Service' },
            { color: NODE_COLORS.POSTGRES, label: 'PostgreSQL' },
            { color: NODE_COLORS.REDIS, label: 'Redis' },
            { color: NODE_COLORS.ELASTICSEARCH, label: 'Elasticsearch' },
            { color: NODE_COLORS.MYSQL, label: 'MySQL' },
            { color: NODE_COLORS.MONGODB, label: 'MongoDB' },
          ].map(({ color, label }) => (
            <div key={label} className="flex items-center gap-2">
              <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: color }} />
              <span className="text-[11px] text-zinc-300">{label}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="absolute bottom-4 right-4 z-10 bg-black/60 backdrop-blur-md rounded-lg px-3 py-2 border border-zinc-800">
        <div className="text-[10px] text-zinc-500">
          🖱️ Drag to orbit • Scroll to zoom • Click service to open
        </div>
      </div>

      <ForceGraph3D
        ref={fgRef}
        width={dimensions.width}
        height={dimensions.height}
        graphData={graphData}
        backgroundColor="#060609"
        nodeThreeObject={(node: any) => createNodeObject(node as TopoNode)}
        nodeThreeObjectExtend={false}
        linkColor={(link: any) => LINK_COLORS[link.type] || '#64748b'}
        linkWidth={1.5}
        linkOpacity={0.4}
        linkCurvature={0.15}
        linkDirectionalParticles={2}
        linkDirectionalParticleWidth={1.5}
        linkDirectionalParticleSpeed={0.005}
        linkDirectionalParticleColor={(link: any) => LINK_COLORS[link.type] || '#64748b'}
        onNodeClick={handleNodeClick}
        enableNodeDrag={true}
        enableNavigationControls={true}
        showNavInfo={false}
        warmupTicks={50}
        cooldownTicks={100}
      />
    </div>
  );
}
