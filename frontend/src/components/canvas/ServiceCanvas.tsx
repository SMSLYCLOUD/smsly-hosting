'use client';

/**
 * ServiceCanvas — 3D force-directed graph of all services.
 * Each service is a glowing node; click to navigate.
 * Full 360° orbit, zoom, pan.
 */

import React, { useEffect, useRef, useCallback, useState } from 'react';
import { Service } from '@/lib/api';
import { useRouter } from 'next/navigation';
import dynamic from 'next/dynamic';

// @ts-ignore
const ForceGraph3D = dynamic(() => import('react-force-graph-3d'), { ssr: false });

interface ServiceCanvasProps {
  services: Service[];
}

interface SvcNode {
  id: string;
  name: string;
  repoUrl?: string;
  status: string;
  framework?: string;
}

interface SvcLink {
  source: string;
  target: string;
}

/* ── Status colors ── */
function statusColor(status: string): string {
  switch (status) {
    case 'ACTIVE': return '#10b981';
    case 'BUILDING': return '#3b82f6';
    case 'QUEUED': return '#fbbf24';
    case 'FAILED': return '#ef4444';
    case 'CANCELLED': return '#f97316';
    default: return '#6366f1';
  }
}

/* ── 3D Node (lazy THREE) ── */
function createServiceNode(node: SvcNode): any {
  const THREE = require('three');
  const group = new THREE.Group();
  const color = statusColor(node.status);

  // Main icosahedron
  const geometry = new THREE.IcosahedronGeometry(7, 1);
  const material = new THREE.MeshPhongMaterial({
    color: new THREE.Color(color),
    emissive: new THREE.Color(color),
    emissiveIntensity: 0.35,
    transparent: true,
    opacity: 0.9,
    shininess: 80,
  });
  group.add(new THREE.Mesh(geometry, material));

  // Outer glow ring
  const ringGeo = new THREE.RingGeometry(9, 11, 32);
  const ringMat = new THREE.MeshBasicMaterial({
    color: new THREE.Color(color), transparent: true, opacity: 0.15, side: THREE.DoubleSide,
  });
  group.add(new THREE.Mesh(ringGeo, ringMat));

  // Status dot
  const dotGeo = new THREE.SphereGeometry(1.5, 8, 8);
  const dotMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(color) });
  const dot = new THREE.Mesh(dotGeo, dotMat);
  dot.position.set(9, 6, 0);
  group.add(dot);

  // Text label
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d')!;
  canvas.width = 320;
  canvas.height = 96;

  ctx.fillStyle = 'transparent';
  ctx.fillRect(0, 0, 320, 96);

  ctx.font = 'bold 26px Inter, system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillStyle = '#ffffff';
  ctx.fillText(node.name.length > 18 ? node.name.slice(0, 16) + '…' : node.name, 160, 30);

  if (node.repoUrl) {
    ctx.font = '14px Inter, system-ui, sans-serif';
    ctx.fillStyle = '#71717a';
    const repoShort = node.repoUrl.replace('https://', '').replace('http://', '');
    ctx.fillText(repoShort.length > 30 ? repoShort.slice(0, 28) + '…' : repoShort, 160, 52);
  }

  ctx.font = 'bold 14px Inter, system-ui, sans-serif';
  ctx.fillStyle = color;
  ctx.fillText(node.status, 160, 74);

  // CPU/RAM bars
  const barY = 82;
  ctx.fillStyle = '#27272a';
  ctx.fillRect(80, barY, 60, 4);
  ctx.fillRect(180, barY, 60, 4);
  ctx.fillStyle = '#3b82f6';
  ctx.fillRect(80, barY, 30 + Math.random() * 25, 4);
  ctx.fillStyle = '#f472b6';
  ctx.fillRect(180, barY, 20 + Math.random() * 35, 4);
  ctx.font = '10px Inter, system-ui, sans-serif';
  ctx.fillStyle = '#52525b';
  ctx.fillText('CPU', 60, barY + 4);
  ctx.fillText('RAM', 164, barY + 4);

  const texture = new THREE.CanvasTexture(canvas);
  const spriteMat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
  const sprite = new THREE.Sprite(spriteMat);
  sprite.scale.set(36, 10.8, 1);
  sprite.position.set(0, -14, 0);
  group.add(sprite);

  return group;
}

/* ── Component ── */
export function ServiceCanvas({ services }: ServiceCanvasProps) {
  const router = useRouter();
  const containerRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>(null);
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

  const graphData = React.useMemo(() => {
    const nodes: SvcNode[] = services.map((svc) => ({
      id: svc.id,
      name: svc.name,
      repoUrl: svc.repository_url,
      status: svc.latest_deployment?.status || 'UNKNOWN',
      framework: svc.buildpack,
    }));
    const links: SvcLink[] = [];
    return { nodes, links };
  }, [services]);

  useEffect(() => {
    if (fgRef.current && graphData.nodes.length > 0) {
      const fg = fgRef.current;
      const dist = 120 + graphData.nodes.length * 30;

      fg.cameraPosition({ x: dist * 0.6, y: dist * 0.4, z: dist }, { x: 0, y: 0, z: 0 }, 1500);
      fg.d3Force('charge')?.strength(-250);

      let angle = 0;
      const radius = dist;
      const rotateInterval = setInterval(() => {
        angle += 0.002;
        fg.cameraPosition(
          { x: radius * Math.sin(angle), y: 50, z: radius * Math.cos(angle) },
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
    router.push(`/services/${node.id}`);
  }, [router]);

  if (services.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground">
        No services deployed. Click &quot;New Service&quot; to get started.
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative w-full h-full bg-[#060609] overflow-hidden">
      <div className="absolute top-4 left-4 z-10 bg-black/60 backdrop-blur-md rounded-lg p-3 border border-zinc-800">
        <div className="text-[10px] text-zinc-400 uppercase tracking-wider mb-2 font-semibold">
          {services.length} Service{services.length !== 1 ? 's' : ''}
        </div>
        <div className="flex flex-col gap-1">
          {[
            { color: '#10b981', label: 'Active' },
            { color: '#3b82f6', label: 'Building' },
            { color: '#fbbf24', label: 'Queued' },
            { color: '#ef4444', label: 'Failed' },
          ].map(({ color, label }) => (
            <div key={label} className="flex items-center gap-1.5">
              <div className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
              <span className="text-[10px] text-zinc-300">{label}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="absolute bottom-4 right-4 z-10 bg-black/60 backdrop-blur-md rounded-lg px-3 py-2 border border-zinc-800">
        <div className="text-[10px] text-zinc-500">🖱️ Drag to orbit • Scroll to zoom • Click to open</div>
      </div>

      <ForceGraph3D
        ref={fgRef}
        width={dimensions.width}
        height={dimensions.height}
        graphData={graphData}
        backgroundColor="#060609"
        nodeThreeObject={(node: any) => createServiceNode(node as SvcNode)}
        nodeThreeObjectExtend={false}
        onNodeClick={handleNodeClick}
        enableNodeDrag={true}
        enableNavigationControls={true}
        showNavInfo={false}
        warmupTicks={30}
        cooldownTicks={80}
      />
    </div>
  );
}
