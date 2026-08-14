"use client";

import { useEffect, useRef } from "react";

/**
 * OperationalMesh — Grid-forward enterprise background.
 *
 * The grid is the primary visual element:
 * - Prominent structural grid lines
 * - Intersection nodes at grid crossings
 * - Data pulses traveling along grid lines
 * - Subtle breathing animation on grid intersections
 *
 * Designed for dark operational interfaces.
 */

interface GridNode {
  x: number;
  y: number;
  col: number;
  row: number;
  active: boolean;
  brightness: number;
  targetBrightness: number;
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
  cooldown: number;
}

const GRID_SPACING = 80;
const NODE_BASE_RADIUS = 1.5;
const NODE_ACTIVE_RADIUS = 2.5;
const GRID_LINE_ALPHA = 0.08;
const GRID_MAJOR_ALPHA = 0.15;
const NODE_ALPHA = 0.3;
const NODE_ACTIVE_ALPHA = 0.6;
const PULSE_SPEED = 0.003;
const PULSE_COOLDOWN_MIN = 800;
const PULSE_COOLDOWN_MAX = 4000;
const MAJOR_LINE_INTERVAL = 4;

// Color palette
const COLORS = {
  gridMinor: [30, 41, 59] as [number, number, number],     // slate-800
  gridMajor: [51, 65, 85] as [number, number, number],     // slate-700
  node: [100, 116, 139] as [number, number, number],       // slate-500
  nodeActive: [16, 185, 129] as [number, number, number],  // emerald
  pulseData: [59, 130, 246] as [number, number, number],   // blue
  pulseTrust: [16, 185, 129] as [number, number, number],  // emerald
  intersection: [71, 85, 105] as [number, number, number], // slate-600
};

