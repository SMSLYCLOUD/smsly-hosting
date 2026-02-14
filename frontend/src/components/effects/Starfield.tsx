'use client';

import { useEffect, useRef } from 'react';

// ── Types ───────────────────────────────────────────────────────────────────
interface Star { x: number; y: number; r: number; o: number; ts: number; tp: number; }

interface Asteroid {
  x: number; y: number; size: number; vx: number; vy: number;
  rot: number; rs: number; opacity: number; shape: number[];
}

interface Satellite {
  x: number; y: number; vx: number; vy: number;
  angle: number; rotSpeed: number; blink: number; size: number;
}

interface Meteor {
  x: number; y: number; len: number; speed: number;
  angle: number; o: number; life: number; max: number;
}

interface AuroraBand {
  y: number; amplitude: number; wavelength: number; speed: number;
  color: [number, number, number]; opacity: number; thickness: number; phase: number;
}

interface Comet {
  x: number; y: number; vx: number; vy: number;
  tailLen: number; size: number; o: number; color: [number, number, number];
}

// ── Helpers ─────────────────────────────────────────────────────────────────
const rand = (min: number, max: number) => Math.random() * (max - min) + min;

function makeAsteroid(w: number, h: number): Asteroid {
  const fromLeft = Math.random() > 0.5;
  const vertices = 5 + Math.floor(rand(0, 5));
  const shape: number[] = [];
  for (let i = 0; i < vertices; i++) shape.push(rand(0.5, 1));
  return {
    x: fromLeft ? rand(-60, -20) : rand(w + 20, w + 60),
    y: rand(40, h - 40),
    size: rand(2, 8),
    vx: fromLeft ? rand(0.1, 0.5) : rand(-0.5, -0.1),
    vy: rand(-0.15, 0.15),
    rot: rand(0, Math.PI * 2),
    rs: rand(-0.015, 0.015),
    opacity: rand(0.12, 0.35),
    shape,
  };
}

function makeSatellite(w: number, h: number): Satellite {
  const fromLeft = Math.random() > 0.3;
  return {
    x: fromLeft ? -25 : w + 25,
    y: rand(40, h * 0.6),
    vx: fromLeft ? rand(0.3, 0.8) : rand(-0.8, -0.3),
    vy: rand(-0.05, 0.05),
    angle: rand(0, Math.PI * 2),
    rotSpeed: rand(0.003, 0.012),
    blink: rand(0, Math.PI * 2),
    size: rand(3, 6),
  };
}

function makeMeteor(w: number, h: number): Meteor {
  return {
    x: rand(0, w * 0.9), y: rand(0, h * 0.35),
    len: rand(50, 140), speed: rand(8, 16),
    angle: rand(Math.PI / 7, Math.PI / 4),
    o: rand(0.5, 1), life: 0, max: rand(30, 55),
  };
}

function makeComet(w: number, h: number): Comet {
  return {
    x: -30, y: rand(30, h * 0.4),
    vx: rand(0.6, 1.2), vy: rand(0.05, 0.2),
    tailLen: rand(80, 160), size: rand(2.5, 4),
    o: rand(0.3, 0.6),
    color: Math.random() > 0.5 ? [100, 200, 255] : [180, 255, 180],
  };
}

