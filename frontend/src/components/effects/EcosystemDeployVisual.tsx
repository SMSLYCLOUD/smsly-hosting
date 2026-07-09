"use client";

import { useEffect, useRef } from "react";

interface PhaseNode {
  label: string;
  sublabel: string;
  color: string;
  glow: string;
}

export function EcosystemDeployVisual() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let animId = 0;
    let w = 0;
    let h = 0;

    const phases: PhaseNode[] = [
      { label: "SCAN", sublabel: "AI Framework Detection", color: "#3b82f6", glow: "rgba(59,130,246," },
      { label: "PLAN", sublabel: "Dependency Graph", color: "#8b5cf6", glow: "rgba(139,92,246," },
      { label: "DEPLOY", sublabel: "Wave Orchestration", color: "#10b981", glow: "rgba(16,185,129," },
    ];

    const serverNodes: { x: number; y: number; size: number; label: string; color: string }[] = [];
    const particles: { x: number; y: number; vx: number; vy: number; life: number; color: string; size: number }[] = [];
    const flowParticles: { fromX: number; fromY: number; toX: number; toY: number; t: number; speed: number; color: string }[] = [];

    function layout() {
      serverNodes.length = 0;
      // Deploy phase server nodes (bottom area)
      const serverCount = 5;
      const serverSpacing = w / (serverCount + 1);
      for (let i = 0; i < serverCount; i++) {
        const sx = serverSpacing * (i + 1);
        const sy = h * 0.82;
        const size = i === 0 ? 14 : 8 + Math.random() * 4;
        const colors = ["#10b981", "#06b6d4", "#3b82f6", "#f59e0b", "#ec4899"];
        serverNodes.push({
          x: sx, y: sy, size,
          label: i === 0 ? "master" : `node-${i}`,
          color: colors[i % colors.length],
        });
      }
    }

    function spawnFlowParticle(phaseIdx: number) {
      if (serverNodes.length === 0) return;
      // Connect phase circles to each other and to server nodes
      const phaseX = w * ((phaseIdx + 1) / (phases.length + 1));
      const phaseY = h * 0.3;
      const target = serverNodes[Math.floor(Math.random() * serverNodes.length)];
      flowParticles.push({
        fromX: phaseX,
        fromY: phaseY,
        toX: target.x,
        toY: target.y,
        t: 0,
        speed: 0.005 + Math.random() * 0.008,
        color: phases[phaseIdx].color,
      });
      if (flowParticles.length > 60) flowParticles.shift();
    }

    function spawnInterPhaseParticle(fromIdx: number, toIdx: number) {
      const fromX = w * ((fromIdx + 1) / (phases.length + 1));
      const toX = w * ((toIdx + 1) / (phases.length + 1));
      const y = h * 0.3;
      flowParticles.push({
        fromX, fromY: y,
        toX, toY: y,
        t: 0,
        speed: 0.01 + Math.random() * 0.015,
        color: phases[fromIdx].color,
      });
      if (flowParticles.length > 60) flowParticles.shift();
    }

    function spawnAmbientParticle() {
      particles.push({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.3,
        vy: (Math.random() - 0.5) * 0.3,
        life: 1,
        color: phases[Math.floor(Math.random() * 3)].color,
        size: 0.5 + Math.random() * 1.5,
      });
      if (particles.length > 40) particles.shift();
    }

    function resize() {
      const rect = canvas!.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      w = rect.width * dpr;
      h = rect.height * dpr;
      canvas!.width = w;
      canvas!.height = h;
      ctx!.scale(dpr, dpr);
      w = rect.width;
      h = rect.height;
      layout();
    }

    function draw() {
      if (!ctx) return;
      const c = ctx;
      c.clearRect(0, 0, w, h);
      const time = Date.now() * 0.001;

      // Draw ambient particles
      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];
        p.x += p.vx;
        p.y += p.vy;
        p.life -= 0.003;
        if (p.life <= 0 || p.x < 0 || p.x > w || p.y < 0 || p.y > h) {
          particles.splice(i, 1);
          continue;
        }
        c.globalAlpha = p.life * 0.3;
        c.beginPath();
        c.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        c.fillStyle = p.color;
        c.fill();
      }

      // Draw inter-phase connection lines (bold, animated dashes)
      for (let i = 0; i < phases.length - 1; i++) {
        const x1 = w * ((i + 1) / (phases.length + 1));
        const x2 = w * ((i + 2) / (phases.length + 1));
        const y = h * 0.3;

        // Glow line
        c.globalAlpha = 0.15;
        c.strokeStyle = phases[i].color;
        c.lineWidth = 6;
        c.setLineDash([12, 8]);
        c.lineDashOffset = -time * 30;
        c.beginPath();
        c.moveTo(x1 + 50, y);
        c.lineTo(x2 - 50, y);
        c.stroke();
        c.setLineDash([]);

        // Arrow
        const arrowX = (x1 + x2) / 2;
        c.globalAlpha = 0.5;
        c.fillStyle = phases[i].color;
        c.beginPath();
        c.moveTo(arrowX + 8, y);
        c.lineTo(arrowX - 4, y - 6);
        c.lineTo(arrowX - 4, y + 6);
        c.closePath();
        c.fill();
      }

      // Draw phase circles (big, bold)
      for (let i = 0; i < phases.length; i++) {
        const p = phases[i];
        const px = w * ((i + 1) / (phases.length + 1));
        const py = h * 0.3;
        const pulse = 0.7 + Math.sin(time * 1.5 + i * 2) * 0.3;
        const baseR = Math.min(w, h) * 0.1;

        // Outer glow
        c.globalAlpha = 0.08 * pulse;
        c.beginPath();
        c.arc(px, py, baseR * 2.5, 0, Math.PI * 2);
        c.fillStyle = p.color;
        c.fill();

        // Ring
        c.globalAlpha = 0.3;
        c.beginPath();
        c.arc(px, py, baseR * 1.4, 0, Math.PI * 2);
        c.strokeStyle = p.color;
        c.lineWidth = 2;
        c.stroke();

        // Inner circle
        c.globalAlpha = 0.15;
        c.beginPath();
        c.arc(px, py, baseR, 0, Math.PI * 2);
        c.fillStyle = p.color;
        c.fill();

        // Border
        c.globalAlpha = 0.6;
        c.beginPath();
        c.arc(px, py, baseR, 0, Math.PI * 2);
        c.strokeStyle = p.color;
        c.lineWidth = 2.5;
        c.stroke();

        // Label
        c.globalAlpha = 1;
        c.font = `bold ${Math.max(14, baseR * 0.35)}px monospace`;
        c.fillStyle = p.color;
        c.textAlign = "center";
        c.textBaseline = "middle";
        c.fillText(p.label, px, py - baseR * 0.15);

        // Sublabel
        c.globalAlpha = 0.5;
        c.font = `${Math.max(9, baseR * 0.18)}px monospace`;
        c.fillStyle = "#94a3b8";
        c.fillText(p.sublabel, px, py + baseR * 0.25);
      }

      // Draw connections from phases to server nodes (bottom area)
      for (let i = 0; i < phases.length; i++) {
        const px = w * ((i + 1) / (phases.length + 1));
        const py = h * 0.3;
        for (let j = 0; j < serverNodes.length; j++) {
          const s = serverNodes[j];
          const dx = s.x - px;
          const dy = s.y - py;
          const dist = Math.sqrt(dx * dx + dy * dy);
          const maxDist = Math.min(w, h) * 0.6;
          if (dist > maxDist) continue;

          c.globalAlpha = 0.06 + (1 - dist / maxDist) * 0.08;
          c.strokeStyle = phases[i].color;
          c.lineWidth = 0.8;
          c.setLineDash([3, 5]);
          c.lineDashOffset = -time * 15;
          c.beginPath();
          c.moveTo(px, py);
          c.lineTo(s.x, s.y);
          c.stroke();
          c.setLineDash([]);
        }
      }

      // Draw flow particles
      for (let i = flowParticles.length - 1; i >= 0; i--) {
        const fp = flowParticles[i];
        fp.t += fp.speed;
        if (fp.t >= 1) {
          flowParticles.splice(i, 1);
          continue;
        }
        const x = fp.fromX + (fp.toX - fp.fromX) * fp.t;
        const y = fp.fromY + (fp.toY - fp.fromY) * fp.t;
        const alpha = 1 - Math.abs(fp.t - 0.5) * 2;

        c.globalAlpha = alpha * 0.8;
        c.beginPath();
        c.arc(x, y, 2.5, 0, Math.PI * 2);
        c.fillStyle = fp.color;
        c.fill();

        c.globalAlpha = alpha * 0.2;
        c.beginPath();
        c.arc(x, y, 7, 0, Math.PI * 2);
        c.fillStyle = fp.color;
        c.fill();
      }

      // Draw server nodes
      for (let i = 0; i < serverNodes.length; i++) {
        const s = serverNodes[i];
        const pulse = 0.7 + Math.sin(time * 2 + i) * 0.3;

        // Outer glow
        c.globalAlpha = 0.1 * pulse;
        c.beginPath();
        c.arc(s.x, s.y, s.size * 2.5, 0, Math.PI * 2);
        c.fillStyle = s.color;
        c.fill();

        // Ring
        c.globalAlpha = 0.3;
        c.beginPath();
        c.arc(s.x, s.y, s.size * 1.4, 0, Math.PI * 2);
        c.strokeStyle = s.color;
        c.lineWidth = 1;
        c.stroke();

        // Body
        c.globalAlpha = 0.8;
        c.beginPath();
        c.arc(s.x, s.y, s.size, 0, Math.PI * 2);
        c.fillStyle = s.color;
        c.fill();

        // Center dot
        c.globalAlpha = 1;
        c.beginPath();
        c.arc(s.x, s.y, s.size * 0.25, 0, Math.PI * 2);
        c.fillStyle = "#fff";
        c.fill();

        // Label
        c.globalAlpha = 0.6;
        c.font = "bold 8px monospace";
        c.fillStyle = "#e2e8f0";
        c.textAlign = "center";
        c.fillText(s.label, s.x, s.y + s.size + 12);
      }

      c.globalAlpha = 1;

      // Spawn particles periodically
      if (Math.random() < 0.08) spawnFlowParticle(Math.floor(Math.random() * 3));
      if (Math.random() < 0.04) {
        const i = Math.floor(Math.random() * 2);
        spawnInterPhaseParticle(i, i + 1);
      }
      if (Math.random() < 0.1) spawnAmbientParticle();

      animId = requestAnimationFrame(draw);
    }

    resize();
    draw();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      cancelAnimationFrame(animId);
    };
  }, []);

  return (
    <div className="w-full rounded-2xl overflow-hidden border border-slate-200 dark:border-slate-800 bg-[#0a0f1a]">
      <div className="px-5 py-3 border-b border-slate-800 flex items-center gap-3">
        <div className="flex gap-1.5">
          <div className="w-3 h-3 rounded-full bg-red-500/80" />
          <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
          <div className="w-3 h-3 rounded-full bg-green-500/80" />
        </div>
        <span className="text-xs font-mono text-slate-500">
          grid deploy --ecosystem — Scan → Plan → Deploy Pipeline
        </span>
      </div>
      <canvas ref={canvasRef} className="w-full h-[380px] md:h-[440px]" />
      <div className="px-5 py-3 border-t border-slate-800 flex flex-wrap items-center justify-between gap-3 text-[11px] font-mono text-slate-500">
        <span className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse" />
          AI scans repos → detects frameworks → maps deps
        </span>
        <span className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-purple-400 animate-pulse" />
          Generates plan with env vars + dependency waves
        </span>
        <span className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
          Deploys addons first, then services in order
        </span>
      </div>
    </div>
  );
}
