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

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  opacity: number;
  color: string;
}

export function CloudHeroAnimation() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const mouseRef = useRef({ x: -1000, y: -1000 });

  const createCloud = useCallback((w: number, h: number, large?: boolean): Cloud => {
    const radius = large ? (120 + Math.random() * 160) : (60 + Math.random() * 100);
    const blobCount = large ? (6 + Math.floor(Math.random() * 4)) : (4 + Math.floor(Math.random() * 3));
    const blobs: { ox: number; oy: number; r: number }[] = [];
    // Main body blob
    blobs.push({ ox: 0, oy: 0, r: radius * 0.8 });
    // Top bumps
    blobs.push({ ox: -radius * 0.4, oy: -radius * 0.3, r: radius * 0.7 });
    blobs.push({ ox: radius * 0.3, oy: -radius * 0.4, r: radius * 0.6 });
    blobs.push({ ox: radius * 0.1, oy: -radius * 0.15, r: radius * 0.75 });
    for (let i = 4; i < blobCount; i++) {
      blobs.push({
        ox: (Math.random() - 0.5) * radius * 1.5,
        oy: (Math.random() - 0.6) * radius * 0.8,
        r: radius * (0.4 + Math.random() * 0.5),
      });
    }
    return {
      x: Math.random() * w,
      y: h * 0.15 + Math.random() * h * 0.6,
      radius,
      speed: large ? (0.2 + Math.random() * 0.3) : (0.3 + Math.random() * 0.5),
      opacity: large ? (0.12 + Math.random() * 0.1) : (0.08 + Math.random() * 0.08),
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
    const particles: Particle[] = [];

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
      particles.length = 0;
      // Big background clouds
      for (let i = 0; i < 4; i++) clouds.push(createCloud(w, h, true));
      // Smaller foreground clouds
      for (let i = 0; i < 5; i++) clouds.push(createCloud(w, h, false));

      const particleCount = Math.max(20, Math.floor(w / 50));
      for (let i = 0; i < particleCount; i++) {
        particles.push({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.4,
          vy: (Math.random() - 0.5) * 0.25,
          radius: 1.5 + Math.random() * 2,
          opacity: 0.15 + Math.random() * 0.35,
          color: Math.random() > 0.5 ? '16,185,129' : '52,211,153',
        });
      }
    };

    const drawCloud = (cloud: Cloud) => {
      const isDark = document.documentElement.classList.contains('dark');
      const bobY = Math.sin(t * 0.0008 + cloud.phase) * 8;

      for (const blob of cloud.blobs) {
        const bx = cloud.x + blob.ox;
        const by = cloud.y + blob.oy + bobY;
        const gradient = ctx.createRadialGradient(bx, by, 0, bx, by, blob.r);
        if (isDark) {
          gradient.addColorStop(0, `rgba(110,231,183,${cloud.opacity * 1.2})`);
          gradient.addColorStop(0.4, `rgba(52,211,153,${cloud.opacity * 0.6})`);
          gradient.addColorStop(1, 'rgba(16,185,129,0)');
        } else {
          gradient.addColorStop(0, `rgba(255,255,255,${cloud.opacity * 4})`);
          gradient.addColorStop(0.3, `rgba(236,253,245,${cloud.opacity * 2.5})`);
          gradient.addColorStop(0.7, `rgba(209,250,229,${cloud.opacity * 1})`);
          gradient.addColorStop(1, 'rgba(167,243,208,0)');
        }
        ctx.beginPath();
        ctx.arc(bx, by, blob.r, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();
      }
    };

    const drawConnections = () => {
      const mx = mouseRef.current.x;
      const my = mouseRef.current.y;
      for (let i = 0; i < particles.length; i++) {
        const a = particles[i];
        const dm = Math.hypot(a.x - mx, a.y - my);
        if (dm < 150) {
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(mx, my);
          ctx.strokeStyle = `rgba(16,185,129,${0.25 * (1 - dm / 150)})`;
          ctx.lineWidth = 0.5;
          ctx.stroke();
        }
        for (let j = i + 1; j < particles.length; j++) {
          const b = particles[j];
          const d = Math.hypot(a.x - b.x, a.y - b.y);
          if (d < 100) {
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.strokeStyle = `rgba(16,185,129,${0.12 * (1 - d / 100)})`;
            ctx.lineWidth = 0.5;
            ctx.stroke();
          }
        }
      }
    };

    const animate = () => {
      t += 16;
      ctx.clearRect(0, 0, w, h);

      // Update and draw clouds
      for (const cloud of clouds) {
        cloud.x += cloud.speed;
        if (cloud.x > w + cloud.radius * 3) {
          cloud.x = -cloud.radius * 3;
          cloud.y = h * 0.15 + Math.random() * h * 0.6;
        }
        const dx = cloud.x - mouseRef.current.x;
        const dy = cloud.y - mouseRef.current.y;
        const dist = Math.hypot(dx, dy);
        if (dist < 250 && dist > 0) {
          cloud.x += (dx / dist) * 0.8;
          cloud.y += (dy / dist) * 0.4;
        }
        drawCloud(cloud);
      }

      // Particles
      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < 0 || p.x > w) p.vx *= -1;
        if (p.y < 0 || p.y > h) p.vy *= -1;

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${p.color},${p.opacity})`;
        ctx.fill();

        // Glow
        const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.radius * 4);
        glow.addColorStop(0, `rgba(${p.color},${p.opacity * 0.25})`);
        glow.addColorStop(1, `rgba(${p.color},0)`);
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius * 4, 0, Math.PI * 2);
        ctx.fillStyle = glow;
        ctx.fill();
      }

      drawConnections();
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