// ── Component ───────────────────────────────────────────────────────────────
export function Starfield() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const ctx = c.getContext('2d');
    if (!ctx) return;

    let raf: number;
    let stars: Star[] = [];
    let asteroids: Asteroid[] = [];
    let satellites: Satellite[] = [];
    let meteors: Meteor[] = [];
    let comets: Comet[] = [];
    let auroras: AuroraBand[] = [];
    let lastMeteor = 0;
    let lastComet = 0;

    const resize = () => {
      c.width = window.innerWidth;
      c.height = window.innerHeight;
      init();
    };

    const init = () => {
      const W = c.width, H = c.height;

      // Stars
      const n = Math.floor((W * H) / 2200);
      stars = [];
      for (let i = 0; i < n; i++) {
        stars.push({
          x: rand(0, W), y: rand(0, H),
          r: rand(0.3, 2), o: rand(0.3, 0.8),
          ts: rand(0.004, 0.025), tp: rand(0, Math.PI * 2),
        });
      }

      // Seed asteroids (8-12)
      asteroids = [];
      for (let i = 0; i < 10; i++) {
        const a = makeAsteroid(W, H);
        a.x = rand(50, W - 50); // spread across screen initially
        asteroids.push(a);
      }

      // Seed satellites (3-4)
      satellites = [];
      for (let i = 0; i < 4; i++) {
        const s = makeSatellite(W, H);
        s.x = rand(50, W - 50);
        satellites.push(s);
      }

      // Aurora bands (4-6 layered)
      auroras = [
        { y: H * 0.12, amplitude: 25, wavelength: 0.004, speed: 0.0004, color: [0, 255, 120], opacity: 0.06, thickness: 50, phase: 0 },
        { y: H * 0.18, amplitude: 30, wavelength: 0.003, speed: 0.0003, color: [0, 200, 255], opacity: 0.04, thickness: 40, phase: 1 },
        { y: H * 0.25, amplitude: 20, wavelength: 0.005, speed: 0.00035, color: [120, 0, 255], opacity: 0.035, thickness: 35, phase: 2 },
        { y: H * 0.14, amplitude: 35, wavelength: 0.0025, speed: 0.00045, color: [0, 255, 200], opacity: 0.045, thickness: 45, phase: 3 },
        { y: H * 0.22, amplitude: 18, wavelength: 0.006, speed: 0.0005, color: [255, 80, 200], opacity: 0.025, thickness: 30, phase: 4 },
      ];
    };

    // ── Drawing functions ──────────────────────────────────────────────────

    const drawAurora = (a: AuroraBand, t: number) => {
      const W = c.width;
      const shift = t * a.speed + a.phase;
      ctx.beginPath();
      ctx.moveTo(0, a.y);

      // Draw flowing curtain
      for (let x = 0; x <= W; x += 4) {
        const wave1 = Math.sin(x * a.wavelength + shift) * a.amplitude;
        const wave2 = Math.sin(x * a.wavelength * 1.7 + shift * 1.3) * a.amplitude * 0.5;
        const wave3 = Math.sin(x * a.wavelength * 0.5 + shift * 0.7) * a.amplitude * 0.3;
        const y = a.y + wave1 + wave2 + wave3;
        ctx.lineTo(x, y);
      }

      // Close bottom
      for (let x = W; x >= 0; x -= 4) {
        const wave1 = Math.sin(x * a.wavelength + shift) * a.amplitude;
        const wave2 = Math.sin(x * a.wavelength * 1.7 + shift * 1.3) * a.amplitude * 0.5;
        const wave3 = Math.sin(x * a.wavelength * 0.5 + shift * 0.7) * a.amplitude * 0.3;
        const y = a.y + wave1 + wave2 + wave3 + a.thickness;
        ctx.lineTo(x, y);
      }
      ctx.closePath();

      // Vertical gradient for the curtain look
      const grad = ctx.createLinearGradient(0, a.y - a.amplitude, 0, a.y + a.thickness + a.amplitude);
      const [r, g, b] = a.color;
      const pulse = 0.7 + 0.3 * Math.sin(t * 0.0008 + a.phase);
      const op = a.opacity * pulse;
      grad.addColorStop(0, `rgba(${r}, ${g}, ${b}, 0)`);
      grad.addColorStop(0.3, `rgba(${r}, ${g}, ${b}, ${op})`);
      grad.addColorStop(0.5, `rgba(${r}, ${g}, ${b}, ${op * 1.3})`);
      grad.addColorStop(0.7, `rgba(${r}, ${g}, ${b}, ${op * 0.8})`);
      grad.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
      ctx.fillStyle = grad;
      ctx.fill();
    };

    const drawAsteroid = (a: Asteroid) => {
      ctx.save();
      ctx.translate(a.x, a.y);
      ctx.rotate(a.rot);
      const v = a.shape.length;
      ctx.beginPath();
      for (let i = 0; i < v; i++) {
        const ang = (i / v) * Math.PI * 2;
        const r = a.size * a.shape[i];
        const px = Math.cos(ang) * r, py = Math.sin(ang) * r;
        i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
      }
      ctx.closePath();
      // Rocky gradient
      const g = ctx.createRadialGradient(-a.size * 0.2, -a.size * 0.2, 0, 0, 0, a.size);
      g.addColorStop(0, `rgba(160, 150, 140, ${a.opacity * 1.3})`);
      g.addColorStop(1, `rgba(90, 80, 70, ${a.opacity})`);
      ctx.fillStyle = g;
      ctx.fill();
      // Crater
      ctx.beginPath();
      ctx.arc(a.size * 0.15, -a.size * 0.1, a.size * 0.2, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(60, 55, 50, ${a.opacity * 0.6})`;
      ctx.fill();
      ctx.restore();
    };

    const drawSatellite = (s: Satellite, t: number) => {
      const blink = Math.sin(t * 0.004 + s.blink);
      const panelGlow = blink > 0.5;
      const sz = s.size;

      ctx.save();
      ctx.translate(s.x, s.y);
      ctx.rotate(s.angle);

      // Solar panel left
      ctx.fillStyle = `rgba(60, 100, 180, ${panelGlow ? 0.45 : 0.2})`;
      ctx.fillRect(-sz * 2.5, -sz * 0.4, sz * 1.8, sz * 0.8);
      // Panel lines
      ctx.strokeStyle = `rgba(100, 140, 220, ${panelGlow ? 0.4 : 0.15})`;
      ctx.lineWidth = 0.5;
      for (let i = 1; i < 4; i++) {
        const lx = -sz * 2.5 + (sz * 1.8 / 4) * i;
        ctx.beginPath(); ctx.moveTo(lx, -sz * 0.4); ctx.lineTo(lx, sz * 0.4); ctx.stroke();
      }

      // Body
      ctx.fillStyle = 'rgba(200, 200, 210, 0.35)';
      ctx.fillRect(-sz * 0.5, -sz * 0.5, sz, sz);
      // Antenna
      ctx.strokeStyle = 'rgba(180, 180, 190, 0.3)';
      ctx.lineWidth = 0.8;
      ctx.beginPath(); ctx.moveTo(0, -sz * 0.5); ctx.lineTo(0, -sz * 1.2); ctx.stroke();
      ctx.beginPath(); ctx.arc(0, -sz * 1.2, 1, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(200, 200, 200, 0.4)'; ctx.fill();

      // Solar panel right
      ctx.fillStyle = `rgba(60, 100, 180, ${panelGlow ? 0.45 : 0.2})`;
      ctx.fillRect(sz * 0.7, -sz * 0.4, sz * 1.8, sz * 0.8);
      ctx.strokeStyle = `rgba(100, 140, 220, ${panelGlow ? 0.4 : 0.15})`;
      for (let i = 1; i < 4; i++) {
        const lx = sz * 0.7 + (sz * 1.8 / 4) * i;
        ctx.beginPath(); ctx.moveTo(lx, -sz * 0.4); ctx.lineTo(lx, sz * 0.4); ctx.stroke();
      }

      // Blinking red light
      if (panelGlow) {
        ctx.beginPath();
        ctx.arc(sz * 0.2, -sz * 0.5, 1.2, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(255, 50, 50, 0.8)';
        ctx.fill();
        // Red glow
        ctx.beginPath();
        ctx.arc(sz * 0.2, -sz * 0.5, 4, 0, Math.PI * 2);
        const rg = ctx.createRadialGradient(sz * 0.2, -sz * 0.5, 0, sz * 0.2, -sz * 0.5, 4);
        rg.addColorStop(0, 'rgba(255, 50, 50, 0.3)');
        rg.addColorStop(1, 'rgba(255, 50, 50, 0)');
        ctx.fillStyle = rg;
        ctx.fill();
      }

      ctx.restore();
    };

    const drawMeteor = (m: Meteor) => {
      const p = m.life / m.max;
      const fadeIn = Math.min(p * 5, 1);
      const fadeOut = Math.max(1 - (p - 0.5) * 2, 0);
      const alpha = m.o * fadeIn * fadeOut;
      const ex = m.x + Math.cos(m.angle) * m.len;
      const ey = m.y + Math.sin(m.angle) * m.len;

      const grad = ctx.createLinearGradient(m.x, m.y, ex, ey);
      grad.addColorStop(0, `rgba(255, 255, 255, 0)`);
      grad.addColorStop(0.4, `rgba(180, 210, 255, ${alpha * 0.4})`);
      grad.addColorStop(1, `rgba(255, 255, 255, ${alpha})`);

      ctx.beginPath();
      ctx.moveTo(m.x, m.y);
      ctx.lineTo(ex, ey);
      ctx.strokeStyle = grad;
      ctx.lineWidth = 1.8;
      ctx.stroke();

      // Bright head
      ctx.beginPath();
      ctx.arc(ex, ey, 2.5, 0, Math.PI * 2);
      const hg = ctx.createRadialGradient(ex, ey, 0, ex, ey, 3);
      hg.addColorStop(0, `rgba(255, 255, 255, ${alpha})`);
      hg.addColorStop(1, `rgba(200, 220, 255, 0)`);
      ctx.fillStyle = hg;
      ctx.fill();
    };

    const drawComet = (co: Comet) => {
      const [r, g, b] = co.color;
      // Tail
      const tailX = co.x - co.tailLen;
      const grad = ctx.createLinearGradient(tailX, co.y, co.x, co.y);
      grad.addColorStop(0, `rgba(${r}, ${g}, ${b}, 0)`);
      grad.addColorStop(0.7, `rgba(${r}, ${g}, ${b}, ${co.o * 0.2})`);
      grad.addColorStop(1, `rgba(${r}, ${g}, ${b}, ${co.o * 0.6})`);
      ctx.beginPath();
      ctx.moveTo(tailX, co.y);
      ctx.quadraticCurveTo(co.x - co.tailLen * 0.3, co.y - 3, co.x, co.y);
      ctx.quadraticCurveTo(co.x - co.tailLen * 0.3, co.y + 3, tailX, co.y);
      ctx.fillStyle = grad;
      ctx.fill();
      // Head
      ctx.beginPath();
      ctx.arc(co.x, co.y, co.size, 0, Math.PI * 2);
      const hg = ctx.createRadialGradient(co.x, co.y, 0, co.x, co.y, co.size * 3);
      hg.addColorStop(0, `rgba(255, 255, 255, ${co.o})`);
      hg.addColorStop(0.5, `rgba(${r}, ${g}, ${b}, ${co.o * 0.5})`);
      hg.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
      ctx.fillStyle = hg;
      ctx.fill();
    };

    // ── Main loop ─────────────────────────────────────────────────────────
    const draw = (t: number) => {
      const W = c.width, H = c.height;
      ctx.clearRect(0, 0, W, H);

      // 1. Aurora (behind everything)
      for (const a of auroras) drawAurora(a, t);

      // 2. Stars
      for (const s of stars) {
        const tw = Math.sin(t * s.ts + s.tp);
        const o = s.o * (0.6 + 0.4 * tw);
        ctx.beginPath();
        ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255,255,255,${o})`;
        ctx.fill();
        if (s.r > 1.2) {
          ctx.beginPath();
          ctx.arc(s.x, s.y, s.r * 3, 0, Math.PI * 2);
          const gl = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, s.r * 3);
          gl.addColorStop(0, `rgba(200,220,255,${o * 0.3})`);
          gl.addColorStop(1, 'rgba(200,220,255,0)');
          ctx.fillStyle = gl;
          ctx.fill();
        }
      }

      // 3. Asteroids
      for (let i = asteroids.length - 1; i >= 0; i--) {
        const a = asteroids[i];
        a.x += a.vx;
        a.y += a.vy + Math.sin(t * 0.0008 + i) * 0.08;
        a.rot += a.rs;
        drawAsteroid(a);
        if (a.x < -80 || a.x > W + 80 || a.y < -80 || a.y > H + 80) {
          asteroids[i] = makeAsteroid(W, H);
        }
      }
      // Keep 8-12 asteroids
      while (asteroids.length < 8) asteroids.push(makeAsteroid(W, H));

      // 4. Satellites
      for (let i = satellites.length - 1; i >= 0; i--) {
        const s = satellites[i];
        s.x += s.vx;
        s.y += s.vy;
        s.angle += s.rotSpeed;
        drawSatellite(s, t);
        if (s.x < -60 || s.x > W + 60) {
          satellites[i] = makeSatellite(W, H);
        }
      }
      // Keep 3-5 satellites
      while (satellites.length < 4) satellites.push(makeSatellite(W, H));

      // 5. Meteors (shooting stars) — every 4-8 seconds
      if (t - lastMeteor > 4000 + Math.random() * 4000) {
        if (meteors.length < 3) {
          meteors.push(makeMeteor(W, H));
          lastMeteor = t;
        }
      }
      for (let i = meteors.length - 1; i >= 0; i--) {
        const m = meteors[i];
        m.x += Math.cos(m.angle) * m.speed;
        m.y += Math.sin(m.angle) * m.speed;
        m.life++;
        drawMeteor(m);
        if (m.life >= m.max) meteors.splice(i, 1);
      }

      // 6. Comets — rare (every 30-60s)
      if (t - lastComet > 30000 + Math.random() * 30000) {
        if (comets.length < 1) {
          comets.push(makeComet(W, H));
          lastComet = t;
        }
      }
      for (let i = comets.length - 1; i >= 0; i--) {
        const co = comets[i];
        co.x += co.vx;
        co.y += co.vy;
        drawComet(co);
        if (co.x > W + co.tailLen + 50) comets.splice(i, 1);
      }

      raf = requestAnimationFrame(draw);
    };

    resize();
    raf = requestAnimationFrame(draw);
    window.addEventListener('resize', resize);
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', resize); };
  }, []);

  return (
    <canvas
      ref={ref}
      className="fixed inset-0 w-full h-full pointer-events-none z-0"
      aria-hidden="true"
    />
  );
}
