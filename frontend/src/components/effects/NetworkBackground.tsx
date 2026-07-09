"use client";

import { useEffect, useRef } from "react";

interface Satellite {
  angle: number;
  distance: number;
  speed: number;
  radius: number;
  pulse: number;
  pulseSpeed: number;
}

export function NetworkBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const TWO_PI = Math.PI * 2;
    let animationFrameId: number = 0;
    let satellites: Satellite[] = [];
    let resizeTimeout: ReturnType<typeof setTimeout>;
    let cx = 0;
    let cy = 0;

    const initSatellites = () => {
      satellites = [];
      const isMobile = window.innerWidth < 768;
      const count = isMobile ? 12 : 20;
      const maxDist = isMobile ? 180 : 300;

      cx = canvas.width * 0.5;
      cy = canvas.height * 0.4;

      for (let i = 0; i < count; i++) {
        satellites.push({
          angle: Math.random() * TWO_PI,
          distance: 60 + Math.random() * maxDist,
          speed: 0.002 + Math.random() * 0.006,
          radius: 1.5 + Math.random() * 3,
          pulse: Math.random() * TWO_PI,
          pulseSpeed: 0.03 + Math.random() * 0.05,
        });
      }
    };

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      initSatellites();
    };

    const handleResize = () => {
      clearTimeout(resizeTimeout);
      resizeTimeout = setTimeout(resize, 200);
    };

    const drawServerNode = (x: number, y: number, size: number, alpha: number) => {
      // Outer ring
      ctx.globalAlpha = alpha * 0.15;
      ctx.beginPath();
      ctx.arc(x, y, size * 2.8, 0, TWO_PI);
      ctx.strokeStyle = '#10b981';
      ctx.lineWidth = 1;
      ctx.stroke();

      // Middle ring
      ctx.globalAlpha = alpha * 0.25;
      ctx.beginPath();
      ctx.arc(x, y, size * 1.8, 0, TWO_PI);
      ctx.strokeStyle = '#10b981';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // Inner ring
      ctx.globalAlpha = alpha * 0.4;
      ctx.beginPath();
      ctx.arc(x, y, size * 1.1, 0, TWO_PI);
      ctx.strokeStyle = '#34d399';
      ctx.lineWidth = 1;
      ctx.stroke();

      // Server body — hexagon
      ctx.globalAlpha = alpha * 0.5;
      ctx.beginPath();
      for (let i = 0; i < 6; i++) {
        const a = (TWO_PI / 6) * i - Math.PI / 6;
        const px = x + size * Math.cos(a);
        const py = y + size * Math.sin(a);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.fillStyle = '#10b981';
      ctx.fill();
      ctx.strokeStyle = '#34d399';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // Center dot
      ctx.globalAlpha = alpha * 0.9;
      ctx.beginPath();
      ctx.arc(x, y, size * 0.25, 0, TWO_PI);
      ctx.fillStyle = '#6ee7b7';
      ctx.fill();
    };

    const draw = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const pulse = 0.5 + Math.sin(Date.now() * 0.001) * 0.5;
      const serverAlpha = 0.4 + pulse * 0.3;

      // Draw the large central server node
      drawServerNode(cx, cy, 32, serverAlpha);

      // Draw satellites and connections
      for (let i = 0; i < satellites.length; i++) {
        const sat = satellites[i];
        sat.angle += sat.speed;
        sat.pulse += sat.pulseSpeed;

        const sx = cx + Math.cos(sat.angle) * sat.distance;
        const sy = cy + Math.sin(sat.angle) * sat.distance;

        // Connection line to central server
        const distFromCenter = Math.sqrt((sx - cx) ** 2 + (sy - cy) ** 2);
        const lineAlpha = (1 - distFromCenter / 350) * 0.12;
        ctx.globalAlpha = lineAlpha;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(sx, sy);
        ctx.strokeStyle = '#10b981';
        ctx.lineWidth = 0.5;
        ctx.stroke();

        // Satellite node
        const satAlpha = 0.3 + Math.abs(Math.sin(sat.pulse)) * 0.4;
        ctx.globalAlpha = satAlpha * 0.6;
        ctx.beginPath();
        ctx.arc(sx, sy, sat.radius, 0, TWO_PI);
        ctx.fillStyle = '#94a3b8';
        ctx.fill();

        // Satellite glow
        ctx.globalAlpha = satAlpha * 0.15;
        ctx.beginPath();
        ctx.arc(sx, sy, sat.radius * 3, 0, TWO_PI);
        ctx.fillStyle = '#10b981';
        ctx.fill();

        // Connections between nearby satellites
        for (let j = i + 1; j < satellites.length; j++) {
          const other = satellites[j];
          const ox = cx + Math.cos(other.angle) * other.distance;
          const oy = cy + Math.sin(other.angle) * other.distance;
          const dx = sx - ox;
          const dy = sy - oy;
          const distSq = dx * dx + dy * dy;

          if (distSq < 40000) {
            const dist = Math.sqrt(distSq);
            const connAlpha = (1 - dist / 200) * 0.06;
            ctx.globalAlpha = connAlpha;
            ctx.beginPath();
            ctx.moveTo(sx, sy);
            ctx.lineTo(ox, oy);
            ctx.strokeStyle = '#10b981';
            ctx.lineWidth = 0.3;
            ctx.stroke();
          }
        }
      }

      ctx.globalAlpha = 1.0;
      animationFrameId = requestAnimationFrame(draw);
    };

    resize();
    draw();

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      clearTimeout(resizeTimeout);
      if (animationFrameId) cancelAnimationFrame(animationFrameId);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 z-0 h-full w-full pointer-events-none"
    />
  );
}
