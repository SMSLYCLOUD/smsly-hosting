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
  // Lightning
  lightningTimer: number;
  lightningDuration: number;
  lightningIntensity: number;
  canFlash: boolean;
}

export function CloudHeroAnimation() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const mouseRef = useRef({ x: -1000, y: -1000 });

  const createCloud = useCallback((w: number, h: number): Cloud => {
    const sizeRoll = Math.random();
    const radius = sizeRoll > 0.7
      ? (140 + Math.random() * 160)
      : sizeRoll > 0.3
        ? (70 + Math.random() * 80)
        : (30 + Math.random() * 50);

    const blobCount = 3 + Math.floor(Math.random() * 10);
    const blobs: { ox: number; oy: number; r: number }[] = [];
    blobs.push({ ox: 0, oy: 0, r: radius * (0.6 + Math.random() * 0.4) });
    for (let i = 1; i < blobCount; i++) {
      const angle = Math.random() * Math.PI * 2;
      const dist = Math.random() * radius * 0.8;
      blobs.push({
        ox: Math.cos(angle) * dist,
        oy: Math.sin(angle) * dist * 0.5 - Math.random() * radius * 0.3,
        r: radius * (0.25 + Math.random() * 0.55),
      });
    }
    return {
      x: Math.random() * (w + radius * 4) - radius * 2,
      y: h * 0.1 + Math.random() * h * 0.7,
      radius,
      speed: 0.08 + Math.random() * 0.4 + (sizeRoll > 0.7 ? 0 : 0.15),
      opacity: 0.5 + Math.random() * 0.45,
      phase: Math.random() * Math.PI * 2,
      drift: (Math.random() - 0.5) * 0.15,
      blobs,
      lightningTimer: 3000 + Math.random() * 8000,
      lightningDuration: 0,
      lightningIntensity: 0,
      canFlash: radius > 100, // Only big clouds flash
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
      const count = 8 + Math.floor(Math.random() * 7);
      for (let i = 0; i < count; i++) clouds.push(createCloud(w, h));
    };

    const drawCloud = (cloud: Cloud) => {
      const bobY = Math.sin(t * 0.0004 + cloud.phase) * 8;
      const wobbleX = Math.sin(t * 0.0003 + cloud.phase * 2) * 3;
      const isDark = document.documentElement.classList.contains('dark');
      const flashBoost = cloud.lightningIntensity;

      for (const blob of cloud.blobs) {
        const bx = cloud.x + blob.ox + wobbleX;
        const by = cloud.y + blob.oy + bobY;
        const gradient = ctx.createRadialGradient(bx, by, 0, bx, by, blob.r);
        
        if (isDark) {
          const li = flashBoost * 0.6;
          gradient.addColorStop(0, `rgba(${148 + li * 100},${163 + li * 80},${184 + li * 70},${cloud.opacity * 0.2 + li * 0.4})`);
          gradient.addColorStop(0.7, `rgba(100,116,139,${cloud.opacity * 0.08})`);
          gradient.addColorStop(1, 'rgba(71,85,105,0)');
        } else {
          // White cloud with lightning glow
          const baseOpacity = cloud.opacity + flashBoost * 0.3;
          const warmth = flashBoost * 40; // Slight golden tint during flash
          gradient.addColorStop(0, `rgba(${255},${255 - warmth * 0.3},${255 - warmth},${Math.min(1, baseOpacity)})`);
          gradient.addColorStop(0.5, `rgba(255,255,${255 - warmth * 0.5},${baseOpacity * 0.8})`);
          gradient.addColorStop(0.85, `rgba(255,255,255,${baseOpacity * 0.3})`);
          gradient.addColorStop(1, 'rgba(255,255,255,0)');
        }
        ctx.beginPath();
        ctx.arc(bx, by, blob.r, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();
      }

      // Lightning bolt flash — a bright inner glow
      if (flashBoost > 0.3) {
        const lx = cloud.x + (Math.random() - 0.5) * cloud.radius * 0.5;
        const ly = cloud.y + bobY + (Math.random() - 0.5) * cloud.radius * 0.3;
        const lr = cloud.radius * 0.3 * flashBoost;
        const lg = ctx.createRadialGradient(lx, ly, 0, lx, ly, lr);
        if (isDark) {
          lg.addColorStop(0, `rgba(200,220,255,${flashBoost * 0.5})`);
          lg.addColorStop(0.5, `rgba(150,180,255,${flashBoost * 0.2})`);
        } else {
          lg.addColorStop(0, `rgba(255,255,230,${flashBoost * 0.6})`);
          lg.addColorStop(0.5, `rgba(255,255,200,${flashBoost * 0.25})`);
        }
        lg.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.beginPath();
        ctx.arc(lx, ly, lr, 0, Math.PI * 2);
        ctx.fillStyle = lg;
        ctx.fill();
      }
    };

    // Sun rays from top-right corner
    const drawSunRays = () => {
      const isDark = document.documentElement.classList.contains('dark');
      if (isDark) return;
      
      const sx = w * 0.85;
      const sy = -30;
      const rayCount = 5;
      
      ctx.save();
      for (let i = 0; i < rayCount; i++) {
        const angle = (Math.PI * 0.15) + (i / rayCount) * Math.PI * 0.35;
        const len = h * 0.7 + Math.sin(t * 0.0003 + i) * 40;
        const rayWidth = 25 + Math.sin(t * 0.0005 + i * 1.5) * 10;
        
        const gradient = ctx.createLinearGradient(sx, sy, sx + Math.cos(angle) * len, sy + Math.sin(angle) * len);
        gradient.addColorStop(0, `rgba(255,255,220,${0.06 + Math.sin(t * 0.0004 + i) * 0.02})`);
        gradient.addColorStop(1, 'rgba(255,255,220,0)');
        
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.lineTo(
          sx + Math.cos(angle - 0.02) * len,
          sy + Math.sin(angle - 0.02) * len
        );
        ctx.lineTo(
          sx + Math.cos(angle + 0.02) * len + rayWidth,
          sy + Math.sin(angle + 0.02) * len + rayWidth
        );
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();
      }
      ctx.restore();
    };

    const animate = () => {
      t += 16;
      ctx.clearRect(0, 0, w, h);

      // Sun rays behind clouds
      drawSunRays();

      for (const cloud of clouds) {
        cloud.x += cloud.speed;
        cloud.y += cloud.drift;

        if (cloud.x > w + cloud.radius * 3) {
          cloud.x = -cloud.radius * 3;
          cloud.y = h * 0.1 + Math.random() * h * 0.7;
        }
        if (cloud.y < -cloud.radius || cloud.y > h + cloud.radius) {
          cloud.drift *= -1;
        }

        // Lightning timing
        if (cloud.canFlash) {
          cloud.lightningTimer -= 16;
          if (cloud.lightningTimer <= 0) {
            cloud.lightningDuration = 80 + Math.random() * 120; // Flash lasts 80-200ms
            cloud.lightningIntensity = 0.6 + Math.random() * 0.4;
            cloud.lightningTimer = 4000 + Math.random() * 12000; // Next flash in 4-16s
          }
          if (cloud.lightningDuration > 0) {
            cloud.lightningDuration -= 16;
            // Rapid flicker effect
            cloud.lightningIntensity *= (Math.random() > 0.3 ? 0.85 : 1.1);
            cloud.lightningIntensity = Math.min(1, cloud.lightningIntensity);
            if (cloud.lightningDuration <= 0) {
              cloud.lightningIntensity = 0;
            }
          }
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
