'use client';

import React, { useEffect, useRef, useCallback, useState } from 'react';
import { Edge, useReactFlow, getSmoothStepPath } from 'reactflow';

// ── Edge color map (must match EcosystemTopology) ───────────────────────────
const EDGE_COLORS: Record<string, string> = {
  PROXY_CHAIN: '#3b82f6',
  DATABASE: '#22c55e',
  QUEUE: '#f97316',
  INTERNAL: '#a855f7',
  CACHE: '#06b6d4',
  TUNNEL: '#ec4899',
};

// ── Particle speed by edge type (lower = faster) ────────────────────────────
const EDGE_SPEED: Record<string, number> = {
  PROXY_CHAIN: 0.006,
  DATABASE: 0.003,
  QUEUE: 0.005,
  INTERNAL: 0.004,
  CACHE: 0.005,
  TUNNEL: 0.004,
};

interface Particle {
  edgeIdx: number;
  t: number;           // 0..1 progress along edge
  speed: number;
  color: string;
  size: number;
}

interface TrafficFlowAnimationProps {
  edges: Edge[];
}

export function TrafficFlowAnimation({ edges }: TrafficFlowAnimationProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const particlesRef = useRef<Particle[]>([]);
  const animFrameRef = useRef<number>(0);
  const { getViewport } = useReactFlow();
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });

  // Observe container size
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const parent = canvas.parentElement;
    if (!parent) return;

    const obs = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      setDimensions({ width, height });
    });
    obs.observe(parent);
    return () => obs.disconnect();
  }, []);

  // Resize canvas to match container
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = dimensions.width * dpr;
    canvas.height = dimensions.height * dpr;
    canvas.style.width = `${dimensions.width}px`;
    canvas.style.height = `${dimensions.height}px`;
    const ctx = canvas.getContext('2d');
    if (ctx) ctx.scale(dpr, dpr);
  }, [dimensions]);

  // Initialize particles — 2-4 per edge
  useEffect(() => {
    const particles: Particle[] = [];
    edges.forEach((edge, idx) => {
      const edgeType = (edge as any).type || '';
      // Determine type from style stroke color
      let type = 'INTERNAL';
      for (const [t, c] of Object.entries(EDGE_COLORS)) {
        if (edge.style?.stroke === c) { type = t; break; }
      }

      const count = edge.animated ? 4 : 2;
      const speed = EDGE_SPEED[type] || 0.004;
      const color = EDGE_COLORS[type] || '#52525b';

      for (let i = 0; i < count; i++) {
        particles.push({
          edgeIdx: idx,
          t: i / count,       // stagger particles evenly
          speed: speed * (0.8 + Math.random() * 0.4),  // slight variation
          color,
          size: edge.animated ? 3.5 : 2.5,
        });
      }
    });
    particlesRef.current = particles;
  }, [edges]);

  // Compute edge path points from ReactFlow edge data.
  // Accepts a pre-built per-frame cache to avoid repeated DOM queries.
  // canvasRect is snapshotted once per frame by the caller to avoid an extra layout read per particle.
  const getEdgePath = useCallback((edge: Edge, rectCache: Map<string, DOMRect>, canvasRect: DOMRect): { x1: number; y1: number; x2: number; y2: number } | null => {
    if (!canvasRef.current) return null;

    // Retrieve or populate cache entry (one DOM query per unique node per frame)
    const getRect = (nodeId: string): DOMRect | null => {
      if (rectCache.has(nodeId)) return rectCache.get(nodeId)!;
      const el = document.querySelector(`[data-id="${nodeId}"]`);
      if (!el) return null;
      const rect = el.getBoundingClientRect();
      rectCache.set(nodeId, rect);
      return rect;
    };

    const sourceRect = getRect(edge.source);
    const targetRect = getRect(edge.target);
    if (!sourceRect || !targetRect) return null;

    const viewport = getViewport();

    const x1 = (sourceRect.left + sourceRect.width / 2 - canvasRect.left - viewport.x) / viewport.zoom;
    const y1 = (sourceRect.top + sourceRect.height / 2 - canvasRect.top - viewport.y) / viewport.zoom;
    const x2 = (targetRect.left + targetRect.width / 2 - canvasRect.left - viewport.x) / viewport.zoom;
    const y2 = (targetRect.top + targetRect.height / 2 - canvasRect.top - viewport.y) / viewport.zoom;

    return { x1, y1, x2, y2 };
  }, [getViewport]);


  // Animation loop
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || edges.length === 0) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let running = true;

    const animate = () => {
      if (!running) return;

      const { width, height } = dimensions;
      if (width === 0 || height === 0) {
        animFrameRef.current = requestAnimationFrame(animate);
        return;
      }

      ctx.clearRect(0, 0, width, height);

      const particles = particlesRef.current;

      // Build a per-frame rect cache so each node is queried from DOM exactly once
      const rectCache = new Map<string, DOMRect>();
      // Also snapshot canvas rect and viewport once per frame (not per particle)
      const canvasRect = canvas.getBoundingClientRect();

      for (const p of particles) {
        const edge = edges[p.edgeIdx];
        if (!edge) continue;

        const path = getEdgePath(edge, rectCache, canvasRect);
        if (!path) continue;

        // Advance particle
        p.t += p.speed;
        if (p.t > 1) p.t -= 1;

        // Interpolate position along straight line
        // (smoothstep path approximation — good enough for particles)
        const t = p.t;
        const cx = path.x1 + (path.x2 - path.x1) * t;
        const cy = path.y1 + (path.y2 - path.y1) * t;

        // Draw particle with glow
        ctx.save();
        ctx.globalAlpha = 0.9;
        ctx.shadowColor = p.color;
        ctx.shadowBlur = 6;
        ctx.fillStyle = p.color;
        ctx.beginPath();
        ctx.arc(cx, cy, p.size, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();

        // Draw a dimmer trail
        ctx.save();
        ctx.globalAlpha = 0.3;
        ctx.fillStyle = p.color;
        ctx.beginPath();
        const trailX = path.x1 + (path.x2 - path.x1) * (t - 0.05 < 0 ? t : t - 0.05);
        const trailY = path.y1 + (path.y2 - path.y1) * (t - 0.05 < 0 ? t : t - 0.05);
        ctx.arc(trailX, trailY, p.size * 0.6, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }

      animFrameRef.current = requestAnimationFrame(animate);
    };

    animFrameRef.current = requestAnimationFrame(animate);

    return () => {
      running = false;
      cancelAnimationFrame(animFrameRef.current);
    };
  }, [edges, dimensions, getEdgePath]);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 pointer-events-none z-10"
      style={{ width: '100%', height: '100%' }}
    />
  );
}
