'use client';

import { useEffect, useRef } from 'react';
import { SpaceVisualState } from '@/lib/spaceStatusMap';

// ── Types ───────────────────────────────────────────────────────────────────
interface Star { x: number; y: number; r: number; o: number; ts: number; tp: number; baseTs: number; }

interface Asteroid {
  x: number; y: number; size: number; vx: number; vy: number;
  rot: number; rs: number; opacity: number; shape: number[]; baseVx: number; baseVy: number;
}

interface Satellite {
  x: number; y: number; vx: number; vy: number;
  angle: number; rotSpeed: number; blink: number; size: number; baseVx: number; baseVy: number;
}

interface Meteor {
  x: number; y: number; len: number; speed: number;
  angle: number; o: number; life: number; max: number; baseSpeed: number;
}

interface AuroraBand {
  y: number; amplitude: number; wavelength: number; speed: number;
  color: [number, number, number]; opacity: number; thickness: number; phase: number; baseSpeed: number; baseOpacity: number;
}

interface Comet {
  x: number; y: number; vx: number; vy: number;
  tailLen: number; size: number; o: number; color: [number, number, number]; baseVx: number; baseVy: number;
}

interface Planet {
  orbitRadius: number; angle: number; speed: number; baseSpeed: number;
  size: number; color: [number, number, number]; glowColor: [number, number, number];
  name: string; hasRing?: boolean; ringColor?: [number, number, number];
}

interface SolarSystem {
  cx: number; cy: number; starRadius: number; planets: Planet[];
}

interface StarfieldProps {
  visualState: SpaceVisualState;
}

// ── Helpers ─────────────────────────────────────────────────────────────────
const rand = (min: number, max: number) => Math.random() * (max - min) + min;

function makeAsteroid(w: number, h: number): Asteroid {
  const fromLeft = Math.random() > 0.5;
  const vertices = 5 + Math.floor(rand(0, 5));
  const shape: number[] = [];
  for (let i = 0; i < vertices; i++) shape.push(rand(0.5, 1));
  const vx = fromLeft ? rand(0.1, 0.5) : rand(-0.5, -0.1);
  const vy = rand(-0.15, 0.15);
  return {
    x: fromLeft ? rand(-60, -20) : rand(w + 20, w + 60),
    y: rand(40, h - 40),
    size: rand(2, 8),
    vx, vy, baseVx: vx, baseVy: vy,
    rot: rand(0, Math.PI * 2),
    rs: rand(-0.015, 0.015),
    opacity: rand(0.12, 0.35),
    shape,
  };
}

function makeSatellite(w: number, h: number): Satellite {
  const fromLeft = Math.random() > 0.3;
  const vx = fromLeft ? rand(0.3, 0.8) : rand(-0.8, -0.3);
  const vy = rand(-0.05, 0.05);
  return {
    x: fromLeft ? -25 : w + 25,
    y: rand(40, h * 0.6),
    vx, vy, baseVx: vx, baseVy: vy,
    angle: rand(0, Math.PI * 2),
    rotSpeed: rand(0.003, 0.012),
    blink: rand(0, Math.PI * 2),
    size: rand(3, 6),
  };
}

function makeMeteor(w: number, h: number): Meteor {
  const speed = rand(8, 16);
  return {
    x: rand(0, w * 0.9), y: rand(0, h * 0.35),
    len: rand(50, 140), speed, baseSpeed: speed,
    angle: rand(Math.PI / 7, Math.PI / 4),
    o: rand(0.5, 1), life: 0, max: rand(30, 55),
  };
}

function makeComet(w: number, h: number): Comet {
  const vx = rand(0.6, 1.2);
  const vy = rand(0.05, 0.2);
  return {
    x: -30, y: rand(30, h * 0.4),
    vx, vy, baseVx: vx, baseVy: vy,
    tailLen: rand(80, 160), size: rand(2.5, 4),
    o: rand(0.3, 0.6),
    color: Math.random() > 0.5 ? [100, 200, 255] : [180, 255, 180],
  };
}

