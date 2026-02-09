'use client';

import { useEffect, useRef, useCallback } from 'react';

interface Cloud {
  x: number;
  y: number;
  radius: number;
  speed: number;
  opacity: number;
  phase: number;
  blobs: { ox: number; oy: number; r: number }[];
}

export function CloudHeroAnimation() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const mouseRef = useRef({ x: -1000, y: -1000 });

  const createCloud = useCallback((w: number, h: number, large?: boolean): Cloud => {
    const radius = large ? (100 + Math.random() * 140) : (50 + Math.random() * 80);
    const blobs: { ox: number; oy: number; r: number }[] = [];
    // Flat bottom, bumpy top — like real clouds
    blobs.push({ ox: 0, oy: 0, r: radius * 0.85 });
    blobs.push({ ox: -radius * 0.5, oy: -radius * 0.1, r: radius * 0.65 });
    blobs.push({ ox: radius * 0.45, oy: -radius * 0.05, r: radius * 0.7 });
    blobs.push({ ox: -radius * 0.15, oy: -radius * 0.4, r: radius * 0.6 });
    blobs.push({ ox: radius * 0.2, oy: -radius * 0.35, r: radius * 0.55 });
    blobs.push({ ox: -radius * 0.6, oy: radius * 0.1, r: radius * 0.5 });
    blobs.push({ ox: radius * 0.55, oy: radius * 0.1, r: radius * 0.5 });
    if (large) {
      blobs.push({ ox: 0, oy: -radius * 0.55, r: radius * 0.5 });
      blobs.push({ ox: radius * 0.35, oy: -radius * 0.5, r: radius * 0.45 });
    }
    return {
      x: Math.random() * (w + radius * 4) - radius * 2,
      y: h * 0.2 + Math.random() * h * 0.5,
      radius,
      speed: large ? (0.15 + Math.random() * 0.2) : (0.25 + Math.random() * 0.4),
      opacity: large ? 0.85 : (0.5 + Math.random() * 0.3),
      phase: Math.random() * Math.PI * 2,
      blobs,
    };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let w = 0, h = 0, t = 0;
    const clouds: Cloud[] = [];

    const resize = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      w = parent.clientWidth;
      h = parent.clientHeight;
      canvas.width = w;
      canvas.height = h;
    };

    const init = () => {
      resize();
      clouds.length = 0;
      // Big fluffy background clouds
      for (let i = 0; i < 5; i++) clouds.push(createCloud(w, h, true));
      // Smaller foreground clouds
      for (let i = 0; i < 6; i++) clouds.push(createCloud(w, h, false));
    };

    const drawCloud = (cloud: Cloud) => {
      const bobY = Math.sin(t * 0.0006 + cloud.phase) * 6;
      const isDark = document.documentElement.classList.contains('dark');

      for (const blob of cloud.blobs) {
        const bx = cloud.x + blob.ox;
        const by = cloud.y + blob.oy + bobY;
        const gradient = ctx.createRadialGradient(bx, by, blob.r * 0.1, bx, by, blob.r);
        if (isDark) {
          gradient.addColorStop(0, `rgba(148,163,184,${cloud.opacity * 0.3})`);
          gradient.addColorStop(0.6, `rgba(100,116,139,${cloud.opacity * 0.15})`);
          gradient.addColorStop(1, 'rgba(71,85,105,0)');
        } else {
          gradient.addColorStop(0, `rgba(255,255,255,${cloud.opacity})`);
          gradient.addColorStop(0.5, `rgba(255,255,255,${cloud.opacity * 0.7})`);
          gradient.addColorStop(0.8, `rgba(255,255,255,${cloud.opacity * 0.3})`);
          gradient.addColorStop(1, 'rgba(255,255,255,0)');
        }
        ctx.beginPath();
        ctx.arc(bx, by, blob.r, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();
      }
    };

    const animate = () => {
      t += 16;
      ctx.clearRect(0, 0, w, h);

      for (const cloud of clouds) {
        cloud.x += cloud.speed;
        if (cloud.x > w + cloud.radius * 3) {
          cloud.x = -cloud.radius * 3;
          cloud.y = h * 0.2 + Math.random() * h * 0.5;
        }
        // Mouse push
        const dx = cloud.x - mouseRef.current.x;
        const dy = cloud.y - mouseRef.current.y;
        const dist = Math.hypot(dx, dy);
        if (dist < 200 && dist > 0) {
          cloud.x += (dx / dist) * 1.2;
          cloud.y += (dy / dist) * 0.6;
        }
        drawCloud(cloud);
      }

      animRef.current = requestAnimationFrame(animate);
    };

    const handleMouse = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouseRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };
    const handleLeave = () => { mouseRef.current = { x: -1000, y: -1000 }; };

    init();
    animate();
    canvas.addEventListener('mousemove', handleMouse);
    canvas.addEventListener('mouseleave', handleLeave);
    window.addEventListener('resize', init);

    return () => {
      cancelAnimationFrame(animRef.current);
      canvas.removeEventListener('mousemove', handleMouse);
      canvas.removeEventListener('mouseleave', handleLeave);
      window.removeEventListener('resize', init);
    };
  }, [createCloud]);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 w-full h-full"
      style={{ pointerEvents: 'auto' }}
    />
  );
}
