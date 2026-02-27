'use client';

/**
 * TopologyView — Railway-style 2D canvas showing services as cards
 * with connection lines. Supports pan, zoom, and click-to-navigate.
 * Pure HTML5 Canvas — no Three.js / WebGL dependencies.
 */

import { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import { servicesApi, Service } from '@/lib/api';
import { useRouter } from 'next/navigation';
import { ZoomIn, ZoomOut, Maximize2 } from 'lucide-react';

/* ── Types ── */

interface TopoNode {
  id: string;
  name: string;
  status: string;
  deployType?: string;
  repoUrl?: string;
  branch?: string;
  domain?: string;
  addons?: string[];
  // Computed layout positions
  x: number;
  y: number;
  w: number;
  h: number;
}

interface TopoEdge {
  from: string;
  to: string;
  type: 'repo' | 'domain' | 'addon';
}

/* ── Constants ── */

const NODE_W = 220;
const NODE_H = 90;
const GAP_X = 80;
const GAP_Y = 50;
const PADDING = 60;

const STATUS_COLORS: Record<string, string> = {
  ACTIVE: '#10b981',
  BUILDING: '#3b82f6',
  DEPLOYING: '#818cf8',
  QUEUED: '#fbbf24',
  FAILED: '#ef4444',
  CANCELLED: '#f97316',
  HEALTH_CHECK: '#06b6d4',
  REVIEW: '#a78bfa',
};

/* ── Deploy-type accent colors (card border + glow) ── */
const DEPLOY_TYPE_COLORS: Record<string, string> = {
  GIT: '#3b82f6',       // Blue
  DOCKER: '#06b6d4',    // Cyan
  TEMPLATE: '#a78bfa',  // Purple
};

const EDGE_COLORS: Record<string, string> = {
  repo: '#3b82f6',     // Blue
  domain: '#10b981',   // Green
  addon: '#a78bfa',    // Purple
};

const DEPLOY_ICONS: Record<string, string> = {
  GIT: '⎇',
  DOCKER: '🐳',
  TEMPLATE: '📄',
};

/* ── Helper: status dot color ── */
function statusColor(s: string): string {
  return STATUS_COLORS[s] || '#6366f1';
}

function deployAccent(dt: string): string {
  return DEPLOY_TYPE_COLORS[dt] || '#6366f1';
}

/* ── Layout: grid with 2-pass grouping ── */
function layoutNodes(services: Service[]): { nodes: TopoNode[]; edges: TopoEdge[] } {
  if (services.length === 0) return { nodes: [], edges: [] };

  // Group by repo owner or standalone
  const groups = new Map<string, Service[]>();
  for (const svc of services) {
    const key = svc.repository_url
      ? svc.repository_url.replace(/\.git$/, '').replace(/https?:\/\//, '').split('/').slice(0, 2).join('/')
      : `standalone/${svc.id}`;
    const arr = groups.get(key) || [];
    arr.push(svc);
    groups.set(key, arr);
  }

  const nodes: TopoNode[] = [];
  const edges: TopoEdge[] = [];

  // Place groups in rows
  let cursorY = PADDING;
  for (const [, groupServices] of Array.from(groups)) {
    let cursorX = PADDING;
    const prevIds: string[] = [];

    for (const svc of groupServices) {
      const node: TopoNode = {
        id: svc.id,
        name: svc.name,
        status: svc.latest_deployment?.status || 'UNKNOWN',
        deployType: svc.deploy_type || 'GIT',
        repoUrl: svc.repository_url || undefined,
        branch: svc.branch || undefined,
        domain: svc.public_domain || undefined,
        x: cursorX,
        y: cursorY,
        w: NODE_W,
        h: NODE_H,
      };
      nodes.push(node);

      // Connect siblings in the same repo group
      for (const prevId of prevIds) {
        edges.push({ from: prevId, to: svc.id, type: 'repo' });
      }
      prevIds.push(svc.id);

      cursorX += NODE_W + GAP_X;
    }
    cursorY += NODE_H + GAP_Y;
  }

  // Connect services sharing the same domain (proxy connections)
  const domainMap = new Map<string, string[]>();
  for (const n of nodes) {
    if (n.domain) {
      const baseDomain = n.domain.split('.').slice(-2).join('.');
      const arr = domainMap.get(baseDomain) || [];
      arr.push(n.id);
      domainMap.set(baseDomain, arr);
    }
  }
  for (const [, ids] of Array.from(domainMap)) {
    if (ids.length > 1) {
      for (let i = 1; i < ids.length; i++) {
        edges.push({ from: ids[0], to: ids[i], type: 'domain' });
      }
    }
  }

  return { nodes, edges };
}

/* ── Draw functions ── */

function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number,
  r: number,
) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function drawEdge(
  ctx: CanvasRenderingContext2D,
  from: TopoNode, to: TopoNode,
  type: TopoEdge['type'],
) {
  const fromCx = from.x + from.w / 2;
  const fromCy = from.y + from.h / 2;
  const toCx = to.x + to.w / 2;
  const toCy = to.y + to.h / 2;

  // Determine exit/entry points (right/left for horizontal, bottom/top for vertical)
  let x1: number, y1: number, x2: number, y2: number;
  if (Math.abs(toCx - fromCx) > Math.abs(toCy - fromCy)) {
    // Horizontal connection
    if (toCx > fromCx) {
      x1 = from.x + from.w; y1 = fromCy;
      x2 = to.x;            y2 = toCy;
    } else {
      x1 = from.x;          y1 = fromCy;
      x2 = to.x + to.w;     y2 = toCy;
    }
  } else {
    // Vertical connection
    if (toCy > fromCy) {
      x1 = fromCx; y1 = from.y + from.h;
      x2 = toCx;   y2 = to.y;
    } else {
      x1 = fromCx; y1 = from.y;
      x2 = toCx;   y2 = to.y + to.h;
    }
  }

  const edgeColor = EDGE_COLORS[type] || '#6366f1';
  const midX = (x1 + x2) / 2;

  ctx.save();
  // Much more visible lines: 70% opacity instead of 25%
  ctx.strokeStyle = edgeColor + 'b3';
  ctx.lineWidth = 2.5;
  ctx.setLineDash(type === 'domain' ? [6, 4] : []);

  // Glow effect for connections
  ctx.shadowColor = edgeColor + '60';
  ctx.shadowBlur = 6;

  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(midX, y1);
  ctx.lineTo(midX, y2);
  ctx.lineTo(x2, y2);
  ctx.stroke();

  ctx.shadowBlur = 0;

  // Colored connection dots (larger, matching edge color)
  ctx.fillStyle = edgeColor;
  ctx.beginPath();
  ctx.arc(x1, y1, 4, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.arc(x2, y2, 4, 0, Math.PI * 2);
  ctx.fill();

  // Corner dot at the bend point
  ctx.fillStyle = edgeColor + '80';
  ctx.beginPath();
  ctx.arc(midX, y1, 2.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.arc(midX, y2, 2.5, 0, Math.PI * 2);
  ctx.fill();

  ctx.restore();
}

function drawNode(ctx: CanvasRenderingContext2D, node: TopoNode, hovered: boolean) {
  const { x, y, w, h, name, status, deployType, repoUrl, branch, domain } = node;
  const sColor = statusColor(status);
  const accent = deployAccent(deployType || 'GIT');

  ctx.save();

  // Card shadow + type-colored glow
  if (hovered) {
    ctx.shadowColor = accent + '60';
    ctx.shadowBlur = 24;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 4;
  } else {
    // Subtle ambient glow based on deploy type
    ctx.shadowColor = accent + '18';
    ctx.shadowBlur = 12;
  }

  // Card background with type-tinted fill (vibrant but not overwhelming)
  drawRoundedRect(ctx, x, y, w, h, 10);
  // Base fill with deploy-type color tint
  const bgGrad = ctx.createLinearGradient(x, y, x + w, y + h);
  if (hovered) {
    bgGrad.addColorStop(0, accent + '25');  // Type color tint at 15%
    bgGrad.addColorStop(1, '#1a1f2e');
  } else {
    bgGrad.addColorStop(0, accent + '15');  // Type color tint at 8%
    bgGrad.addColorStop(1, '#0d1117');
  }
  ctx.fillStyle = bgGrad;
  ctx.fill();
  ctx.strokeStyle = hovered ? accent + 'cc' : accent + '50';
  ctx.lineWidth = hovered ? 2 : 1.2;
  ctx.stroke();

  ctx.shadowBlur = 0;
  ctx.shadowColor = 'transparent';

  // Left accent bar (deploy type color, not just status)
  drawRoundedRect(ctx, x, y, 4, h, 2);
  const gradient = ctx.createLinearGradient(x, y, x, y + h);
  gradient.addColorStop(0, accent);
  gradient.addColorStop(1, sColor);
  ctx.fillStyle = gradient;
  ctx.fill();

  // Status dot (status color)
  ctx.beginPath();
  ctx.arc(x + 18, y + 18, 5, 0, Math.PI * 2);
  ctx.fillStyle = sColor;
  ctx.fill();

  // Pulsing ring for active
  if (status === 'ACTIVE' || status === 'BUILDING') {
    ctx.beginPath();
    ctx.arc(x + 18, y + 18, 8, 0, Math.PI * 2);
    ctx.strokeStyle = sColor + '50';
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  // Service name
  ctx.font = 'bold 13px Inter, system-ui, sans-serif';
  ctx.fillStyle = '#e4e4e7';
  ctx.textBaseline = 'middle';
  const displayName = name.length > 22 ? name.slice(0, 20) + '…' : name;
  ctx.fillText(displayName, x + 30, y + 18);

  // Deploy type icon (colored by type)
  const icon = DEPLOY_ICONS[deployType || 'GIT'] || '📦';
  ctx.font = '11px Inter, system-ui, sans-serif';
  ctx.fillStyle = accent + 'cc';
  ctx.fillText(icon + ' ' + (deployType || 'GIT'), x + 14, y + 38);

  // Branch
  if (branch) {
    ctx.fillStyle = '#71717a';
    ctx.fillText('⎇ ' + (branch.length > 12 ? branch.slice(0, 10) + '…' : branch), x + 100, y + 38);
  }

  // Status label (colored badge)
  ctx.font = 'bold 10px Inter, system-ui, sans-serif';
  ctx.fillStyle = sColor;
  ctx.textAlign = 'right';
  ctx.fillText(status, x + w - 12, y + 18);

  // Domain
  ctx.textAlign = 'left';
  ctx.font = '10px Inter, system-ui, sans-serif';
  ctx.fillStyle = '#71717a';
  if (domain) {
    ctx.fillText('🌐 ' + (domain.length > 28 ? domain.slice(0, 26) + '…' : domain), x + 14, y + 56);
  }

  // Repo (short)
  if (repoUrl) {
    const short = repoUrl.replace(/https?:\/\//, '').replace('.git', '');
    ctx.fillStyle = '#52525b';
    ctx.fillText(short.length > 28 ? short.slice(0, 26) + '…' : short, x + 14, y + 72);
  }

  ctx.restore();
}

/* ── Legend ── */
const STATUS_LEGEND = [
  { color: '#10b981', label: 'Active' },
  { color: '#3b82f6', label: 'Building' },
  { color: '#fbbf24', label: 'Queued' },
  { color: '#ef4444', label: 'Failed' },
  { color: '#6366f1', label: 'Other' },
];

const TYPE_LEGEND = [
  { color: '#3b82f6', label: 'Git Deploy' },
  { color: '#06b6d4', label: 'Docker' },
  { color: '#a78bfa', label: 'Template' },
];

/* ── Main Component ── */
export function TopologyView() {
  const router = useRouter();
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [services, setServices] = useState<Service[]>([]);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [viewport, setViewport] = useState({ width: 0, height: 0 });

  // Pan / zoom state
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [scale, setScale] = useState(1);
  const isPanning = useRef(false);
  const panMoved = useRef(false);
  const panStart = useRef({ x: 0, y: 0 });
  const offsetStart = useRef({ x: 0, y: 0 });

  const updateViewport = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const width = Math.max(1, Math.floor(rect.width));
    const height = Math.max(1, Math.floor(rect.height));
    setViewport((prev) => (
      prev.width === width && prev.height === height
        ? prev
        : { width, height }
    ));
  }, []);

  // Fetch services
  useEffect(() => {
    const load = async () => {
      try {
        const data = await servicesApi.list();
        setServices(data);
      } catch (e) {
        console.error('Topology fetch failed:', e);
      }
    };
    load();
    const iv = setInterval(load, 15000);
    return () => clearInterval(iv);
  }, []);

  // Compute layout
  const { nodes, edges } = useMemo(() => layoutNodes(services), [services]);

  // Find content bounds for fit
  const bounds = useMemo(() => {
    if (nodes.length === 0) return { minX: 0, minY: 0, maxX: 800, maxY: 600 };
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of nodes) {
      minX = Math.min(minX, n.x);
      minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x + n.w);
      maxY = Math.max(maxY, n.y + n.h);
    }
    return { minX: minX - PADDING, minY: minY - PADDING, maxX: maxX + PADDING, maxY: maxY + PADDING };
  }, [nodes]);

  // Fit to view
  const fitToView = useCallback(() => {
    const el = containerRef.current;
    if (!el || nodes.length === 0) return;
    const rect = el.getBoundingClientRect();
    const contentW = bounds.maxX - bounds.minX;
    const contentH = bounds.maxY - bounds.minY;
    const scaleX = rect.width / contentW;
    const scaleY = rect.height / contentH;
    const newScale = Math.min(scaleX, scaleY, 1.5) * 0.9; // 90% to add some padding
    setScale(newScale);
    setOffset({
      x: (rect.width - contentW * newScale) / 2 - bounds.minX * newScale,
      y: (rect.height - contentH * newScale) / 2 - bounds.minY * newScale,
    });
  }, [nodes.length, bounds]);

  // Initial fit
  useEffect(() => {
    if (nodes.length > 0) fitToView();
  }, [nodes.length, fitToView]);

  // Hit test
  const hitTest = useCallback((clientX: number, clientY: number): TopoNode | null => {
    const el = containerRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    const mx = (clientX - rect.left - offset.x) / scale;
    const my = (clientY - rect.top - offset.y) / scale;
    // Reverse order for z-order (topmost first)
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      if (mx >= n.x && mx <= n.x + n.w && my >= n.y && my <= n.y + n.h) {
        return n;
      }
    }
    return null;
  }, [nodes, offset, scale]);

  // Mouse handlers
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    isPanning.current = true;
    panMoved.current = false;
    panStart.current = { x: e.clientX, y: e.clientY };
    offsetStart.current = { x: offset.x, y: offset.y };
  }, [offset]);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (isPanning.current) {
      const dx = e.clientX - panStart.current.x;
      const dy = e.clientY - panStart.current.y;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
        panMoved.current = true;
      }
      setOffset({ x: offsetStart.current.x + dx, y: offsetStart.current.y + dy });
    }
    const hit = hitTest(e.clientX, e.clientY);
    setHoveredId(hit?.id || null);
  }, [hitTest]);

  const handleMouseUp = useCallback(() => {
    isPanning.current = false;
  }, []);

  const handleClick = useCallback((e: React.MouseEvent) => {
    if (panMoved.current) {
      panMoved.current = false;
      return;
    }
    const hit = hitTest(e.clientX, e.clientY);
    if (hit) router.push(`/services/${hit.id}`);
  }, [hitTest, router]);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const factor = e.deltaY > 0 ? 0.9 : 1.1;
    const newScale = Math.max(0.2, Math.min(3, scale * factor));
    setOffset({
      x: mx - (mx - offset.x) * (newScale / scale),
      y: my - (my - offset.y) * (newScale / scale),
    });
    setScale(newScale);
  }, [scale, offset]);

  // Draw
  useEffect(() => {
    const canvas = canvasRef.current;
    const el = containerRef.current;
    if (!canvas || !el) return;

    const width = viewport.width || el.clientWidth;
    const height = viewport.height || el.clientHeight;
    if (!width || !height) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    // Background
    ctx.fillStyle = '#04070f';
    ctx.fillRect(0, 0, width, height);

    // Grid dots (Railway-style)
    ctx.save();
    ctx.translate(offset.x, offset.y);
    ctx.scale(scale, scale);

    const gridSpacing = 40;
    const startX = Math.floor(((-offset.x / scale) - 100) / gridSpacing) * gridSpacing;
    const startY = Math.floor(((-offset.y / scale) - 100) / gridSpacing) * gridSpacing;
    const endX = startX + (width / scale) + 200;
    const endY = startY + (height / scale) + 200;

    ctx.fillStyle = '#1a1a2e';
    for (let gx = startX; gx < endX; gx += gridSpacing) {
      for (let gy = startY; gy < endY; gy += gridSpacing) {
        ctx.beginPath();
        ctx.arc(gx, gy, 1, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // Draw edges first (behind nodes)
    const nodeMap = new Map(nodes.map(n => [n.id, n]));
    for (const edge of edges) {
      const from = nodeMap.get(edge.from);
      const to = nodeMap.get(edge.to);
      if (from && to) drawEdge(ctx, from, to, edge.type);
    }

    // Draw nodes
    for (const node of nodes) {
      drawNode(ctx, node, node.id === hoveredId);
    }

    ctx.restore();

    // Watermark count
    ctx.font = '11px Inter, system-ui, sans-serif';
    ctx.fillStyle = '#52525b';
    ctx.textAlign = 'left';
    ctx.fillText(`${nodes.length} service${nodes.length !== 1 ? 's' : ''} · ${edges.length} connection${edges.length !== 1 ? 's' : ''}`, 16, height - 12);

  }, [nodes, edges, offset, scale, hoveredId, viewport.height, viewport.width]);

  // Resize listener
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    updateViewport();
    const obs = new ResizeObserver(updateViewport);
    obs.observe(el);
    window.addEventListener('resize', updateViewport);
    return () => {
      obs.disconnect();
      window.removeEventListener('resize', updateViewport);
    };
  }, [updateViewport]);

  return (
    <div
      ref={containerRef}
      className="relative h-full w-full overflow-hidden bg-[#04070f]"
      style={{ cursor: hoveredId ? 'pointer' : isPanning.current ? 'grabbing' : 'grab' }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onClick={handleClick}
      onWheel={handleWheel}
    >
      <canvas ref={canvasRef} className="absolute inset-0" />

      {/* Legend */}
      <div className="absolute left-4 top-4 z-10 rounded-xl border border-zinc-800/80 bg-black/70 p-3 backdrop-blur-lg">
        <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-400">
          FLEET STATUS
        </div>
        <div className="mb-2 text-[11px] text-zinc-500">
          {services.length} service{services.length !== 1 ? 's' : ''}
        </div>
        <div className="flex flex-col gap-1.5">
          {STATUS_LEGEND.map(({ color, label }) => (
            <div key={label} className="flex items-center gap-2">
              <div className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
              <span className="text-[11px] text-zinc-300">{label}</span>
            </div>
          ))}
        </div>
        <div className="mt-3 border-t border-zinc-800 pt-2">
          <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-400">
            DEPLOY TYPE
          </div>
          <div className="flex flex-col gap-1.5">
            {TYPE_LEGEND.map(({ color, label }) => (
              <div key={label} className="flex items-center gap-2">
                <div className="h-2 w-4 rounded-sm" style={{ backgroundColor: color }} />
                <span className="text-[11px] text-zinc-300">{label}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="mt-3 border-t border-zinc-800 pt-2">
          <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-400">
            CONNECTIONS
          </div>
          <div className="flex items-center gap-2">
            <div className="w-4 border-t-2 border-blue-500/70" />
            <span className="text-[10px] text-zinc-400">Same repo</span>
          </div>
          <div className="flex items-center gap-2 mt-1">
            <div className="w-4 border-t-2 border-dashed border-emerald-500/70" />
            <span className="text-[10px] text-zinc-400">Same domain</span>
          </div>
        </div>
      </div>

      {/* Controls */}
      <div className="absolute right-4 top-4 z-10 flex items-center gap-1.5 rounded-xl border border-zinc-800/80 bg-black/55 p-1.5 backdrop-blur-lg">
        <span className="px-1 text-[10px] uppercase tracking-[0.14em] text-zinc-500">View</span>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setScale(Math.min(3, scale * 1.2)); }}
          className="flex h-8 w-8 items-center justify-center rounded-md border border-zinc-700 bg-zinc-900/85 text-zinc-300 transition-colors hover:border-zinc-500 hover:text-white"
          title="Zoom in"
        >
          <ZoomIn className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setScale(Math.max(0.2, scale * 0.8)); }}
          className="flex h-8 w-8 items-center justify-center rounded-md border border-zinc-700 bg-zinc-900/85 text-zinc-300 transition-colors hover:border-zinc-500 hover:text-white"
          title="Zoom out"
        >
          <ZoomOut className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); fitToView(); }}
          className="flex h-8 w-8 items-center justify-center rounded-md border border-zinc-700 bg-zinc-900/85 text-zinc-300 transition-colors hover:border-zinc-500 hover:text-white"
          title="Fit to view"
        >
          <Maximize2 className="w-4 h-4" />
        </button>
      </div>

      {/* Help */}
      <div className="absolute bottom-4 right-4 z-10 rounded-xl border border-zinc-800/80 bg-black/55 px-3 py-2 backdrop-blur-lg">
        <div className="text-[10px] text-zinc-400">Drag to pan · Scroll to zoom · Click node to open</div>
      </div>

      {/* Empty state */}
      {services.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center text-muted-foreground z-10">
          No services deployed. Click &ldquo;New Service&rdquo; to get started.
        </div>
      )}
    </div>
  );
}
