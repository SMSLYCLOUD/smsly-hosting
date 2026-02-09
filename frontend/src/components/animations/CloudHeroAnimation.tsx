'use client';

import { useEffect, useRef, useCallback } from 'react';

interface Cloud {
  x: number;
  y: number;
  radius: number;
  speed: number;
  opacity: number;
  phase: number;
  drift: number;
  blobs: { ox: number; oy: number; r: number }[];
}

export function CloudHeroAnimation() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const mouseRef = useRef({ x: -1000, y: -1000 });

  const createCloud = useCallback((w: number, h: number): Cloud => {
    // Truly random sizes — from small wisps to giant formations
    const sizeRoll = Math.random();
    const radius = sizeRoll > 0.7
      ? (140 + Math.random() * 160) // 30% chance: big cloud
      : sizeRoll > 0.3
        ? (70 + Math.random() * 80) // 40% chance: medium cloud  
        : (30 + Math.random() * 50); // 30% chance: small wisp

    // Random number of blobs (3-12)
    const blobCount = 3 + Math.floor(Math.random() * 10);
    const blobs: { ox: number; oy: number; r: number }[] = [];
    
    // Central body
    blobs.push({ ox: 0, oy: 0, r: radius * (0.6 + Math.random() * 0.4) });
    
    // Random additional blobs — no two clouds look the same
    for (let i = 1; i < blobCount; i++) {
      const angle = Math.random() * Math.PI * 2;
      const dist = Math.random() * radius * 0.8;
      blobs.push({
        ox: Math.cos(angle) * dist,
        oy: Math.sin(angle) * dist * 0.5 - Math.random() * radius * 0.3, // bias upward
        r: radius * (0.25 + Math.random() * 0.55),
      });
    }
    
    return {
      x: Math.random() * (w + radius * 4) - radius * 2,
      y: h * 0.1 + Math.random() * h * 0.7,
      radius,
      speed: 0.08 + Math.random() * 0.4 + (sizeRoll > 0.7 ? 0 : 0.15), // big ones slower
      opacity: 0.5 + Math.random() * 0.45,
      phase: Math.random() * Math.PI * 2,
      drift: (Math.random() - 0.5) * 0.15, // vertical drift
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
      // Random count: 8-14 clouds
      const count = 8 + Math.floor(Math.random() * 7);
      for (let i = 0; i < count; i++) clouds.push(createCloud(w, h));
    };

    const drawCloud = (cloud: Cloud) => {
      const bobY = Math.sin(t * 0.0004 + cloud.phase) * 8;
      const wobbleX = Math.sin(t * 0.0003 + cloud.phase * 2) * 3;
      const isDark = document.documentElement.classList.contains('dark');

      for (const blob of cloud.blobs) {
        const bx = cloud.x + blob.ox + wobbleX;
        const by = cloud.y + blob.oy + bobY;
        const gradient = ctx.createRadialGradient(bx, by, 0, bx, by, blob.r);
        if (isDark) {
          gradient.addColorStop(0, `rgba(148,163,184,${cloud.opacity * 0.2})`);
          gradient.addColorStop(0.7, `rgba(100,116,139,${cloud.opacity * 0.08})`);
          gradient.addColorStop(1, 'rgba(71,85,105,0)');
        } else {
          gradient.addColorStop(0, `rgba(255,255,255,${cloud.opacity})`);
          gradient.addColorStop(0.5, `rgba(255,255,255,${cloud.opacity * 0.8})`);
          gradient.addColorStop(0.85, `rgba(255,255,255,${cloud.opacity * 0.3})`);
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
        cloud.y += cloud.drift;
        
        // Wrap around
        if (cloud.x > w + cloud.radius * 3) {
          cloud.x = -cloud.radius * 3;
          cloud.y = h * 0.1 + Math.random() * h * 0.7;
        }
        // Keep within vertical bounds
        if (cloud.y < -cloud.radius || cloud.y > h + cloud.radius) {
          cloud.drift *= -1;
        }
        
        // Mouse push
        const dx = cloud.x - mouseRef.current.x;
        const dy = cloud.y - mouseRef.current.y;
        const dist = Math.hypot(dx, dy);
        if (dist < 200 && dist > 0) {
          cloud.x += (dx / dist) * 1.5;
          cloud.y += (dy / dist) * 0.8;
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
