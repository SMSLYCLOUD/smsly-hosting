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
import * as THREE from 'three';
import { Maximize2, ZoomIn, ZoomOut } from 'lucide-react';

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

function hashSeed(value: string): number {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) - hash + value.charCodeAt(index)) >>> 0;
  }
  return hash;
}

function stableMetricWidth(seed: string, min: number, max: number): number {
  if (max <= min) {
    return min;
  }
  return min + (hashSeed(seed) % (max - min + 1));
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
  const group = new THREE.Group();
  const color = statusColor(node.status);

  // Nucleus sphere
  const geometry = new THREE.SphereGeometry(10, 32, 32);
  const material = new THREE.MeshPhongMaterial({
    color: new THREE.Color(color),
    emissive: new THREE.Color(color),
    emissiveIntensity: 0.5,
    transparent: true,
    opacity: 0.9,
    shininess: 120,
  });
  group.add(new THREE.Mesh(geometry, material));

  // Outer glow shell
  const glowGeo = new THREE.SphereGeometry(15, 16, 16);
  const glowMat = new THREE.MeshBasicMaterial({
    color: new THREE.Color(color), transparent: true, opacity: 0.08, side: THREE.BackSide,
  });
  group.add(new THREE.Mesh(glowGeo, glowMat));

  // Electron shell orbits (3 rings at different angles)
  for (let i = 0; i < 3; i++) {
    const ringGeo = new THREE.RingGeometry(13 + i * 2.5, 13.5 + i * 2.5, 64);
    const ringMat = new THREE.MeshBasicMaterial({
      color: new THREE.Color(color), transparent: true, opacity: 0.12 - i * 0.03, side: THREE.DoubleSide,
    });
    const ring = new THREE.Mesh(ringGeo, ringMat);
    ring.rotation.x = Math.PI / 2 + (i * Math.PI / 4);
    ring.rotation.y = i * Math.PI / 3;
    group.add(ring);
  }

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
  ctx.fillRect(80, barY, stableMetricWidth(`${node.id}:cpu`, 30, 55), 4);
  ctx.fillStyle = '#f472b6';
  ctx.fillRect(180, barY, stableMetricWidth(`${node.id}:ram`, 20, 55), 4);
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
  const hasInitialFitRef = useRef(false);
  const nodeObjectCacheRef = useRef<Map<string, { key: string; object: any }>>(new Map());
  const initialDistanceRef = useRef(220);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });

  const measureContainer = useCallback(() => {
    const el = containerRef.current;
    if (!el) {
      return;
    }
    const rect = el.getBoundingClientRect();
    const nextWidth = Math.max(Math.floor(rect.width), 480);
    const nextHeight = Math.max(Math.floor(rect.height), 420);
    setDimensions((prev) => (
      prev.width === nextWidth && prev.height === nextHeight
        ? prev
        : { width: nextWidth, height: nextHeight }
    ));
  }, []);

  useEffect(() => {
    measureContainer();
    if (!containerRef.current) {
      return;
    }

    const obs = new ResizeObserver(() => {
      measureContainer();
    });
    obs.observe(containerRef.current);
    window.addEventListener('resize', measureContainer);
    let rafId = 0;
    let rafRuns = 0;
    const pulseMeasure = () => {
      measureContainer();
      rafRuns += 1;
      if (rafRuns < 6) {
        rafId = window.requestAnimationFrame(pulseMeasure);
      }
    };
    rafId = window.requestAnimationFrame(pulseMeasure);

    return () => {
      window.cancelAnimationFrame(rafId);
      obs.disconnect();
      window.removeEventListener('resize', measureContainer);
    };
  }, [measureContainer]);

  const graphData = React.useMemo(() => {
    nodeObjectCacheRef.current.clear();
    hasInitialFitRef.current = false;
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

  const zoomCamera = useCallback((factor: number) => {
    const fg = fgRef.current;
    if (!fg) return;
    const camera = fg.camera?.();
    if (!camera?.position) return;

    const { x, y, z } = camera.position;
    const currentDistance = Math.sqrt(x * x + y * y + z * z) || 1;
    const nextDistance = Math.max(80, Math.min(2200, currentDistance * factor));
    const scale = nextDistance / currentDistance;

    fg.cameraPosition(
      { x: x * scale, y: y * scale, z: z * scale },
      { x: 0, y: 0, z: 0 },
      300
    );
  }, []);

  const resetCamera = useCallback(() => {
    const fg = fgRef.current;
    if (!fg) return;
    const dist = initialDistanceRef.current;
    fg.cameraPosition(
      { x: dist * 0.6, y: dist * 0.4, z: dist },
      { x: 0, y: 0, z: 0 },
      450
    );
  }, []);

  const fitGraphToViewport = useCallback((duration = 450) => {
    const fg = fgRef.current;
    if (!fg || graphData.nodes.length === 0 || dimensions.width === 0 || dimensions.height === 0) {
      return;
    }
    if (typeof fg.zoomToFit === 'function') {
      try {
        fg.zoomToFit(duration, 120);
      } catch (error) {
        console.error('zoomToFit failed:', error);
      }
    }
  }, [graphData.nodes.length, dimensions.height, dimensions.width]);

  const getServiceNodeObject = useCallback((node: SvcNode) => {
    const cacheKey = `${node.id}:${node.name}:${node.status}:${node.repoUrl || ''}`;
    const cached = nodeObjectCacheRef.current.get(node.id);
    if (cached?.key === cacheKey) {
      return cached.object;
    }
    const object = createServiceNode(node);
    nodeObjectCacheRef.current.set(node.id, { key: cacheKey, object });
    return object;
  }, []);

  useEffect(() => {
    if (fgRef.current && graphData.nodes.length > 0) {
      const fg = fgRef.current;
      const dist = 120 + graphData.nodes.length * 30;
      initialDistanceRef.current = dist;

      fg.cameraPosition({ x: dist * 0.6, y: dist * 0.4, z: dist }, { x: 0, y: 0, z: 0 }, 1500);
      fg.d3Force('charge')?.strength(-250);
      fg.d3ReheatSimulation?.();

      const fitTimer = setTimeout(() => {
        if (!hasInitialFitRef.current) {
          fitGraphToViewport(700);
          hasInitialFitRef.current = true;
        }
      }, 250);
      return () => {
        clearTimeout(fitTimer);
      };
    }
  }, [fitGraphToViewport, graphData.nodes.length]);

  useEffect(() => {
    if (graphData.nodes.length === 0 || !hasInitialFitRef.current) {
      return;
    }
    const fitTimer = setTimeout(() => fitGraphToViewport(250), 120);
    return () => {
      clearTimeout(fitTimer);
    };
  }, [dimensions.height, dimensions.width, graphData.nodes.length, fitGraphToViewport]);

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
    <div ref={containerRef} className="relative h-full w-full min-h-[500px] overflow-hidden bg-[#04070f]">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(16,185,129,0.08),transparent_45%),radial-gradient(circle_at_80%_30%,rgba(59,130,246,0.09),transparent_42%),radial-gradient(circle_at_50%_85%,rgba(99,102,241,0.08),transparent_48%)]" />
      <div className="pointer-events-none absolute left-1/2 top-1/2 h-[40rem] w-[40rem] -translate-x-1/2 -translate-y-1/2 rounded-full border border-zinc-700/15" />
      <div className="pointer-events-none absolute left-1/2 top-1/2 h-[30rem] w-[30rem] -translate-x-1/2 -translate-y-1/2 rounded-full border border-zinc-700/20" />
      <div className="pointer-events-none absolute left-1/2 top-1/2 h-[20rem] w-[20rem] -translate-x-1/2 -translate-y-1/2 rounded-full border border-zinc-700/25" />
      <div className="absolute left-4 top-4 z-10 w-44 rounded-xl border border-zinc-800/80 bg-black/55 p-3 backdrop-blur-lg">
        <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-400">
          Fleet Status
        </div>
        <div className="mb-2 text-[11px] text-zinc-500">
          {services.length} service{services.length !== 1 ? 's' : ''}
        </div>
        <div className="flex flex-col gap-1.5">
          {[
            { color: '#10b981', label: 'Active' },
            { color: '#3b82f6', label: 'Building' },
            { color: '#fbbf24', label: 'Queued' },
            { color: '#ef4444', label: 'Failed' },
          ].map(({ color, label }) => (
            <div key={label} className="flex items-center gap-2">
              <div className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
              <span className="text-[11px] text-zinc-300">{label}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="absolute bottom-4 right-4 z-10 rounded-xl border border-zinc-800/80 bg-black/55 px-3 py-2 backdrop-blur-lg">
        <div className="text-[10px] text-zinc-400">Drag to orbit | Scroll to zoom | Click node to open</div>
      </div>

      <div className="absolute right-4 top-4 z-10 flex items-center gap-1.5 rounded-xl border border-zinc-800/80 bg-black/55 p-1.5 backdrop-blur-lg">
        <span className="px-1 text-[10px] uppercase tracking-[0.14em] text-zinc-500">View</span>
        <button
          type="button"
          onClick={() => zoomCamera(0.82)}
          className="flex h-8 w-8 items-center justify-center rounded-md border border-zinc-700 bg-zinc-900/85 text-zinc-300 transition-colors hover:border-zinc-500 hover:text-white"
          title="Zoom in"
          aria-label="Zoom in"
        >
          <ZoomIn className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={() => zoomCamera(1.22)}
          className="flex h-8 w-8 items-center justify-center rounded-md border border-zinc-700 bg-zinc-900/85 text-zinc-300 transition-colors hover:border-zinc-500 hover:text-white"
          title="Zoom out"
          aria-label="Zoom out"
        >
          <ZoomOut className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={resetCamera}
          className="flex h-8 w-8 items-center justify-center rounded-md border border-zinc-700 bg-zinc-900/85 text-zinc-300 transition-colors hover:border-zinc-500 hover:text-white"
          title="Reset view"
          aria-label="Reset view"
        >
          <Maximize2 className="w-4 h-4" />
        </button>
      </div>

      <div className="absolute inset-0">
        {dimensions.width > 0 && dimensions.height > 0 && (
          <ForceGraph3D
            ref={fgRef}
            width={dimensions.width}
            height={dimensions.height}
            graphData={graphData}
            backgroundColor="#04070f"
            nodeThreeObject={(node: any) => getServiceNodeObject(node as SvcNode)}
            nodeThreeObjectExtend={false}
            onNodeClick={handleNodeClick}
            enableNodeDrag={true}
            enableNavigationControls={true}
            showNavInfo={false}
            warmupTicks={30}
            cooldownTicks={80}
          />
        )}
      </div>
    </div>
  );
}
