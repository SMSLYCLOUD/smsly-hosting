'use client';

import { useEffect, useRef, useCallback } from 'react';

interface Raindrop {
  x: number;
  y: number;
  speed: number;
  length: number;
  opacity: number;
}

interface Cloud {
  x: number;
  y: number;
  radius: number;
  speed: number;
  opacity: number;
  phase: number;
  drift: number;
  blobs: { ox: number; oy: number; r: number }[];
  lightningTimer: number;
  lightningDuration: number;
  lightningIntensity: number;
  canFlash: boolean;
}

interface Bird {
  x: number;
  y: number;
  speed: number;
  wingPhase: number;
  wingSpeed: number;
  size: number;
  opacity: number;
}

interface Particle {
  x: number;
  y: number;
  speed: number;
  size: number;
  opacity: number;
  drift: number;
  phase: number;
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

    const blobCount = 3 + Math.floor(Math.random() * 4);
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
      y: h * 0.05 + Math.random() * h * 0.5,
      radius,
      speed: 0.08 + Math.random() * 0.4 + (sizeRoll > 0.7 ? 0 : 0.15),
      opacity: 0.55 + Math.random() * 0.4,
      phase: Math.random() * Math.PI * 2,
      drift: (Math.random() - 0.5) * 0.1,
      blobs,
      lightningTimer: 5000 + Math.random() * 15000,
      lightningDuration: 0,
      lightningIntensity: 0,
      canFlash: radius > 120,
    };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let w = 0, h = 0, t = 0;
    const clouds: Cloud[] = [];
    const raindrops: Raindrop[] = [];
    const birds: Bird[] = [];
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
      raindrops.length = 0;
      birds.length = 0;
      particles.length = 0;

      const count = 5 + Math.floor(Math.random() * 3);
      for (let i = 0; i < count; i++) clouds.push(createCloud(w, h));

      // Raindrops (optimized count for 60fps)
      for (let i = 0; i < 25; i++) {
        raindrops.push({
          x: Math.random() * w,
          y: Math.random() * h,
          speed: 3 + Math.random() * 5,
          length: 8 + Math.random() * 15,
          opacity: 0.1 + Math.random() * 0.25,
        });
      }

      // Birds
      for (let i = 0; i < 5; i++) {
        birds.push({
          x: Math.random() * w,
          y: h * 0.1 + Math.random() * h * 0.3,
          speed: 0.5 + Math.random() * 1.2,
          wingPhase: Math.random() * Math.PI * 2,
          wingSpeed: 0.04 + Math.random() * 0.03,
          size: 3 + Math.random() * 4,
          opacity: 0.3 + Math.random() * 0.4,
        });
      }

      // Floating light particles (optimized count for 60fps)
      for (let i = 0; i < 15; i++) {
        particles.push({
          x: Math.random() * w,
          y: Math.random() * h,
          speed: 0.15 + Math.random() * 0.4,
          size: 1 + Math.random() * 2.5,
          opacity: 0.15 + Math.random() * 0.3,
          drift: (Math.random() - 0.5) * 0.3,
          phase: Math.random() * Math.PI * 2,
        });
      }
    };

    const drawCloud = (cloud: Cloud) => {
      const bobY = Math.sin(t * 0.0004 + cloud.phase) * 8;
      const wobbleX = Math.sin(t * 0.0003 + cloud.phase * 2) * 3;
      const isDark = document.documentElement.classList.contains('dark');
      const flashBoost = cloud.lightningIntensity;

      // Cloud shadow on ground (light mode only)
      if (!isDark && cloud.radius > 60) {
        const shadowX = cloud.x + cloud.radius * 0.3;
        const shadowY = h * 0.92;
        const shadowW = cloud.radius * 1.8;
        const shadowH = cloud.radius * 0.15;
        const shadowGrad = ctx.createRadialGradient(shadowX, shadowY, 0, shadowX, shadowY, shadowW);
        shadowGrad.addColorStop(0, `rgba(0,0,0,${0.03 * cloud.opacity})`);
        shadowGrad.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.beginPath();
        ctx.ellipse(shadowX, shadowY, shadowW, shadowH, 0, 0, Math.PI * 2);
        ctx.fillStyle = shadowGrad;
        ctx.fill();
      }

      for (const blob of cloud.blobs) {
        const bx = cloud.x + blob.ox + wobbleX;
        const by = cloud.y + blob.oy + bobY;
        const gradient = ctx.createRadialGradient(bx, by, 0, bx, by, blob.r);
        if (isDark) {
          const li = flashBoost * 0.6;
          gradient.addColorStop(0, `rgba(${148 + li * 100},${163 + li * 80},${184 + li * 70},${cloud.opacity * 0.25 + li * 0.4})`);
          gradient.addColorStop(0.7, `rgba(100,116,139,${cloud.opacity * 0.1})`);
          gradient.addColorStop(1, 'rgba(71,85,105,0)');
        } else {
          const baseOpacity = cloud.opacity + flashBoost * 0.3;
          const warmth = flashBoost * 30;
          gradient.addColorStop(0, `rgba(255,${255 - warmth * 0.2},${255 - warmth},${Math.min(1, baseOpacity)})`);
          gradient.addColorStop(0.5, `rgba(255,255,${255 - warmth * 0.3},${baseOpacity * 0.85})`);
          gradient.addColorStop(0.85, `rgba(255,255,255,${baseOpacity * 0.35})`);
          gradient.addColorStop(1, 'rgba(255,255,255,0)');
        }
        ctx.beginPath();
        ctx.arc(bx, by, blob.r, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();
      }

      // Lightning inner glow
      if (flashBoost > 0.3) {
        const lx = cloud.x + (Math.random() - 0.5) * cloud.radius * 0.4;
        const ly = cloud.y + bobY + (Math.random() - 0.5) * cloud.radius * 0.2;
        const lr = cloud.radius * 0.35 * flashBoost;
        const lg = ctx.createRadialGradient(lx, ly, 0, lx, ly, lr);
        if (isDark) {
          lg.addColorStop(0, `rgba(180,200,255,${flashBoost * 0.5})`);
          lg.addColorStop(0.5, `rgba(140,170,255,${flashBoost * 0.2})`);
        } else {
          lg.addColorStop(0, `rgba(255,255,220,${flashBoost * 0.7})`);
          lg.addColorStop(0.5, `rgba(255,255,180,${flashBoost * 0.3})`);
        }
        lg.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.beginPath();
        ctx.arc(lx, ly, lr, 0, Math.PI * 2);
        ctx.fillStyle = lg;
        ctx.fill();

        // Lightning bolt streak
        if (flashBoost > 0.6 && Math.random() > 0.5) {
          ctx.save();
          ctx.strokeStyle = isDark
            ? `rgba(180,200,255,${flashBoost * 0.6})`
            : `rgba(255,255,200,${flashBoost * 0.8})`;
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          let bx = cloud.x + (Math.random() - 0.5) * cloud.radius * 0.3;
          let by = cloud.y + cloud.radius * 0.3;
          ctx.moveTo(bx, by);
          for (let seg = 0; seg < 4; seg++) {
            bx += (Math.random() - 0.5) * 20;
            by += 10 + Math.random() * 15;
            ctx.lineTo(bx, by);
          }
          ctx.stroke();
          ctx.restore();
        }
      }
    };

    // Sun glow from top-right with enhanced rays and lens flare
    const drawSun = () => {
      const isDark = document.documentElement.classList.contains('dark');
      if (isDark) return;

      const sx = w * 0.88;
      const sy = 20;
      const pulse = 1 + Math.sin(t * 0.001) * 0.08;

      // Outer warm atmosphere glow
      const atmo = ctx.createRadialGradient(sx, sy, 0, sx, sy, 250 * pulse);
      atmo.addColorStop(0, 'rgba(255,245,180,0.12)');
      atmo.addColorStop(0.3, 'rgba(255,240,160,0.06)');
      atmo.addColorStop(0.7, 'rgba(255,230,140,0.02)');
      atmo.addColorStop(1, 'rgba(255,220,100,0)');
      ctx.beginPath();
      ctx.arc(sx, sy, 250 * pulse, 0, Math.PI * 2);
      ctx.fillStyle = atmo;
      ctx.fill();

      // Sun halo
      const halo = ctx.createRadialGradient(sx, sy, 0, sx, sy, 120 * pulse);
      halo.addColorStop(0, 'rgba(255,250,200,0.3)');
      halo.addColorStop(0.4, 'rgba(255,245,180,0.1)');
      halo.addColorStop(1, 'rgba(255,240,150,0)');
      ctx.beginPath();
      ctx.arc(sx, sy, 120 * pulse, 0, Math.PI * 2);
      ctx.fillStyle = halo;
      ctx.fill();

      // Sun core (small bright center)
      const core = ctx.createRadialGradient(sx, sy, 0, sx, sy, 30 * pulse);
      core.addColorStop(0, 'rgba(255,255,240,0.5)');
      core.addColorStop(0.5, 'rgba(255,250,210,0.2)');
      core.addColorStop(1, 'rgba(255,245,180,0)');
      ctx.beginPath();
      ctx.arc(sx, sy, 30 * pulse, 0, Math.PI * 2);
      ctx.fillStyle = core;
      ctx.fill();

      // Sun rays
      ctx.save();
      const rayCount = 8;
      for (let i = 0; i < rayCount; i++) {
        const angle = (Math.PI * 0.15) + (i / rayCount) * Math.PI * 0.5;
        const shimmer = Math.sin(t * 0.0005 + i * 1.2) * 0.02;
        const len = h * 0.6 + Math.sin(t * 0.0003 + i) * 40;
        const gradient = ctx.createLinearGradient(sx, sy, sx + Math.cos(angle) * len, sy + Math.sin(angle) * len);
        gradient.addColorStop(0, `rgba(255,250,200,${0.05 + shimmer})`);
        gradient.addColorStop(0.5, `rgba(255,248,190,${0.02 + shimmer * 0.5})`);
        gradient.addColorStop(1, 'rgba(255,250,200,0)');
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.lineTo(sx + Math.cos(angle - 0.02) * len, sy + Math.sin(angle - 0.02) * len);
        ctx.lineTo(sx + Math.cos(angle + 0.02) * len + 18, sy + Math.sin(angle + 0.02) * len + 18);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();
      }
      ctx.restore();

      // Lens flare dots
      const flareCount = 3;
      for (let i = 0; i < flareCount; i++) {
        const fDist = 80 + i * 60;
        const fAngle = Math.PI * 0.55;
        const fx = sx + Math.cos(fAngle) * fDist;
        const fy = sy + Math.sin(fAngle) * fDist;
        const fSize = (8 - i * 2) * pulse;
        const fOpacity = (0.06 - i * 0.015) * (1 + Math.sin(t * 0.002) * 0.3);
        const flare = ctx.createRadialGradient(fx, fy, 0, fx, fy, fSize);
        flare.addColorStop(0, `rgba(255,255,255,${fOpacity})`);
        flare.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.beginPath();
        ctx.arc(fx, fy, fSize, 0, Math.PI * 2);
        ctx.fillStyle = flare;
        ctx.fill();
      }
    };

    // Rain
    const drawRain = () => {
      const isDark = document.documentElement.classList.contains('dark');
      ctx.save();
      for (const drop of raindrops) {
        ctx.beginPath();
        ctx.moveTo(drop.x, drop.y);
        ctx.lineTo(drop.x - 0.5, drop.y + drop.length);
        ctx.strokeStyle = isDark
          ? `rgba(150,180,220,${drop.opacity * 0.6})`
          : `rgba(120,160,220,${drop.opacity})`;
        ctx.lineWidth = 0.8;
        ctx.stroke();

        drop.y += drop.speed;
        drop.x -= 0.3;
        if (drop.y > h) {
          drop.y = -drop.length;
          drop.x = Math.random() * w;
        }
        if (drop.x < 0) drop.x = w;
      }
      ctx.restore();
    };

    // Birds flying in V-like patterns
    const drawBirds = () => {
      const isDark = document.documentElement.classList.contains('dark');
      ctx.save();
      for (const bird of birds) {
        const wingY = Math.sin(bird.wingPhase) * bird.size * 0.7;
        const bodyBob = Math.sin(bird.wingPhase * 0.5) * 1.5;

        ctx.strokeStyle = isDark
          ? `rgba(180,200,220,${bird.opacity * 0.5})`
          : `rgba(60,80,100,${bird.opacity})`;
        ctx.lineWidth = 1.2;
        ctx.lineCap = 'round';

        // Left wing
        ctx.beginPath();
        ctx.moveTo(bird.x, bird.y + bodyBob);
        ctx.quadraticCurveTo(
          bird.x - bird.size * 0.6, bird.y + bodyBob - wingY * 0.5,
          bird.x - bird.size, bird.y + bodyBob - wingY
        );
        ctx.stroke();

        // Right wing
        ctx.beginPath();
        ctx.moveTo(bird.x, bird.y + bodyBob);
        ctx.quadraticCurveTo(
          bird.x + bird.size * 0.6, bird.y + bodyBob - wingY * 0.5,
          bird.x + bird.size, bird.y + bodyBob - wingY
        );
        ctx.stroke();

        // Animate
        bird.x += bird.speed;
        bird.wingPhase += bird.wingSpeed;
        bird.y += Math.sin(t * 0.0002 + bird.wingPhase) * 0.15;

        if (bird.x > w + 30) {
          bird.x = -30;
          bird.y = h * 0.1 + Math.random() * h * 0.3;
        }
      }
      ctx.restore();
    };

    // Floating light particles (dust motes / pollen in sunlight)
    const drawParticles = () => {
      const isDark = document.documentElement.classList.contains('dark');
      ctx.save();
      for (const p of particles) {
        const twinkle = 0.5 + Math.sin(t * 0.003 + p.phase) * 0.5;
        const alpha = p.opacity * twinkle;

        if (isDark) {
          ctx.fillStyle = `rgba(180,200,240,${alpha * 0.4})`;
        } else {
          ctx.fillStyle = `rgba(255,255,230,${alpha})`;
          // Glow around particle
          const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size * 3);
          glow.addColorStop(0, `rgba(255,250,200,${alpha * 0.3})`);
          glow.addColorStop(1, 'rgba(255,250,200,0)');
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size * 3, 0, Math.PI * 2);
          ctx.fillStyle = glow;
          ctx.fill();
          ctx.fillStyle = `rgba(255,255,230,${alpha})`;
        }

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fill();

        // Float movement
        p.x += p.drift + Math.sin(t * 0.001 + p.phase) * 0.15;
        p.y -= p.speed;
        if (p.y < -10) {
          p.y = h + 10;
          p.x = Math.random() * w;
        }
        if (p.x < -10) p.x = w + 10;
        if (p.x > w + 10) p.x = -10;
      }
      ctx.restore();
    };

    // Horizon haze line (atmospheric depth)
    const drawHorizonHaze = () => {
      const isDark = document.documentElement.classList.contains('dark');
      if (isDark) return;

      const hazeY = h * 0.85;
      const haze = ctx.createLinearGradient(0, hazeY - 40, 0, hazeY + 40);
      haze.addColorStop(0, 'rgba(200,230,255,0)');
      haze.addColorStop(0.5, 'rgba(200,230,255,0.08)');
      haze.addColorStop(1, 'rgba(200,230,255,0)');
      ctx.fillStyle = haze;
      ctx.fillRect(0, hazeY - 40, w, 80);
    };

    const animate = () => {
      t += 16;
      ctx.clearRect(0, 0, w, h);

      // Sun behind everything
      drawSun();

      // Horizon atmospheric haze
      drawHorizonHaze();

      // Rain behind clouds
      drawRain();

      // Floating dust/pollen particles
      drawParticles();

      for (const cloud of clouds) {
        cloud.x += cloud.speed;
        cloud.y += cloud.drift;
        if (cloud.x > w + cloud.radius * 3) {
          cloud.x = -cloud.radius * 3;
          cloud.y = h * 0.05 + Math.random() * h * 0.5;
        }
        if (cloud.y < -cloud.radius * 0.5 || cloud.y > h * 0.6 + cloud.radius) {
          cloud.drift *= -1;
        }

        // Lightning
        if (cloud.canFlash) {
          cloud.lightningTimer -= 16;
          if (cloud.lightningTimer <= 0) {
            cloud.lightningDuration = 60 + Math.random() * 150;
            cloud.lightningIntensity = 0.5 + Math.random() * 0.5;
            cloud.lightningTimer = 6000 + Math.random() * 18000;
          }
          if (cloud.lightningDuration > 0) {
            cloud.lightningDuration -= 16;
            cloud.lightningIntensity *= (Math.random() > 0.3 ? 0.82 : 1.15);
            cloud.lightningIntensity = Math.min(1, cloud.lightningIntensity);
            if (cloud.lightningDuration <= 0) cloud.lightningIntensity = 0;
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

      // Birds on top of clouds
      drawBirds();

      animRef.current = requestAnimationFrame(animate);
    };

    const handleMouse = (e: MouseEvent) => {
      mouseRef.current = { x: e.offsetX, y: e.offsetY };
    };
    const handleLeave = () => { mouseRef.current = { x: -1000, y: -1000 }; };

    init();
    animate();
    canvas.addEventListener('mousemove', handleMouse, { passive: true });
    canvas.addEventListener('mouseleave', handleLeave, { passive: true });
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
      style={{ pointerEvents: 'none' }}
    />
  );
}