// ── Component ───────────────────────────────────────────────────────────────
export function Starfield({ visualState }: StarfieldProps) {
  const ref = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef<SpaceVisualState>(visualState);

  // Update ref when props change to avoid restarting animation
  useEffect(() => {
    stateRef.current = visualState;
  }, [visualState]);

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
    let solar: SolarSystem = { cx: 0, cy: 0, starRadius: 0, planets: [] };
    let lastMeteor = 0;
    let lastComet = 0;
    let blackHoleParticles: {x: number, y: number, angle: number, speed: number, dist: number, r: number}[] = [];
    let whiteHoleParticles: {x: number, y: number, angle: number, speed: number, dist: number, r: number}[] = [];

    const isReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const resize = () => {
      c.width = window.innerWidth;
      c.height = window.innerHeight;
      init();
    };

    const init = () => {
      const W = c.width, H = c.height;
      const m = isReducedMotion ? 0.3 : 1;

      // Stars
      const n = Math.floor((W * H) / 2200);
      stars = [];
      for (let i = 0; i < n; i++) {
        const ts = rand(0.004, 0.025) * m;
        stars.push({
          x: rand(0, W), y: rand(0, H),
          r: rand(0.3, 2), o: rand(0.3, 0.8),
          ts, baseTs: ts, tp: rand(0, Math.PI * 2),
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
        { y: H * 0.12, amplitude: 25, wavelength: 0.004, speed: 0.0004 * m, color: [0, 255, 120], opacity: 0.06, baseOpacity: 0.06, thickness: 50, phase: 0, baseSpeed: 0.0004 * m },
        { y: H * 0.18, amplitude: 30, wavelength: 0.003, speed: 0.0003 * m, color: [0, 200, 255], opacity: 0.04, baseOpacity: 0.04, thickness: 40, phase: 1, baseSpeed: 0.0003 * m },
        { y: H * 0.25, amplitude: 20, wavelength: 0.005, speed: 0.00035 * m, color: [120, 0, 255], opacity: 0.035, baseOpacity: 0.035, thickness: 35, phase: 2, baseSpeed: 0.00035 * m },
        { y: H * 0.14, amplitude: 35, wavelength: 0.0025, speed: 0.00045 * m, color: [0, 255, 200], opacity: 0.045, baseOpacity: 0.045, thickness: 45, phase: 3, baseSpeed: 0.00045 * m },
        { y: H * 0.22, amplitude: 18, wavelength: 0.006, speed: 0.0005 * m, color: [255, 80, 200], opacity: 0.025, baseOpacity: 0.025, thickness: 30, phase: 4, baseSpeed: 0.0005 * m },
      ];

      // Solar system — big, bold, center-right
      const minDim = Math.min(W, H);
      solar = {
        cx: W * 0.55,
        cy: H * 0.48,
        starRadius: minDim * 0.045,
        planets: [
          { orbitRadius: minDim * 0.09, angle: rand(0, Math.PI * 2), speed: 0.0008 * m, baseSpeed: 0.0008 * m, size: minDim * 0.008, color: [180, 120, 80], glowColor: [200, 150, 100], name: 'Mercury' },
          { orbitRadius: minDim * 0.14, angle: rand(0, Math.PI * 2), speed: 0.0006 * m, baseSpeed: 0.0006 * m, size: minDim * 0.013, color: [220, 180, 100], glowColor: [240, 200, 120], name: 'Venus' },
          { orbitRadius: minDim * 0.20, angle: rand(0, Math.PI * 2), speed: 0.0005 * m, baseSpeed: 0.0005 * m, size: minDim * 0.014, color: [60, 140, 220], glowColor: [80, 160, 255], name: 'Earth' },
          { orbitRadius: minDim * 0.26, angle: rand(0, Math.PI * 2), speed: 0.0004 * m, baseSpeed: 0.0004 * m, size: minDim * 0.011, color: [200, 80, 50], glowColor: [230, 100, 60], name: 'Mars' },
          { orbitRadius: minDim * 0.35, angle: rand(0, Math.PI * 2), speed: 0.00025 * m, baseSpeed: 0.00025 * m, size: minDim * 0.028, color: [200, 170, 120], glowColor: [220, 190, 140], name: 'Jupiter' },
          { orbitRadius: minDim * 0.43, angle: rand(0, Math.PI * 2), speed: 0.00018 * m, baseSpeed: 0.00018 * m, size: minDim * 0.024, color: [210, 190, 140], glowColor: [230, 210, 160], name: 'Saturn', hasRing: true, ringColor: [200, 180, 130] },
          { orbitRadius: minDim * 0.52, angle: rand(0, Math.PI * 2), speed: 0.00012 * m, baseSpeed: 0.00012 * m, size: minDim * 0.020, color: [140, 220, 230], glowColor: [160, 240, 250], name: 'Uranus', hasRing: true, ringColor: [150, 210, 220] },
          { orbitRadius: minDim * 0.60, angle: rand(0, Math.PI * 2), speed: 0.00009 * m, baseSpeed: 0.00009 * m, size: minDim * 0.018, color: [60, 100, 220], glowColor: [80, 120, 255], name: 'Neptune' },
          { orbitRadius: minDim * 0.30, angle: rand(0, Math.PI * 2), speed: 0.00035 * m, baseSpeed: 0.00035 * m, size: minDim * 0.006, color: [160, 160, 150], glowColor: [180, 180, 170], name: 'Ceres' },
          { orbitRadius: minDim * 0.68, angle: rand(0, Math.PI * 2), speed: 0.00007 * m, baseSpeed: 0.00007 * m, size: minDim * 0.007, color: [190, 170, 150], glowColor: [210, 190, 170], name: 'Pluto' },
        ],
      };

      for(let i=0; i<150; i++) {
        blackHoleParticles.push({
          x: 0, y: 0, angle: rand(0, Math.PI * 2), speed: rand(1, 4), dist: rand(20, minDim), r: rand(1, 3)
        });
        whiteHoleParticles.push({
          x: 0, y: 0, angle: rand(0, Math.PI * 2), speed: rand(1, 4), dist: rand(5, 50), r: rand(1, 3)
        });
      }
    };

    // ── Drawing functions ──────────────────────────────────────────────────

    const drawAurora = (a: AuroraBand, t: number, state: SpaceVisualState) => {
      const W = c.width;
      const currentSpeed = a.baseSpeed * state.baseSpeedMultiplier;
      a.phase += currentSpeed;
      const shift = a.phase;
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
      let [r, g, b] = a.color;
      if (state.anomalyColor) {
        [r, g, b] = [
            (r + state.anomalyColor[0]) / 2,
            (g + state.anomalyColor[1]) / 2,
            (b + state.anomalyColor[2]) / 2
        ];
      }

      const pulse = 0.7 + 0.3 * Math.sin(t * 0.0008 + shift);
      const targetOpacity = a.baseOpacity * state.auroraOpacityMultiplier;
      a.opacity += (targetOpacity - a.opacity) * 0.05; // smooth transition
      const op = a.opacity * pulse;
      grad.addColorStop(0, `rgba(${r}, ${g}, ${b}, 0)`);
      grad.addColorStop(0.3, `rgba(${r}, ${g}, ${b}, ${op})`);
      grad.addColorStop(0.5, `rgba(${r}, ${g}, ${b}, ${op * 1.3})`);
      grad.addColorStop(0.7, `rgba(${r}, ${g}, ${b}, ${op * 0.8})`);
      grad.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
      ctx.fillStyle = grad;
      ctx.fill();
    };

    const drawAsteroid = (a: Asteroid, state: SpaceVisualState) => {
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
      let color1 = '160, 150, 140';
      let color2 = '90, 80, 70';
      let craterColor = '60, 55, 50';

      if (state.anomalyColor) {
        color1 = '200, 120, 50';
        color2 = '150, 80, 20';
        craterColor = '100, 50, 10';
      }

      g.addColorStop(0, `rgba(${color1}, ${a.opacity * 1.3})`);
      g.addColorStop(1, `rgba(${color2}, ${a.opacity})`);
      ctx.fillStyle = g;
      ctx.fill();
      // Crater
      ctx.beginPath();
      ctx.arc(a.size * 0.15, -a.size * 0.1, a.size * 0.2, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${craterColor}, ${a.opacity * 0.6})`;
      ctx.fill();
      ctx.restore();
    };

    const drawSatellite = (s: Satellite, t: number, state: SpaceVisualState) => {
      const currentSpeed = s.baseVx * state.baseSpeedMultiplier;
      const blink = Math.sin(t * 0.004 * state.baseSpeedMultiplier + s.blink);
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

    // ── Solar system drawing ─────────────────────────────────────────────
    const drawSolarSystem = (t: number, state: SpaceVisualState) => {
      const { cx, cy, starRadius, planets } = solar;

      if (state.showBlackHole) {
        // Black Hole Distortion Effect
        ctx.beginPath();
        ctx.arc(cx, cy, starRadius * 8, 0, Math.PI * 2);
        const bhGrad = ctx.createRadialGradient(cx, cy, starRadius, cx, cy, starRadius * 8);
        bhGrad.addColorStop(0, 'rgba(0, 0, 0, 1)');
        bhGrad.addColorStop(0.1, `rgba(${state.coreGlow[0]}, ${state.coreGlow[1]}, ${state.coreGlow[2]}, 0.8)`);
        bhGrad.addColorStop(0.5, `rgba(${state.coreGlow[0]}, ${state.coreGlow[1]}, ${state.coreGlow[2]}, 0.2)`);
        bhGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');
        ctx.fillStyle = bhGrad;
        ctx.fill();

        // Particles getting sucked in
        for (let p of blackHoleParticles) {
            p.dist -= p.speed * state.baseSpeedMultiplier;
            p.angle += 0.05 * state.baseSpeedMultiplier;
            if (p.dist < starRadius) p.dist = rand(starRadius * 4, starRadius * 8);

            const px = cx + Math.cos(p.angle) * p.dist;
            const py = cy + Math.sin(p.angle) * p.dist * 0.6;
            ctx.beginPath();
            ctx.arc(px, py, p.r, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(255, 100, 100, ${p.dist / (starRadius*8)})`;
            ctx.fill();
        }
      } else if (state.showWhiteHole) {
        // White Hole Recovery Effect
        ctx.beginPath();
        ctx.arc(cx, cy, starRadius * 6, 0, Math.PI * 2);
        const whGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, starRadius * 6);
        whGrad.addColorStop(0, 'rgba(255, 255, 255, 1)');
        whGrad.addColorStop(0.2, `rgba(${state.coreGlow[0]}, ${state.coreGlow[1]}, ${state.coreGlow[2]}, 0.8)`);
        whGrad.addColorStop(1, 'rgba(255, 255, 255, 0)');
        ctx.fillStyle = whGrad;
        ctx.fill();

        // Particles bursting out
        for (let p of whiteHoleParticles) {
            p.dist += p.speed * state.baseSpeedMultiplier;
            p.angle -= 0.02 * state.baseSpeedMultiplier;
            if (p.dist > starRadius * 8) {
                p.dist = rand(0, starRadius * 2);
                p.angle = rand(0, Math.PI * 2);
            }

            const px = cx + Math.cos(p.angle) * p.dist;
            const py = cy + Math.sin(p.angle) * p.dist * 0.6;
            ctx.beginPath();
            ctx.arc(px, py, p.r, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(255, 255, 255, ${1 - p.dist / (starRadius*8)})`;
            ctx.fill();
        }
      } else {

        // Normal Star with dynamic colors
        // Draw orbit rings
        for (const p of planets) {
            ctx.beginPath();
            ctx.arc(cx, cy, p.orbitRadius, 0, Math.PI * 2);
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.04)';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        // Draw central star with multi-layer glow
        const pulse = 0.85 + 0.15 * Math.sin(t * 0.001 * state.baseSpeedMultiplier);
        // Outermost glow
        const g3 = ctx.createRadialGradient(cx, cy, 0, cx, cy, starRadius * 6);
        g3.addColorStop(0, `rgba(${state.coreGlow[0]}, ${state.coreGlow[1]}, ${state.coreGlow[2]}, ${0.06 * pulse})`);
        g3.addColorStop(0.3, `rgba(${state.coreGlow[0]}, ${state.coreGlow[1]}, ${state.coreGlow[2]}, ${0.03 * pulse})`);
        g3.addColorStop(1, `rgba(${state.coreGlow[0]}, ${state.coreGlow[1]}, ${state.coreGlow[2]}, 0)`);
        ctx.beginPath();
        ctx.arc(cx, cy, starRadius * 6, 0, Math.PI * 2);
        ctx.fillStyle = g3;
        ctx.fill();
        // Mid glow
        const g2 = ctx.createRadialGradient(cx, cy, 0, cx, cy, starRadius * 3);
        g2.addColorStop(0, `rgba(${state.coreGlow[0]}, ${state.coreGlow[1]}, ${state.coreGlow[2]}, ${0.15 * pulse})`);
        g2.addColorStop(0.5, `rgba(${state.coreGlow[0]}, ${state.coreGlow[1]}, ${state.coreGlow[2]}, ${0.08 * pulse})`);
        g2.addColorStop(1, `rgba(${state.coreGlow[0]}, ${state.coreGlow[1]}, ${state.coreGlow[2]}, 0)`);
        ctx.beginPath();
        ctx.arc(cx, cy, starRadius * 3, 0, Math.PI * 2);
        ctx.fillStyle = g2;
        ctx.fill();
        // Core
        const g1 = ctx.createRadialGradient(cx, cy, 0, cx, cy, starRadius);
        g1.addColorStop(0, `rgba(${state.coreColor[0]}, ${state.coreColor[1]}, ${state.coreColor[2]}, ${0.35 * pulse})`);
        g1.addColorStop(0.4, `rgba(${state.coreColor[0]}, ${state.coreColor[1]}, ${state.coreColor[2]}, ${0.25 * pulse})`);
        g1.addColorStop(0.8, `rgba(${state.coreColor[0]}, ${state.coreColor[1]}, ${state.coreColor[2]}, ${0.15 * pulse})`);
        g1.addColorStop(1, `rgba(${state.coreColor[0]}, ${state.coreColor[1]}, ${state.coreColor[2]}, 0)`);
        ctx.beginPath();
        ctx.arc(cx, cy, starRadius, 0, Math.PI * 2);
        ctx.fillStyle = g1;
        ctx.fill();
      }

      // Draw planets
      for (const p of planets) {
        if (state.showBlackHole) {
             // pull planets toward center
             p.orbitRadius -= 0.5;
             if (p.orbitRadius < 0) p.orbitRadius = 0;
        } else if (state.showWhiteHole) {
            // push planets outward slightly
            p.orbitRadius += 0.2;
            if (p.orbitRadius > Math.min(cx, cy) * 0.8) p.orbitRadius = Math.min(cx, cy) * 0.8;
        }

        p.angle += p.baseSpeed * state.baseSpeedMultiplier;
        const px = cx + Math.cos(p.angle) * p.orbitRadius;
        const py = cy + Math.sin(p.angle) * p.orbitRadius * 0.4; // elliptical orbits for perspective
        let [r, g, b] = p.color;
        let [gr, gg, gb] = p.glowColor;

        if (state.anomalyColor) {
            r = (r + state.anomalyColor[0]) / 2;
            g = (g + state.anomalyColor[1]) / 2;
            b = (b + state.anomalyColor[2]) / 2;
        }

        // Planet glow
        const pg = ctx.createRadialGradient(px, py, 0, px, py, p.size * 3);
        pg.addColorStop(0, `rgba(${gr}, ${gg}, ${gb}, 0.25)`);
        pg.addColorStop(1, `rgba(${gr}, ${gg}, ${gb}, 0)`);
        ctx.beginPath();
        ctx.arc(px, py, p.size * 3, 0, Math.PI * 2);
        ctx.fillStyle = pg;
        ctx.fill();

        // Planet body
        const pb = ctx.createRadialGradient(px - p.size * 0.3, py - p.size * 0.3, 0, px, py, p.size);
        pb.addColorStop(0, `rgba(${Math.min(r + 60, 255)}, ${Math.min(g + 60, 255)}, ${Math.min(b + 60, 255)}, 0.6)`);
        pb.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0.4)`);
        ctx.beginPath();
        ctx.arc(px, py, p.size, 0, Math.PI * 2);
        ctx.fillStyle = pb;
        ctx.fill();

        // Saturn-like ring
        if (p.hasRing && p.ringColor) {
          const [rr, rg, rb] = p.ringColor;
          ctx.save();
          ctx.translate(px, py);
          ctx.scale(1, 0.3);
          ctx.beginPath();
          ctx.arc(0, 0, p.size * 2, 0, Math.PI * 2);
          ctx.strokeStyle = `rgba(${rr}, ${rg}, ${rb}, 0.3)`;
          ctx.lineWidth = p.size * 0.4;
          ctx.stroke();
          ctx.restore();
        }
      }
    };

    // ── Main loop ─────────────────────────────────────────────────────────
    const draw = (t: number) => {
      const W = c.width, H = c.height;
      const state = stateRef.current;
      ctx.clearRect(0, 0, W, H);

      // 1. Aurora (behind everything)
      for (const a of auroras) drawAurora(a, t, state);

      // 2. Solar system (behind stars, big and bold)
      drawSolarSystem(t, state);

      // 2. Stars
      let i = 0;
      for (const s of stars) {
        // Only draw based on density
        if (i > stars.length * state.particleDensity && state.particleDensity < 1) { i++; continue; }

        const currentTs = s.baseTs * state.baseSpeedMultiplier;
        const tw = Math.sin(t * currentTs + s.tp);
        let o = s.o * (0.6 + 0.4 * tw);

        if (state.anomalyColor && Math.random() > 0.9) {
            ctx.fillStyle = `rgba(${state.anomalyColor[0]},${state.anomalyColor[1]},${state.anomalyColor[2]},${o})`;
        } else {
            ctx.fillStyle = `rgba(255,255,255,${o})`;
        }

        ctx.beginPath();
        ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
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
        i++;
      }

      // 3. Asteroids
      let targetAsteroids = Math.floor(10 * state.particleDensity);
      for (let i = asteroids.length - 1; i >= 0; i--) {
        const a = asteroids[i];
        a.x += a.baseVx * state.baseSpeedMultiplier;
        a.y += a.baseVy * state.baseSpeedMultiplier + Math.sin(t * 0.0008 + i) * 0.08;
        a.rot += a.rs * state.baseSpeedMultiplier;
        drawAsteroid(a, state);
        if (a.x < -80 || a.x > W + 80 || a.y < -80 || a.y > H + 80) {
          asteroids[i] = makeAsteroid(W, H);
        }
      }
      while (asteroids.length < targetAsteroids) asteroids.push(makeAsteroid(W, H));
      if (asteroids.length > targetAsteroids) asteroids.length = targetAsteroids;

      // 4. Satellites
      let targetSatellites = Math.floor(4 * state.particleDensity);
      for (let i = satellites.length - 1; i >= 0; i--) {
        const s = satellites[i];
        s.x += s.baseVx * state.baseSpeedMultiplier;
        s.y += s.baseVy * state.baseSpeedMultiplier;
        s.angle += s.rotSpeed * state.baseSpeedMultiplier;
        drawSatellite(s, t, state);
        if (s.x < -60 || s.x > W + 60) {
          satellites[i] = makeSatellite(W, H);
        }
      }
      while (satellites.length < targetSatellites) satellites.push(makeSatellite(W, H));
      if (satellites.length > targetSatellites) satellites.length = targetSatellites;

      // 5. Meteors (shooting stars)
      const meteorInterval = (4000 + Math.random() * 4000) / (state.meteorFrequency || 0.1);
      if (t - lastMeteor > meteorInterval) {
        if (meteors.length < 5) {
          meteors.push(makeMeteor(W, H));
          lastMeteor = t;
        }
      }
      for (let i = meteors.length - 1; i >= 0; i--) {
        const m = meteors[i];
        const currentSpeed = m.baseSpeed * state.baseSpeedMultiplier;
        m.x += Math.cos(m.angle) * currentSpeed;
        m.y += Math.sin(m.angle) * currentSpeed;
        m.life += state.baseSpeedMultiplier;
        drawMeteor(m);
        if (m.life >= m.max) meteors.splice(i, 1);
      }

      // 6. Comets
      const cometInterval = (30000 + Math.random() * 30000) / (state.cometFrequency || 0.1);
      if (t - lastComet > cometInterval) {
        if (comets.length < 3) {
          comets.push(makeComet(W, H));
          lastComet = t;
        }
      }
      for (let i = comets.length - 1; i >= 0; i--) {
        const co = comets[i];
        co.x += co.baseVx * state.baseSpeedMultiplier;
        co.y += co.baseVy * state.baseSpeedMultiplier;
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
