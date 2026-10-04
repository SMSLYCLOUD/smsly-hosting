'use client';

import { useMemo, useState, useRef, useCallback } from 'react';
import dynamic from 'next/dynamic';
import * as THREE from 'three';
// useGraphData removed as it's passed as prop
import { TopologyNode, TopologyNodeData } from '@/types/topology';
import { Loader2 } from 'lucide-react';
import { getAddonMetadata } from '@/lib/addonRegistry';
import { addonHex, SERVICE_HEX, SHARED_HEX } from '@/lib/addonColors';
import { ServiceSidePanel } from '../src/components/topology/ServiceSidePanel';
import { ErrorBoundary } from '../src/components/ErrorBoundary';

// Logo textures for addon nodes (URL -> texture). Brand marks are SVGs,
// which decode to 0x0 without intrinsic dimensions — uploading one
// straight to the GPU throws texSubImage2D every frame. So rasterize
// through a fixed-size canvas first and only hand out fully-backed
// textures. A failed load leaves the plain status-colored mesh — never
// break the graph over an image.
const logoTextureCache = new Map<string, any>();
const logoLoadStarted = new Set<string>();
function addonLogoTexture(addonType: string | undefined, onReady: (tex: any) => void): any | null {
  const url = addonType ? getAddonMetadata(addonType)?.logo : undefined;
  if (!url) return null;
  const hit = logoTextureCache.get(url);
  if (hit) return hit === 'failed' ? null : hit;
  if (typeof document === 'undefined' || logoLoadStarted.has(url)) return null;
  logoLoadStarted.add(url);
  try {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      try {
        const w = img.naturalWidth || img.width || 0;
        const h = img.naturalHeight || img.height || 0;
        if (!w || !h) throw new Error('empty image');
        const size = 128;
        const canvas = document.createElement('canvas');
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext('2d');
        if (!ctx) throw new Error('no 2d context');
        const scale = Math.min(size / w, size / h);
        const dw = Math.max(1, Math.floor(w * scale));
        const dh = Math.max(1, Math.floor(h * scale));
        ctx.clearRect(0, 0, size, size);
        ctx.drawImage(img, Math.floor((size - dw) / 2), Math.floor((size - dh) / 2), dw, dh);
        const tex = new THREE.CanvasTexture(canvas);
        if ('colorSpace' in tex) (tex as any).colorSpace = THREE.SRGBColorSpace;
        logoTextureCache.set(url, tex);
        onReady(tex);
      } catch {
        logoTextureCache.set(url, 'failed');
      }
    };
    img.onerror = () => { logoTextureCache.set(url, 'failed'); };
    img.src = url;
  } catch {
    logoTextureCache.set(url, 'failed');
  }
  return null;
}

// Dynamically import ForceGraph3D to avoid SSR issues with window/canvas
const ForceGraph3D = dynamic(() => import('react-force-graph-3d'), {
  ssr: false,
  loading: () => <div className="flex items-center justify-center h-full text-zinc-500"><Loader2 className="w-6 h-6 animate-spin mr-2" /> Loading 3D Engine...</div>
});

const NODE_REL_SIZE = 6;

// Status colors — state accents only. Node bodies are colored by TYPE
// (service blue / addon brand / shared pink) so a healthy graph is
// colorful, not an emerald blob. FAILED still overrides the body.
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

// Edge colors by dependency kind.
const LINK_COLORS: Record<string, string> = {
  DATABASE: '#818cf8', CACHE: '#f87171', QUEUE: '#fb923c',
  SEARCH: '#22d3ee', STORAGE: '#fbbf24', ADDON: '#94a3b8',
  API: '#60a5fa', DOMAIN: '#34d399', REPLICA: '#34d399',
  INTERNAL: '#475569',
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
    const nodeType = (node.type || '').toLowerCase();
    const status = (data.status || '').toUpperCase();
    const isFailed = status === 'FAILED' || status === 'ERROR';
    const isShared = !!(data as any).shared;
    // Body color: type brand (lively by design). Shared addons glow
    // pink regardless of type so shared-vs-owned is obvious at a
    // glance. FAILED overrides everything; other states show through
    // opacity while the brand stays recognizable.
    let color: string;
    if (isFailed) {
      color = STATUS_COLORS.FAILED;
    } else if (isShared) {
      color = SHARED_HEX;
    } else if (nodeType === 'addon') {
      color = addonHex(data.addon_type, data.name);
    } else if (nodeType === 'service') {
      color = SERVICE_HEX;
    } else {
      color = getNodeColor(data.status);
    }
    const dimmed = !isFailed && status !== 'ACTIVE' && status !== 'RUNNING' && status !== '';
    const material = new THREE.MeshLambertMaterial({
      color,
      transparent: true,
      opacity: dimmed ? 0.45 : 0.9,
      emissive: new THREE.Color(color).multiplyScalar(isShared ? 0.35 : 0.12),
    });

    // Addon nodes wear their registry brand mark (fully-rasterized
    // texture only; plain color until the logo is ready or has none).
    if ((node.type || '').toLowerCase() === 'addon') {
      const tex = addonLogoTexture(data.addon_type, (readyTex) => {
        material.map = readyTex;
        material.needsUpdate = true;
      });
      if (tex) material.map = tex;
    }

    const nodeTypeLower = nodeType;
    let mesh;

    // Replica nodes: smaller, rounded cube
    if (nodeTypeLower === 'replica') {
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
          linkColor={(link: any) => {
            const t = String(link?.type || '').toUpperCase();
            const shared = /shared/i.test(String(link?.label || ''));
            const base = LINK_COLORS[t] || '#ffffff30';
            return shared ? '#f472b6' : base;
          }}
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
          <div className="flex items-center gap-2"><div className="w-3 h-3 bg-blue-500 rounded-sm"></div> Service</div>
          <div className="flex items-center gap-2"><div className="w-3 h-3 rounded-sm" style={{backgroundColor: SHARED_HEX}}></div> Shared addon</div>
          <div className="flex items-center gap-2"><div className="w-3 h-3 bg-red-500 rounded-sm"></div> Failed</div>
          <div className="flex items-center gap-2"><div className="w-3 h-3 bg-emerald-500 rounded-sm opacity-60" style={{width: 8, height: 8}}></div> Replica</div>
          <div className="text-[10px] text-zinc-600 pt-1">Addons wear type colors + brand logos.<br />Dimmed = non-running state.</div>
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
