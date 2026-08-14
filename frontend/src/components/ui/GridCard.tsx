"use client";

import { useEffect, useRef, ReactNode } from "react";

/**
 * GridCard — Card with animated canvas grid background.
 *
 * Renders a live grid on canvas as the card background.
 * Nodes at intersections pulse subtly.
 */

interface GridCardProps {
  children: ReactNode;
  className?: string;
}

const GRID_SIZE = 24;
const NODE_RADIUS = 1;
const NODE_ALPHA = 0.15;
const LINE_ALPHA = 0.06;
const MAJOR_INTERVAL = 4;
const MAJOR_ALPHA = 0.12;

export function GridCard({ children, className = "" }: GridCardProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;

    let raf: number;

    const draw = () => {
      const W = c.width;
      const H = c.height;
      ctx.clearRect(0, 0, W, H);

      // Minor lines
      ctx.strokeStyle = `rgba(100, 116, 139, ${LINE_ALPHA})`;
      ctx.lineWidth = 0.5;

      for (let x = 0; x <= W; x += GRID_SIZE) {
        if (Math.round(x / GRID_SIZE) % MAJOR_INTERVAL === 0) continue;
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, H);
        ctx.stroke();
      }
      for (let y = 0; y <= H; y += GRID_SIZE) {
        if (Math.round(y / GRID_SIZE) % MAJOR_INTERVAL === 0) continue;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(W, y);
        ctx.stroke();
      }

      // Major lines
      ctx.strokeStyle = `rgba(100, 116, 139, ${MAJOR_ALPHA})`;
      ctx.lineWidth = 0.5;

      for (let x = 0; x <= W; x += GRID_SIZE) {
        if (Math.round(x / GRID_SIZE) % MAJOR_INTERVAL !== 0) continue;
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, H);
        ctx.stroke();
      }
      for (let y = 0; y <= H; y += GRID_SIZE) {
        if (Math.round(y / GRID_SIZE) % MAJOR_INTERVAL !== 0) continue;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(W, y);
        ctx.stroke();
      }

      // Intersection nodes at major crossings
      for (let x = 0; x <= W; x += GRID_SIZE) {
        if (Math.round(x / GRID_SIZE) % MAJOR_INTERVAL !== 0) continue;
        for (let y = 0; y <= H; y += GRID_SIZE) {
          if (Math.round(y / GRID_SIZE) % MAJOR_INTERVAL !== 0) continue;
          ctx.beginPath();
          ctx.arc(x, y, NODE_RADIUS, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(16, 185, 129, ${NODE_ALPHA})`;
          ctx.fill();
        }
      }
    };

    const resize = () => {
      const rect = c.parentElement?.getBoundingClientRect();
      if (rect) {
        c.width = rect.width;
        c.height = rect.height;
        draw();
      }
    };

    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(c.parentElement!);

    return () => observer.disconnect();
  }, []);

  return (
    <div className={`relative overflow-hidden ${className}`}>
      <canvas
        ref={canvasRef}
        className="absolute inset-0 pointer-events-none"
        aria-hidden="true"
      />
      <div className="relative z-10">
        {children}
      </div>
    </div>
  );
}
