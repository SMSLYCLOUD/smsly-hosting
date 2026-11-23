"use client";

import { useEffect, useRef } from "react";

interface PhaseNode {
  label: string;
  sublabel: string;
  color: string;
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
    let isVisible = true;

    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    let reducedMotion = mq.matches;

    const phases: PhaseNode[] = [
      { label: "SCAN", sublabel: "AI Framework Detection", color: "#3b82f6" },
      { label: "PLAN", sublabel: "Dependency Graph", color: "#8b5cf6" },
      { label: "DEPLOY", sublabel: "Wave Orchestration", color: "#10b981" },
    ];

    const serverNodes: { x: number; y: number; size: number; label: string; color: string }[] = [];
    const particles: { x: number; y: number; vx: number; vy: number; life: number; color: string; size: number }[] = [];
    const flowParticles: { fromX: number; fromY: number; toX: number; toY: number; t: number; speed: number; color: string }[] = [];

    function layout() {
      serverNodes.length = 0;
      const serverCount = 5;
      const serverSpacing = w / (serverCount + 1);
      const colors = ["#10b981", "#06b6d4", "#3b82f6", "#f59e0b", "#ec4899"];
      for (let i = 0; i < serverCount; i++) {
        const sx = serverSpacing * (i + 1);
        const sy = h * 0.8;
        const size = i === 0 ? 13 : 9 + (i % 3);
        serverNodes.push({
          x: sx,
          y: sy,
          size,
          label: i === 0 ? "master" : `node-${i}`,
          color: colors[i % colors.length],
        });
      }
    }

