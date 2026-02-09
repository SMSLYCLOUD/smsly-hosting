'use client';

import React, { useRef, useEffect, useCallback } from 'react';

interface Particle {
  x: number;
  y: number;
  radius: number;
  vx: number;
  vy: number;
  opacity: number;
  targetOpacity: number;
  pulseSpeed: number;
  pulsePhase: number;
  color: string;
}

interface CloudBlob {
  x: number;
  y: number;
  radius: number;
  vx: number;
  vy: number;
  opacity: number;
  wobbleSpeed: number;
  wobblePhase: number;
  wobbleAmplitude: number;
}

const COLORS = {
  emerald: [
    'rgba(16, 185, 129,',   // emerald-500
    'rgba(52, 211, 153,',   // emerald-400
    'rgba(110, 231, 183,',  // emerald-300
    'rgba(6, 95, 70,',      // emerald-800
  ],
  accent: [
    'rgba(20, 184, 166,',   // teal-500
    'rgba(45, 212, 191,',   // teal-400
    'rgba(255, 255, 255,',  // white sparkle
  ]
};

export function CloudHeroAnimation() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animationRef = useRef<number>(0);
  const particlesRef = useRef<Particle[]>([]);
  const cloudsRef = useRef<CloudBlob[]>([]);
  const mouseRef = useRef({ x: -1000, y: -1000 });
  const timeRef = useRef(0);

  const initParticles = useCallback((width: number, height: number) => {
    const particles: Particle[] = [];
    const count = Math.min(Math.floor((width * height) / 12000), 120);

    for (let i = 0; i < count; i++) {
      const allColors = [...COLORS.emerald, ...COLORS.accent];
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        radius: Math.random() * 2.5 + 0.5,
        vx: (Math.random() - 0.5) * 0.3,
        vy: (Math.random() - 0.5) * 0.15 - 0.1,
        opacity: Math.random() * 0.5 + 0.1,
        targetOpacity: Math.random() * 0.6 + 0.2,
        pulseSpeed: Math.random() * 0.02 + 0.005,
        pulsePhase: Math.random() * Math.PI * 2,
        color: allColors[Math.floor(Math.random() * allColors.length)],
      });
    }
    return particles;
  }, []);

  const initClouds = useCallback((width: number, height: number) => {
    const clouds: CloudBlob[] = [];
    const count = Math.min(Math.floor(width / 200), 8);

    for (let i = 0; i < count; i++) {
      clouds.push({
        x: Math.random() * width,
        y: Math.random() * height * 0.7 + height * 0.1,
        radius: Math.random() * 120 + 60,
        vx: (Math.random() - 0.5) * 0.2,
        vy: (Math.random() - 0.5) * 0.05,
        opacity: Math.random() * 0.06 + 0.02,
        wobbleSpeed: Math.random() * 0.003 + 0.001,
        wobblePhase: Math.random() * Math.PI * 2,
        wobbleAmplitude: Math.random() * 20 + 10,
      });
    }
    return clouds;
  }, []);

  const drawCloud = useCallback((ctx: CanvasRenderingContext2D, cloud: CloudBlob, time: number) => {
    const wobbleX = Math.sin(time * cloud.wobbleSpeed + cloud.wobblePhase) * cloud.wobbleAmplitude;
    const wobbleY = Math.cos(time * cloud.wobbleSpeed * 0.7 + cloud.wobblePhase) * cloud.wobbleAmplitude * 0.5;
    const cx = cloud.x + wobbleX;
    const cy = cloud.y + wobbleY;

    // Multi-layered cloud gradient
    const gradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, cloud.radius);
    gradient.addColorStop(0, `rgba(16, 185, 129, ${cloud.opacity * 1.5})`);
    gradient.addColorStop(0.4, `rgba(52, 211, 153, ${cloud.opacity})`);
    gradient.addColorStop(0.7, `rgba(110, 231, 183, ${cloud.opacity * 0.5})`);
    gradient.addColorStop(1, `rgba(16, 185, 129, 0)`);

    ctx.beginPath();
    ctx.arc(cx, cy, cloud.radius, 0, Math.PI * 2);
    ctx.fillStyle = gradient;
    ctx.fill();

    // Inner glow
    const innerGradient = ctx.createRadialGradient(cx, cy - cloud.radius * 0.2, 0, cx, cy, cloud.radius * 0.6);
    innerGradient.addColorStop(0, `rgba(255, 255, 255, ${cloud.opacity * 0.8})`);
    innerGradient.addColorStop(1, `rgba(255, 255, 255, 0)`);
    ctx.beginPath();
    ctx.arc(cx, cy - cloud.radius * 0.2, cloud.radius * 0.6, 0, Math.PI * 2);
    ctx.fillStyle = innerGradient;
    ctx.fill();
  }, []);

  const drawConnections = useCallback((ctx: CanvasRenderingContext2D, particles: Particle[], mouse: {x: number, y: number}) => {
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist < 120) {
          const opacity = (1 - dist / 120) * 0.15;
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.strokeStyle = `rgba(16, 185, 129, ${opacity})`;
          ctx.lineWidth = 0.5;
          ctx.stroke();
        }
      }

      // Mouse connections
      const dx = particles[i].x - mouse.x;
      const dy = particles[i].y - mouse.y;
      const dist = Math.sqrt(dx * dx + dy * dy);

      if (dist < 180) {
        const opacity = (1 - dist / 180) * 0.3;
        ctx.beginPath();
        ctx.moveTo(particles[i].x, particles[i].y);
        ctx.lineTo(mouse.x, mouse.y);
        ctx.strokeStyle = `rgba(52, 211, 153, ${opacity})`;
        ctx.lineWidth = 1;
        ctx.stroke();

        // Push particles gently away from mouse
        const force = (180 - dist) / 180 * 0.5;
        particles[i].vx += (dx / dist) * force * 0.1;
        particles[i].vy += (dy / dist) * force * 0.1;
      }
    }
  }, []);

  const animate = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const { width, height } = canvas;
    timeRef.current += 1;

    // Clear with slight trail effect
    ctx.clearRect(0, 0, width, height);

    // Draw clouds
    for (const cloud of cloudsRef.current) {
      drawCloud(ctx, cloud, timeRef.current);
      cloud.x += cloud.vx;
      cloud.y += cloud.vy;

      // Wrap
      if (cloud.x < -cloud.radius) cloud.x = width + cloud.radius;
      if (cloud.x > width + cloud.radius) cloud.x = -cloud.radius;
      if (cloud.y < -cloud.radius) cloud.y = height + cloud.radius;
      if (cloud.y > height + cloud.radius) cloud.y = -cloud.radius;
    }

    // Draw connections
    drawConnections(ctx, particlesRef.current, mouseRef.current);

    // Draw and update particles
    for (const p of particlesRef.current) {
      // Pulse opacity
      p.pulsePhase += p.pulseSpeed;
      p.opacity = p.targetOpacity + Math.sin(p.pulsePhase) * 0.15;

      // Draw particle
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);

      // Glow effect
      const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.radius * 3);
      glow.addColorStop(0, `${p.color} ${p.opacity})`);
      glow.addColorStop(0.5, `${p.color} ${p.opacity * 0.3})`);
      glow.addColorStop(1, `${p.color} 0)`);

      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.radius * 3, 0, Math.PI * 2);
      ctx.fill();

      // Solid core
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
      ctx.fillStyle = `${p.color} ${p.opacity})`;
      ctx.fill();

      // Move
      p.x += p.vx;
      p.y += p.vy;

      // Dampen velocity
      p.vx *= 0.99;
      p.vy *= 0.99;

      // Add slight upward drift (cloud rising effect)
      p.vy -= 0.001;

      // Wrap
      if (p.x < -10) p.x = width + 10;
      if (p.x > width + 10) p.x = -10;
      if (p.y < -10) { p.y = height + 10; p.vy = Math.abs(p.vy) * -0.5; }
      if (p.y > height + 10) p.y = -10;
    }

    // Floating sparkle effect — occasional bright flashes
    if (Math.random() < 0.03) {
      const sx = Math.random() * width;
      const sy = Math.random() * height;
      const sparkleGradient = ctx.createRadialGradient(sx, sy, 0, sx, sy, 8);
      sparkleGradient.addColorStop(0, 'rgba(255, 255, 255, 0.6)');
      sparkleGradient.addColorStop(0.5, 'rgba(110, 231, 183, 0.2)');
      sparkleGradient.addColorStop(1, 'rgba(16, 185, 129, 0)');
      ctx.beginPath();
      ctx.arc(sx, sy, 8, 0, Math.PI * 2);
      ctx.fillStyle = sparkleGradient;
      ctx.fill();
    }

    animationRef.current = requestAnimationFrame(animate);
  }, [drawCloud, drawConnections]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const handleResize = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
      particlesRef.current = initParticles(canvas.width, canvas.height);
      cloudsRef.current = initClouds(canvas.width, canvas.height);
    };

    const handleMouseMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouseRef.current = {
        x: e.clientX - rect.left,
        y: e.clientY - rect.top,
      };
    };

    const handleMouseLeave = () => {
      mouseRef.current = { x: -1000, y: -1000 };
    };

    handleResize();
    window.addEventListener('resize', handleResize);
    canvas.addEventListener('mousemove', handleMouseMove);
    canvas.addEventListener('mouseleave', handleMouseLeave);

    animationRef.current = requestAnimationFrame(animate);

    return () => {
      window.removeEventListener('resize', handleResize);
      canvas.removeEventListener('mousemove', handleMouseMove);
      canvas.removeEventListener('mouseleave', handleMouseLeave);
      cancelAnimationFrame(animationRef.current);
    };
  }, [animate, initParticles, initClouds]);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 -z-10"
      style={{ pointerEvents: 'auto' }}
      aria-hidden="true"
    />
  );
}
