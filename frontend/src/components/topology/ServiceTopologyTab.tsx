'use client';

/**
 * ServiceTopologyTab — 3D force graph of a single service + its addons/volumes.
 * Full 360° orbit, zoom, pan.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Loader2, Database } from 'lucide-react';
import dynamic from 'next/dynamic';
import api from '@/lib/api';
import { addonsApi } from '@/lib/api';

// @ts-ignore
const ForceGraph3D = dynamic(() => import('react-force-graph-3d'), { ssr: false });

/* ── Types ── */
interface Addon {
  id: string;
  name: string;
  addon_type: string;
  status: string;
}

interface Volume {
  id: string;
  name: string;
  mount_path: string;
  size_gb: number;
}

interface GraphNode {
  id: string;
  name: string;
  nodeType: 'service' | 'addon' | 'volume';
  subType?: string;
  status?: string;
  detail?: string;
}

interface GraphLink {
  source: string;
  target: string;
  linkType: string;
}

/* ── Colors ── */
const COLORS: Record<string, string> = {
  service: '#10b981',
  POSTGRES: '#818cf8',
  REDIS: '#f87171',
  MYSQL: '#22d3ee',
  MONGODB: '#4ade80',
  ELASTICSEARCH: '#38bdf8',
  RABBITMQ: '#fb923c',
  MINIO: '#f472b6',
  QDRANT: '#a78bfa',
  volume: '#eab308',
  default: '#6366f1',
};

function getColor(node: GraphNode): string {
  if (node.nodeType === 'service') return COLORS.service;
  if (node.nodeType === 'addon') return COLORS[node.subType?.toUpperCase() || ''] || COLORS.default;
  return COLORS.volume;
}

/* ── 3D Node (lazy THREE) ── */
function createNode3D(node: GraphNode): any {
  const THREE = require('three');
  const group = new THREE.Group();
  const color = getColor(node);

  let geometry;
  if (node.nodeType === 'service') {
    // Nucleus: large glowing sphere
    geometry = new THREE.SphereGeometry(10, 32, 32);
  } else if (node.nodeType === 'addon') {
    // Electron: smaller, faceted
    geometry = new THREE.SphereGeometry(5, 16, 16);
  } else {
    geometry = new THREE.BoxGeometry(5, 5, 5);
  }

  const material = new THREE.MeshPhongMaterial({
    color: new THREE.Color(color),
    emissive: new THREE.Color(color),
    emissiveIntensity: node.nodeType === 'service' ? 0.5 : 0.35,
    transparent: true,
    opacity: 0.9,
    shininess: 120,
  });
  group.add(new THREE.Mesh(geometry, material));

  // Outer glow shell
  const glowSize = node.nodeType === 'service' ? 14 : 7;
  const glowGeo = new THREE.SphereGeometry(glowSize, 16, 16);
  const glowMat = new THREE.MeshBasicMaterial({
    color: new THREE.Color(color), transparent: true, opacity: 0.08, side: THREE.BackSide,
  });
  group.add(new THREE.Mesh(glowGeo, glowMat));

  if (node.nodeType === 'service') {
    // Electron shell rings (3 orbits at different angles)
    for (let i = 0; i < 3; i++) {
      const ringGeo = new THREE.RingGeometry(13 + i * 2, 13.5 + i * 2, 64);
      const ringMat = new THREE.MeshBasicMaterial({
        color: new THREE.Color(color), transparent: true, opacity: 0.12 - i * 0.03, side: THREE.DoubleSide,
      });
      const ring = new THREE.Mesh(ringGeo, ringMat);
      ring.rotation.x = Math.PI / 2 + (i * Math.PI / 4);
      ring.rotation.y = i * Math.PI / 3;
      group.add(ring);
    }
  }

  // Status indicator
  if (node.status) {
    const statusColor = node.status === 'ACTIVE' ? '#10b981' : node.status === 'FAILED' ? '#ef4444' : '#fbbf24';
    const dotGeo = new THREE.SphereGeometry(1.2, 8, 8);
    const dotMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(statusColor) });
    const dot = new THREE.Mesh(dotGeo, dotMat);
    dot.position.set(node.nodeType === 'service' ? 9 : 6, node.nodeType === 'service' ? 6 : 4, 0);
    group.add(dot);
  }

  // Text label
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d')!;
  canvas.width = 300;
  canvas.height = 80;
  ctx.fillStyle = 'transparent';
  ctx.fillRect(0, 0, 300, 80);

  ctx.font = `bold ${node.nodeType === 'service' ? 28 : 22}px Inter, system-ui, sans-serif`;
  ctx.textAlign = 'center';
  ctx.fillStyle = '#ffffff';
  ctx.fillText(node.name.length > 20 ? node.name.slice(0, 18) + '…' : node.name, 150, 32);

  ctx.font = '16px Inter, system-ui, sans-serif';
  ctx.fillStyle = color;
  const label = node.nodeType === 'service' ? 'SERVICE'
    : node.nodeType === 'addon' ? (node.subType || 'ADDON').toUpperCase()
    : 'VOLUME';
  ctx.fillText(label, 150, 56);

  if (node.detail) {
    ctx.font = '13px Inter, system-ui, sans-serif';
    ctx.fillStyle = '#a1a1aa';
    ctx.fillText(node.detail.length > 25 ? node.detail.slice(0, 23) + '…' : node.detail, 150, 74);
  }

  const texture = new THREE.CanvasTexture(canvas);
  const spriteMat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
  const sprite = new THREE.Sprite(spriteMat);
  sprite.scale.set(32, 8.5, 1);
  sprite.position.set(0, node.nodeType === 'service' ? -15 : -11, 0);
  group.add(sprite);

  return group;
}