    function spawnFlowParticle(phaseIdx: number) {
      if (serverNodes.length === 0) return;
      const phaseX = w * ((phaseIdx + 1) / (phases.length + 1));
      const phaseY = h * 0.32;
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
      const y = h * 0.32;
      flowParticles.push({
        fromX,
        fromY: y,
        toX,
        toY: y,
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

    function renderFrame(animated: boolean) {
      if (!ctx || w <= 0 || h <= 0) return;
      const c = ctx;
      c.clearRect(0, 0, w, h);
      const time = animated ? Date.now() * 0.001 : 0;

      if (animated) {
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
      }

      const baseR = Math.min(w * 0.085, h * 0.11);

      // Inter-phase connections (bounded cleanly so narrow mobile screens never invert)
      for (let i = 0; i < phases.length - 1; i++) {
        const x1 = w * ((i + 1) / (phases.length + 1));
        const x2 = w * ((i + 2) / (phases.length + 1));
        const y = h * 0.32;
        const gap = Math.min(50, baseR * 1.35);

        if (x2 - gap > x1 + gap + 6) {
          c.globalAlpha = 0.28;
          c.strokeStyle = phases[i].color;
          c.lineWidth = 4;
          c.setLineDash([10, 8]);
          c.lineDashOffset = animated ? -time * 28 : 0;
          c.beginPath();
          c.moveTo(x1 + gap, y);
          c.lineTo(x2 - gap, y);
          c.stroke();
          c.setLineDash([]);

          const arrowX = (x1 + x2) / 2;
          c.globalAlpha = 0.65;
          c.fillStyle = phases[i].color;
          c.beginPath();
          c.moveTo(arrowX + 7, y);
          c.lineTo(arrowX - 4, y - 5);
          c.lineTo(arrowX - 4, y + 5);
          c.closePath();
          c.fill();
        }
      }

      // Phase circles
      const showSublabels = w >= 540;
      for (let i = 0; i < phases.length; i++) {
        const p = phases[i];
        const px = w * ((i + 1) / (phases.length + 1));
        const py = h * 0.32;
        const pulse = animated ? 0.75 + Math.sin(time * 1.5 + i * 2) * 0.25 : 0.85;

        c.globalAlpha = 0.08 * pulse;
        c.beginPath();
        c.arc(px, py, baseR * 2.1, 0, Math.PI * 2);
        c.fillStyle = p.color;
        c.fill();

        c.globalAlpha = 0.32;
        c.beginPath();
        c.arc(px, py, baseR * 1.3, 0, Math.PI * 2);
        c.strokeStyle = p.color;
        c.lineWidth = 1.8;
        c.stroke();

        c.globalAlpha = 0.16;
        c.beginPath();
        c.arc(px, py, baseR, 0, Math.PI * 2);
        c.fillStyle = p.color;
        c.fill();

        c.globalAlpha = 0.7;
        c.beginPath();
        c.arc(px, py, baseR, 0, Math.PI * 2);
        c.strokeStyle = p.color;
        c.lineWidth = 2;
        c.stroke();

        c.globalAlpha = 1;
        c.font = `bold ${Math.max(12, Math.min(18, baseR * 0.34))}px monospace`;
        c.fillStyle = "#f8fafc";
        c.textAlign = "center";
        c.textBaseline = "middle";
        c.fillText(p.label, px, showSublabels ? py - baseR * 0.14 : py);

        if (showSublabels) {
          c.globalAlpha = 0.88;
          c.font = `11px monospace`;
          c.fillStyle = "#cbd5e1";
          c.fillText(p.sublabel, px, py + baseR * 0.28);
        }
      }

      // Connections to target nodes
      for (let i = 0; i < phases.length; i++) {
        const px = w * ((i + 1) / (phases.length + 1));
        const py = h * 0.32 + baseR;
        for (let j = 0; j < serverNodes.length; j++) {
          const s = serverNodes[j];
          const dx = s.x - px;
          const dy = s.y - py;
          const dist = Math.sqrt(dx * dx + dy * dy);
          const maxDist = Math.max(w, h) * 0.65;
          if (dist > maxDist) continue;

          c.globalAlpha = 0.1 + (1 - dist / maxDist) * 0.12;
          c.strokeStyle = phases[i].color;
          c.lineWidth = 1;
          c.setLineDash([3, 5]);
          c.lineDashOffset = animated ? -time * 15 : 0;
          c.beginPath();
          c.moveTo(px, py);
          c.lineTo(s.x, s.y);
          c.stroke();
          c.setLineDash([]);
        }
      }

      if (animated) {
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
          c.globalAlpha = alpha * 0.85;
          c.beginPath();
          c.arc(x, y, 2.5, 0, Math.PI * 2);
          c.fillStyle = fp.color;
          c.fill();
        }
      }

      // Draw server nodes
      for (let i = 0; i < serverNodes.length; i++) {
        const s = serverNodes[i];
        const pulse = animated ? 0.75 + Math.sin(time * 2 + i) * 0.25 : 0.85;

        c.globalAlpha = 0.12 * pulse;
        c.beginPath();
        c.arc(s.x, s.y, s.size * 2.2, 0, Math.PI * 2);
        c.fillStyle = s.color;
        c.fill();

        c.globalAlpha = 0.85;
        c.beginPath();
        c.arc(s.x, s.y, s.size, 0, Math.PI * 2);
        c.fillStyle = s.color;
        c.fill();

        c.globalAlpha = 1;
        c.beginPath();
        c.arc(s.x, s.y, s.size * 0.25, 0, Math.PI * 2);
        c.fillStyle = "#fff";
        c.fill();

        c.globalAlpha = 0.9;
        c.font = "bold 11px monospace";
        c.fillStyle = "#e2e8f0";
        c.textAlign = "center";
        c.fillText(s.label, s.x, s.y + s.size + 14);
      }

      c.globalAlpha = 1;
    }

    function tick() {
      if (reducedMotion || !isVisible || document.visibilityState !== "visible") {
        animId = 0;
        return;
      }
      renderFrame(true);
      if (Math.random() < 0.08) spawnFlowParticle(Math.floor(Math.random() * 3));
      if (Math.random() < 0.04) {
        const i = Math.floor(Math.random() * 2);
        spawnInterPhaseParticle(i, i + 1);
      }
      if (Math.random() < 0.1) spawnAmbientParticle();
      animId = requestAnimationFrame(tick);
    }

    function startLoop() {
      if (reducedMotion) {
        renderFrame(false);
        return;
      }
      if (!animId && isVisible && document.visibilityState === "visible") {
        animId = requestAnimationFrame(tick);
      }
    }

    function stopLoop() {
      if (animId) {
        cancelAnimationFrame(animId);
        animId = 0;
      }
    }

    function resize() {
      const rect = canvas!.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = rect.width;
      h = rect.height;
      canvas!.width = Math.floor(rect.width * dpr);
      canvas!.height = Math.floor(rect.height * dpr);
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      layout();
      renderFrame(!reducedMotion);
    }

    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    const io = new IntersectionObserver(
      (entries) => {
        isVisible = entries[0]?.isIntersecting ?? true;
        if (isVisible) startLoop();
        else stopLoop();
      },
      { threshold: 0.05 },
    );
    io.observe(canvas);

    const onVisChange = () => {
      if (document.visibilityState === "visible") startLoop();
      else stopLoop();
    };
    const onMotionChange = (e: MediaQueryListEvent) => {
      reducedMotion = e.matches;
      stopLoop();
      startLoop();
    };

    document.addEventListener("visibilitychange", onVisChange);
    mq.addEventListener("change", onMotionChange);
    resize();
    startLoop();

    return () => {
      ro.disconnect();
      io.disconnect();
      document.removeEventListener("visibilitychange", onVisChange);
      mq.removeEventListener("change", onMotionChange);
      stopLoop();
    };
  }, []);

  return (
    <div className="w-full rounded-2xl overflow-hidden border border-slate-800 bg-[#0a0f1a]">
      <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between gap-3">
        <span className="text-xs font-mono text-slate-300">
          grid deploy --ecosystem
        </span>
        <span className="text-xs font-mono text-emerald-400">
          Scan → Plan → Deploy Pipeline
        </span>
      </div>
      <canvas
        ref={canvasRef}
        role="img"
        aria-label="Ecosystem deploy pipeline: Scan detects frameworks, Plan maps dependencies, and Deploy orchestrates wave rollouts across cluster nodes"
        className="w-full h-[320px] sm:h-[360px] md:h-[420px] block"
      />
      <div className="px-5 py-3 border-t border-slate-800 flex flex-wrap items-center justify-between gap-3 text-xs font-mono text-slate-300">
        <span className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse motion-reduce:animate-none" aria-hidden="true" />
          AI scans repos → detects frameworks → maps deps
        </span>
        <span className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-purple-400 animate-pulse motion-reduce:animate-none" aria-hidden="true" />
          Generates plan with env vars + dependency waves
        </span>
        <span className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse motion-reduce:animate-none" aria-hidden="true" />
          Deploys addons first, then services in order
        </span>
      </div>
    </div>
  );
}
