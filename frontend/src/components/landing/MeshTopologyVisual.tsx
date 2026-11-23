"use client";

import { useEffect, useRef } from "react";

export function MeshTopologyVisual() {
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

    const nodes = [
      { label: "Master", region: "Frankfurt", color: "#10b981", tier: "control" as const },
      { label: "EU-West", region: "Berlin", color: "#3b82f6", tier: "worker" as const },
      { label: "EU-East", region: "Warsaw", color: "#6366f1", tier: "worker" as const },
      { label: "US-East", region: "NYC", color: "#f59e0b", tier: "worker" as const },
      { label: "US-West", region: "SF", color: "#06b6d4", tier: "worker" as const },
      { label: "Asia", region: "Singapore", color: "#a855f7", tier: "worker" as const },
      { label: "Oceania", region: "Sydney", color: "#ec4899", tier: "worker" as const },
      { label: "S-Am", region: "Sao Paulo", color: "#f97316", tier: "worker" as const },
    ];

    const positions: { x: number; y: number }[] = [];
    const packets: { from: number; to: number; t: number; speed: number; color: string }[] = [];

    function layout() {
      positions.length = 0;
      const cx = w / 2;
      const cy = h / 2;
      const rx = w * 0.36;
      const ry = h * 0.34;
      positions.push({ x: cx, y: cy - ry * 0.8 });
      for (let i = 1; i < nodes.length; i++) {
        const angle = ((i - 1) / (nodes.length - 1)) * Math.PI * 2 - Math.PI / 2;
        positions.push({
          x: cx + Math.cos(angle) * rx,
          y: cy + Math.sin(angle) * ry * 0.9 + ry * 0.12,
        });
      }
    }

    function spawnPacket() {
      const from = Math.floor(Math.random() * nodes.length);
      let to = from;
      while (to === from) to = Math.floor(Math.random() * nodes.length);
      packets.push({
        from,
        to,
        t: 0,
        speed: 0.006 + Math.random() * 0.01,
        color: nodes[from].color,
      });
      if (packets.length > 40) packets.shift();
    }

    function renderFrame(animated: boolean) {
      if (!ctx || w <= 0 || h <= 0 || positions.length === 0) return;
      const c = ctx;
      c.clearRect(0, 0, w, h);
      const time = animated ? Date.now() * 0.001 : 0;

      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = positions[i];
          const b = positions[j];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          const maxDist = Math.max(w, h) * 0.72;
          if (dist > maxDist) continue;

          const alpha = 0.14 + (1 - dist / maxDist) * 0.2;
          c.strokeStyle = `rgba(16,185,129,${alpha})`;
          c.lineWidth = 1.2;
          c.setLineDash([6, 4]);
          c.lineDashOffset = animated ? -time * 25 : 0;
          c.beginPath();
          c.moveTo(a.x, a.y);
          c.lineTo(b.x, b.y);
          c.stroke();
          c.setLineDash([]);
        }
      }

      if (animated) {
        for (let i = packets.length - 1; i >= 0; i--) {
          const p = packets[i];
          p.t += p.speed;
          if (p.t >= 1) {
            packets.splice(i, 1);
            continue;
          }
          const a = positions[p.from];
          const b = positions[p.to];
          const x = a.x + (b.x - a.x) * p.t;
          const y = a.y + (b.y - a.y) * p.t;
          c.globalAlpha = 1 - p.t * 0.5;
          c.beginPath();
          c.arc(x, y, 3, 0, Math.PI * 2);
          c.fillStyle = p.color;
          c.fill();
        }
      }

      const showRegion = w >= 480;
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        const p = positions[i];
        const pulse = animated ? 0.75 + Math.sin(time * 2 + i) * 0.25 : 0.85;
        const r = n.tier === "control" ? 14 : 9;

        c.globalAlpha = 0.12 * pulse;
        c.beginPath();
        c.arc(p.x, p.y, r * 2.6, 0, Math.PI * 2);
        c.fillStyle = n.color;
        c.fill();

        c.globalAlpha = 0.9;
        c.beginPath();
        c.arc(p.x, p.y, r, 0, Math.PI * 2);
        c.fillStyle = n.color;
        c.fill();

        c.globalAlpha = 1;
        c.beginPath();
        c.arc(p.x, p.y, r * 0.3, 0, Math.PI * 2);
        c.fillStyle = "#fff";
        c.fill();

        c.globalAlpha = 0.95;
        c.font = "bold 11px monospace";
        c.fillStyle = "#f1f5f9";
        c.textAlign = "center";
        c.fillText(n.label, p.x, p.y + r + 14);

        if (showRegion) {
          c.globalAlpha = 0.82;
          c.font = "11px monospace";
          c.fillStyle = "#cbd5e1";
          c.fillText(n.region, p.x, p.y + r + 28);
        }
      }

      c.globalAlpha = 1;
    }

    function tick() {
      if (reducedMotion || !isVisible || document.visibilityState !== "visible") {
        animId = 0;
        return;
      }
      renderFrame(true);
      if (Math.random() < 0.18) spawnPacket();
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
      <div className="px-5 py-3 border-b border-slate-800 flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-mono text-slate-300">
          WireGuard VPN Mesh Topology
        </span>
        <span className="text-xs font-mono text-emerald-400">
          8 Nodes · 28 Encrypted Links · Multi-Region
        </span>
      </div>
      <canvas
        ref={canvasRef}
        role="img"
        aria-label="WireGuard VPN mesh connecting Master Frankfurt node and 7 regional worker nodes across EU, US, Asia, Oceania, and South America"
        className="w-full h-[300px] sm:h-[340px] md:h-[400px] block"
      />
      <div className="px-5 py-3 border-t border-slate-800 flex flex-wrap items-center justify-between gap-3 text-xs font-mono text-slate-300">
        <span className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse motion-reduce:animate-none" aria-hidden="true" />
          All traffic encrypted · WireGuard tunnel
        </span>
        <span className="tabular-nums">Avg latency: 12ms · 0 packet loss · 99.999% uptime</span>
      </div>
    </div>
  );
}