/* ── Component ── */
export function ServiceTopologyTab({ serviceId, serviceName }: { serviceId: string; serviceName: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>(null);
  const [graphData, setGraphData] = useState<{ nodes: GraphNode[]; links: GraphLink[] }>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [dimensions, setDimensions] = useState({ width: 800, height: 500 });

  useEffect(() => {
    if (!containerRef.current) return;
    const obs = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      setDimensions({ width: Math.max(width, 400), height: Math.max(height, 400) });
    });
    obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, []);

  const fetchData = useCallback(async () => {
    try {
      const [allAddons, volumeRes] = await Promise.all([
        addonsApi.list().catch(() => []),
        api.get(`/services/${serviceId}/storage/`).catch(() => ({ data: [] })),
      ]);
      const volumeData = volumeRes?.data || [];

      const nodes: GraphNode[] = [];
      const links: GraphLink[] = [];

      nodes.push({ id: serviceId, name: serviceName, nodeType: 'service', status: 'ACTIVE' });

      // Filter addons for this service
      const serviceAddons = allAddons.filter((a: any) => a.service === serviceId);
      serviceAddons.forEach((addon: Addon) => {
        nodes.push({ id: addon.id, name: addon.name, nodeType: 'addon', subType: addon.addon_type, status: addon.status });
        links.push({ source: serviceId, target: addon.id, linkType: addon.addon_type === 'REDIS' ? 'CACHE' : 'DATABASE' });
      });

      // Volumes
      const volumes = Array.isArray(volumeData) ? volumeData : (volumeData?.results || []);
      volumes.forEach((vol: Volume) => {
        nodes.push({ id: vol.id, name: vol.name, nodeType: 'volume', detail: `${vol.mount_path} · ${vol.size_gb}GB` });
        links.push({ source: serviceId, target: vol.id, linkType: 'STORAGE' });
      });

      setGraphData({ nodes, links });
    } catch (e) {
      console.error('Topology fetch error:', e);
    } finally {
      setLoading(false);
    }
  }, [serviceId, serviceName]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [fetchData]);

  useEffect(() => {
    if (fgRef.current && graphData.nodes.length > 0) {
      const fg = fgRef.current;
      const dist = 100 + graphData.nodes.length * 25;

      fg.cameraPosition({ x: dist * 0.6, y: dist * 0.4, z: dist }, { x: 0, y: 0, z: 0 }, 1500);
      fg.d3Force('charge')?.strength(-300);
      fg.d3Force('link')?.distance(45);

      let angle = 0;
      const radius = dist;
      const rotateInterval = setInterval(() => {
        angle += 0.003;
        fg.cameraPosition(
          { x: radius * Math.sin(angle), y: 40, z: radius * Math.cos(angle) },
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

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground gap-2">
        <Loader2 className="w-5 h-5 animate-spin" /> Loading 3D topology...
      </div>
    );
  }

  if (graphData.nodes.length <= 1) {
    return (
      <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
        <div>
          <h2 className="text-xl font-bold text-foreground">Service Topology</h2>
          <p className="text-sm text-muted-foreground mt-1">Visual map of {serviceName}&apos;s connected infrastructure</p>
        </div>
        <div className="bg-card border border-border rounded-xl p-8 min-h-[300px] flex flex-col items-center justify-center">
          <Database className="w-10 h-10 text-muted-foreground/30 mb-3" />
          <p className="text-sm text-muted-foreground">No connected resources</p>
          <p className="text-xs text-muted-foreground/60 mt-1">Add addons from the Addons tab to see them here</p>
        </div>
      </div>
    );
  }

  const LINK_COLORS: Record<string, string> = {
    DATABASE: '#818cf8', CACHE: '#f87171', STORAGE: '#fbbf24', ADDON: '#64748b',
  };

  return (
    <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4">
      <div>
        <h2 className="text-xl font-bold text-foreground">Service Topology</h2>
        <p className="text-sm text-muted-foreground mt-1">3D map of {serviceName}&apos;s infrastructure</p>
      </div>

      <div ref={containerRef} className="relative w-full h-[500px] bg-[#060609] rounded-xl overflow-hidden border border-zinc-800">
        <div className="absolute top-3 left-3 z-10 bg-black/60 backdrop-blur-md rounded-lg p-2.5 border border-zinc-800">
          <div className="text-[9px] text-zinc-400 uppercase tracking-wider mb-1.5 font-semibold">Legend</div>
          <div className="flex flex-col gap-1">
            {[
              { color: COLORS.service, label: 'Service' },
              ...graphData.nodes
                .filter(n => n.nodeType === 'addon')
                .map(n => ({ color: getColor(n), label: (n.subType || 'addon').toUpperCase() }))
                .filter((v, i, a) => a.findIndex(x => x.label === v.label) === i),
              ...(graphData.nodes.some(n => n.nodeType === 'volume')
                ? [{ color: COLORS.volume, label: 'Volume' }] : []),
            ].map(({ color, label }) => (
              <div key={label} className="flex items-center gap-1.5">
                <div className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
                <span className="text-[10px] text-zinc-300">{label}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="absolute bottom-3 right-3 z-10 bg-black/60 backdrop-blur-md rounded-lg px-2.5 py-1.5 border border-zinc-800">
          <div className="text-[9px] text-zinc-500">🖱️ Drag to orbit • Scroll to zoom</div>
        </div>

        <ForceGraph3D
          ref={fgRef}
          width={dimensions.width}
          height={dimensions.height}
          graphData={graphData}
          backgroundColor="#060609"
          nodeThreeObject={(node: any) => createNode3D(node as GraphNode)}
          nodeThreeObjectExtend={false}
          linkColor={(link: any) => LINK_COLORS[link.linkType] || '#64748b'}
          linkWidth={3.5}
          linkOpacity={0.6}
          linkCurvature={0.15}
          linkDirectionalParticles={5}
          linkDirectionalParticleWidth={3}
          linkDirectionalParticleSpeed={0.008}
          linkDirectionalParticleColor={(link: any) => LINK_COLORS[link.linkType] || '#64748b'}
          enableNodeDrag={true}
          enableNavigationControls={true}
          showNavInfo={false}
          warmupTicks={30}
          cooldownTicks={60}
        />
      </div>
    </div>
  );
}