export function OperationalMesh() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;

    let raf: number;
    let gridNodes: GridNode[] = [];
    let pulses: Pulse[] = [];
    let gridCols = 0;
    let gridRows = 0;
    const isReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const resize = () => {
      c.width = window.innerWidth;
      c.height = window.innerHeight;
      init();
    };

    const init = () => {
      const W = c.width;
      const H = c.height;
      gridCols = Math.ceil(W / GRID_SPACING) + 1;
      gridRows = Math.ceil(H / GRID_SPACING) + 1;

      // Create grid nodes at intersections
      gridNodes = [];
      for (let row = 0; row < gridRows; row++) {
        for (let col = 0; col < gridCols; col++) {
          const isMajor = col % MAJOR_LINE_INTERVAL === 0 && row % MAJOR_LINE_INTERVAL === 0;
          gridNodes.push({
            x: col * GRID_SPACING,
            y: row * GRID_SPACING,
            col,
            row,
            active: isMajor && Math.random() > 0.6,
            brightness: 0,
            targetBrightness: isMajor ? 0.15 + Math.random() * 0.1 : 0.05,
          });
        }
      }

      // Initialize pulses
      pulses = [];
      for (let i = 0; i < 8; i++) {
        spawnPulse();
      }
    };

    const spawnPulse = () => {
      const isHorizontal = Math.random() > 0.5;
      const col = Math.floor(Math.random() * gridCols);
      const row = Math.floor(Math.random() * gridRows);
      const color = Math.random() > 0.7 ? COLORS.pulseTrust : COLORS.pulseData;

      if (isHorizontal) {
        const y = row * GRID_SPACING;
        const goRight = Math.random() > 0.5;
        pulses.push({
          startX: goRight ? -20 : c.width + 20,
          startY: y,
          endX: goRight ? c.width + 20 : -20,
          endY: y,
          pos: 0,
          speed: PULSE_SPEED + Math.random() * 0.002,
          axis: "h",
          color,
          cooldown: 0,
        });
      } else {
        const x = col * GRID_SPACING;
        const goDown = Math.random() > 0.5;
        pulses.push({
          startX: x,
          startY: goDown ? -20 : c.height + 20,
          endX: x,
          endY: goDown ? c.height + 20 : -20,
          pos: 0,
          speed: PULSE_SPEED + Math.random() * 0.002,
          axis: "v",
          color,
          cooldown: 0,
        });
      }
    };

    const drawGrid = () => {
      const W = c.width;
      const H = c.height;
      const [mr, mg, mb] = COLORS.gridMinor;
      const [Mzr, Mzg, Mzb] = COLORS.gridMajor;

      // Minor grid lines
      ctx.strokeStyle = `rgba(${mr}, ${mg}, ${mb}, ${GRID_LINE_ALPHA})`;
      ctx.lineWidth = 0.5;

      for (let x = 0; x <= W; x += GRID_SPACING) {
        if (Math.round(x / GRID_SPACING) % MAJOR_LINE_INTERVAL === 0) continue;
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, H);
        ctx.stroke();
      }
      for (let y = 0; y <= H; y += GRID_SPACING) {
        if (Math.round(y / GRID_SPACING) % MAJOR_LINE_INTERVAL === 0) continue;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(W, y);
        ctx.stroke();
      }

      // Major grid lines
      ctx.strokeStyle = `rgba(${Mzr}, ${Mzg}, ${Mzb}, ${GRID_MAJOR_ALPHA})`;
      ctx.lineWidth = 1;

      for (let x = 0; x <= W; x += GRID_SPACING) {
        if (Math.round(x / GRID_SPACING) % MAJOR_LINE_INTERVAL !== 0) continue;
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, H);
        ctx.stroke();
      }
      for (let y = 0; y <= H; y += GRID_SPACING) {
        if (Math.round(y / GRID_SPACING) % MAJOR_LINE_INTERVAL !== 0) continue;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(W, y);
        ctx.stroke();
      }
    };

    const drawNodes = () => {
      for (const node of gridNodes) {
        // Smooth brightness transition
        node.brightness += (node.targetBrightness - node.brightness) * 0.02;

        const isActive = node.active;
        const [nr, ng, nb] = isActive ? COLORS.nodeActive : COLORS.node;
        const alpha = isActive ? NODE_ACTIVE_ALPHA : NODE_ALPHA;
        const radius = isActive ? NODE_ACTIVE_RADIUS : NODE_BASE_RADIUS;
        const finalAlpha = alpha * node.brightness;

        if (finalAlpha < 0.01) continue;

        // Intersection glow for major nodes
        if (isActive && node.brightness > 0.1) {
          const grad = ctx.createRadialGradient(
            node.x, node.y, 0,
            node.x, node.y, GRID_SPACING * 0.4
          );
          grad.addColorStop(0, `rgba(${nr}, ${ng}, ${nb}, ${finalAlpha * 0.15})`);
          grad.addColorStop(1, `rgba(${nr}, ${ng}, ${nb}, 0)`);
          ctx.fillStyle = grad;
          ctx.beginPath();
          ctx.arc(node.x, node.y, GRID_SPACING * 0.4, 0, Math.PI * 2);
          ctx.fill();
        }

        // Node dot
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${nr}, ${ng}, ${nb}, ${finalAlpha})`;
        ctx.fill();
      }
    };

    const drawPulses = (dt: number) => {
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

        // Pulse trail
        const trailLength = 0.08;
        const trailStart = Math.max(0, pulse.pos - trailLength);
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

        // Pulse head glow
        const headGrad = ctx.createRadialGradient(x, y, 0, x, y, 6);
        headGrad.addColorStop(0, `rgba(${pr}, ${pg}, ${pb}, 0.8)`);
        headGrad.addColorStop(1, `rgba(${pr}, ${pg}, ${pb}, 0)`);
        ctx.fillStyle = headGrad;
        ctx.beginPath();
        ctx.arc(x, y, 6, 0, Math.PI * 2);
        ctx.fill();

        // Activate nodes near pulse
        const snapCol = Math.round(x / GRID_SPACING);
        const snapRow = Math.round(y / GRID_SPACING);
        for (const node of gridNodes) {
          if (node.col === snapCol && node.row === snapRow) {
            node.targetBrightness = 0.8;
            setTimeout(() => { node.targetBrightness = node.active ? 0.3 : 0.1; }, 300);
          }
        }
      }
    };

    const updateBreathing = () => {
      // Periodically activate random major nodes
      if (Math.random() > 0.98) {
        const majorNodes = gridNodes.filter(
          n => n.col % MAJOR_LINE_INTERVAL === 0 && n.row % MAJOR_LINE_INTERVAL === 0
        );
        if (majorNodes.length > 0) {
          const node = majorNodes[Math.floor(Math.random() * majorNodes.length)];
          node.active = true;
          node.targetBrightness = 0.5;
          setTimeout(() => { node.targetBrightness = 0.15; }, 2000);
        }
      }
    };

    let lastTime = performance.now();

    const animate = (time: number) => {
      const dt = Math.min(time - lastTime, 50);
      lastTime = time;

      ctx.clearRect(0, 0, c.width, c.height);

      drawGrid();

      if (!isReducedMotion) {
        updateBreathing();
      }

      drawNodes();
      drawPulses(dt);

      raf = requestAnimationFrame(animate);
    };

    resize();
    window.addEventListener("resize", resize);
    raf = requestAnimationFrame(animate);

    return () => {
      window.removeEventListener("resize", resize);
      cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 pointer-events-none"
      style={{ zIndex: 0 }}
      aria-hidden="true"
    />
  );
}
