"use client";

import { useEffect, useRef } from "react";

interface GridNode {
  x: number;
  y: number;
  col: number;
  row: number;
  active: boolean;
  brightness: number;
  targetBrightness: number;
  decayAt: number;
}

interface Pulse {
  startX: number;
  startY: number;
  endX: number;
  endY: number;
  pos: number;
  speed: number;
  axis: "h" | "v";
  color: [number, number, number];
}

const GRID_SPACING = 80;
const NODE_BASE_RADIUS = 1.5;
const NODE_ACTIVE_RADIUS = 2.5;
const GRID_LINE_ALPHA = 0.08;
const GRID_MAJOR_ALPHA = 0.15;
const NODE_ALPHA = 0.3;
const NODE_ACTIVE_ALPHA = 0.6;
const PULSE_SPEED = 0.003;
const MAJOR_LINE_INTERVAL = 4;

const COLORS = {
  gridMinor: [30, 41, 59] as [number, number, number],
  gridMajor: [51, 65, 85] as [number, number, number],
  node: [100, 116, 139] as [number, number, number],
  nodeActive: [16, 185, 129] as [number, number, number],
  pulseData: [59, 130, 246] as [number, number, number],
  pulseTrust: [16, 185, 129] as [number, number, number],
};

export function OperationalMesh() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let width = 0;
    let height = 0;
    let gridNodes: GridNode[] = [];
    let pulses: Pulse[] = [];
    let gridCols = 0;
    let gridRows = 0;

    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    let isReducedMotion = mq.matches;

    const spawnPulse = () => {
      const isHorizontal = Math.random() > 0.5;
      const col = Math.floor(Math.random() * Math.max(1, gridCols));
      const row = Math.floor(Math.random() * Math.max(1, gridRows));
      const color = Math.random() > 0.7 ? COLORS.pulseTrust : COLORS.pulseData;

      if (isHorizontal) {
        const y = row * GRID_SPACING;
        const goRight = Math.random() > 0.5;
        pulses.push({
          startX: goRight ? -20 : width + 20,
          startY: y,
          endX: goRight ? width + 20 : -20,
          endY: y,
          pos: 0,
          speed: PULSE_SPEED + Math.random() * 0.002,
          axis: "h",
          color,
        });
      } else {
        const x = col * GRID_SPACING;
        const goDown = Math.random() > 0.5;
        pulses.push({
          startX: x,
          startY: goDown ? -20 : height + 20,
          endX: x,
          endY: goDown ? height + 20 : -20,
          pos: 0,
          speed: PULSE_SPEED + Math.random() * 0.002,
          axis: "v",
          color,
        });
      }
    };

    const init = () => {
      gridCols = Math.ceil(width / GRID_SPACING) + 1;
      gridRows = Math.ceil(height / GRID_SPACING) + 1;

      gridNodes = [];
      for (let row = 0; row < gridRows; row++) {
        for (let col = 0; col < gridCols; col++) {
          const isMajor = col % MAJOR_LINE_INTERVAL === 0 && row % MAJOR_LINE_INTERVAL === 0;
          const base = isMajor ? 0.15 : 0.05;
          gridNodes.push({
            x: col * GRID_SPACING,
            y: row * GRID_SPACING,
            col,
            row,
            active: isMajor && Math.random() > 0.6,
            brightness: base,
            targetBrightness: base,
            decayAt: 0,
          });
        }
      }

      pulses = [];
      for (let i = 0; i < 8; i++) {
        spawnPulse();
      }
    };

    const drawGrid = () => {
      const [mr, mg, mb] = COLORS.gridMinor;
      const [Mzr, Mzg, Mzb] = COLORS.gridMajor;

      ctx.strokeStyle = `rgba(${mr}, ${mg}, ${mb}, ${GRID_LINE_ALPHA})`;
      ctx.lineWidth = 0.5;
      for (let x = 0; x <= width; x += GRID_SPACING) {
        if (Math.round(x / GRID_SPACING) % MAJOR_LINE_INTERVAL === 0) continue;
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
      }
      for (let y = 0; y <= height; y += GRID_SPACING) {
        if (Math.round(y / GRID_SPACING) % MAJOR_LINE_INTERVAL === 0) continue;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
      }

      ctx.strokeStyle = `rgba(${Mzr}, ${Mzg}, ${Mzb}, ${GRID_MAJOR_ALPHA})`;
      ctx.lineWidth = 1;
      for (let x = 0; x <= width; x += GRID_SPACING) {
        if (Math.round(x / GRID_SPACING) % MAJOR_LINE_INTERVAL !== 0) continue;
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
      }
      for (let y = 0; y <= height; y += GRID_SPACING) {
        if (Math.round(y / GRID_SPACING) % MAJOR_LINE_INTERVAL !== 0) continue;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
      }
    };

    const drawNodes = (now: number) => {
      for (const node of gridNodes) {
        if (node.decayAt > 0 && now >= node.decayAt) {
          node.targetBrightness = node.active ? 0.3 : 0.08;
          node.decayAt = 0;
        }
        node.brightness += (node.targetBrightness - node.brightness) * 0.04;

        const isActive = node.active;
        const [nr, ng, nb] = isActive ? COLORS.nodeActive : COLORS.node;
        const alpha = isActive ? NODE_ACTIVE_ALPHA : NODE_ALPHA;
        const radius = isActive ? NODE_ACTIVE_RADIUS : NODE_BASE_RADIUS;
        const finalAlpha = alpha * node.brightness;
        if (finalAlpha < 0.01) continue;

        ctx.beginPath();
        ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${nr}, ${ng}, ${nb}, ${finalAlpha})`;
        ctx.fill();
      }
    };

    const drawPulses = (dt: number, now: number) => {
      for (let i = pulses.length - 1; i >= 0; i--) {
        const pulse = pulses[i];
        pulse.pos += pulse.speed * dt * 0.06;
        if (pulse.pos >= 1) {
          pulses.splice(i, 1);
          spawnPulse();
          continue;
        }

        const x = pulse.startX + (pulse.endX - pulse.startX) * pulse.pos;
        const y = pulse.startY + (pulse.endY - pulse.startY) * pulse.pos;
        const [pr, pg, pb] = pulse.color;

        const trailStart = Math.max(0, pulse.pos - 0.08);
        const tx = pulse.startX + (pulse.endX - pulse.startX) * trailStart;
        const ty = pulse.startY + (pulse.endY - pulse.startY) * trailStart;

        const gradient = ctx.createLinearGradient(tx, ty, x, y);
        gradient.addColorStop(0, `rgba(${pr}, ${pg}, ${pb}, 0)`);
        gradient.addColorStop(1, `rgba(${pr}, ${pg}, ${pb}, 0.6)`);

        ctx.strokeStyle = gradient;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(tx, ty);
        ctx.lineTo(x, y);
        ctx.stroke();

        const snapCol = Math.round(x / GRID_SPACING);
        const snapRow = Math.round(y / GRID_SPACING);
        for (const node of gridNodes) {
          if (node.col === snapCol && node.row === snapRow) {
            node.targetBrightness = 0.8;
            node.decayAt = now + 300;
          }
        }
      }
    };

    const renderStatic = () => {
      ctx.clearRect(0, 0, width, height);
      drawGrid();
      drawNodes(performance.now());
    };

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = window.innerWidth;
      height = window.innerHeight;
      c.width = Math.floor(width * dpr);
      c.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      init();
      if (isReducedMotion) renderStatic();
    };

    let lastTime = performance.now();
    const animate = (time: number) => {
      if (isReducedMotion) return;
      const dt = Math.min(time - lastTime, 50);
      lastTime = time;

      ctx.clearRect(0, 0, width, height);
      drawGrid();
      drawNodes(time);
      drawPulses(dt, time);
      raf = requestAnimationFrame(animate);
    };

    const onMotionChange = (e: MediaQueryListEvent) => {
      isReducedMotion = e.matches;
      cancelAnimationFrame(raf);
      if (isReducedMotion) {
        renderStatic();
      } else {
        lastTime = performance.now();
        raf = requestAnimationFrame(animate);
      }
    };

    resize();
    if (!isReducedMotion) {
      raf = requestAnimationFrame(animate);
    }
    window.addEventListener("resize", resize);
    mq.addEventListener("change", onMotionChange);

    return () => {
      window.removeEventListener("resize", resize);
      mq.removeEventListener("change", onMotionChange);
      cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 w-full h-full pointer-events-none"
      style={{ zIndex: 0 }}
      aria-hidden="true"
    />
  );
}
