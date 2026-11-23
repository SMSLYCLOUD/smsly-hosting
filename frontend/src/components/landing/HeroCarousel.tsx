"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  Check,
  Copy,
  KeyRound,
  Lock,
  Pause,
  Play,
  Server,
  Shield,
  ShieldCheck,
  Terminal,
  Unlock,
} from "lucide-react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";

const heroSlides = [
  {
    id: "sovereign-paas",
    code: "01 / SOVEREIGN CONTROL PLANE",
    badge: "MIT Open-Source Sovereign PaaS",
    lead: "Hyperscaler capabilities on ",
    accent: "hardware you actually own.",
    subtitle:
      "Turn any VPS or bare-metal fleet into a self-healing cloud. 5 deployment engines, Patroni PostgreSQL HA, Redis Sentinel, WireGuard mesh, SPIFFE mTLS, and 40+ data addons—with zero per-seat tax.",
    consoleTab: "cluster" as const,
  },
  {
    id: "cryptd-cnapp",
    code: "02 / CRYPTOGRAPHIC VAULT & CNAPP",
    badge: "Opt-In grid-cryptd + 11-Layer Defense",
    lead: "Zero-leak encryption & ",
    accent: "autonomous runtime security.",
    subtitle:
      "Opt-in RAM-only mlock() DEK keystore backed by your external Root Vault, PostgreSQL & Redis wire gates (:15432/:16379), LUKS2 encrypted volumes, ASPM secret/IaC/SAST gates, CycloneDX SBOMs, and Falco eBPF XDR.",
    consoleTab: "cryptd" as const,
  },
  {
    id: "mesh-ha",
    code: "03 / MULTI-SERVER MESH & SCALE-TO-ZERO",
    badge: "WireGuard + CoreDNS + SPIRE mTLS",
    lead: "One control plane across ",
    accent: "every region and provider.",
    subtitle:
      "Encrypted WireGuard mesh with split-horizon CoreDNS, Envoy SPIFFE mTLS sidecars, Caddy + Traefik dual-edge TLS, built-in FRP private tunnels, Sablier scale-to-zero sleeping containers, and live cross-server transfers.",
    consoleTab: "cluster" as const,
  },
  {
    id: "ai-senate-mcp",
    code: "04 / AI SENATE, JULES & MCP SERVER",
    badge: "17+ LLM Senate + Native MCP Server",
    lead: "Infrastructure that diagnoses ",
    accent: "and repairs itself.",
    subtitle:
      "Multi-model AI Senate consensus (Propose → Review → Synthesize), Loki Log Bridge crash correlation, Google Jules automated fix PRs, 3-engine predictive autoscaling, and a built-in MCP server for AI coding agents.",
    consoleTab: "cnapp" as const,
  },
];

type ConsoleTab = "cluster" | "cryptd" | "cnapp";

export function HeroCarousel() {
  const [current, setCurrent] = useState(0);
  const [consoleTab, setConsoleTab] = useState<ConsoleTab>("cluster");
  const [userSelectedConsoleTab, setUserSelectedConsoleTab] = useState(false);
  const [vaultLockedSim, setVaultLockedSim] = useState(false);
  const [copiedCmd, setCopiedCmd] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const [isUserPaused, setIsUserPaused] = useState(false);
  const prefersReducedMotion = useReducedMotion();

  const paused = Boolean(isHovered || isFocused || isUserPaused || prefersReducedMotion);

  useEffect(() => {
    if (paused) return;
    const timer = setInterval(() => {
      setCurrent((prev) => {
        const next = (prev + 1) % heroSlides.length;
        if (!userSelectedConsoleTab) {
          setConsoleTab(heroSlides[next].consoleTab);
        }
        return next;
      });
    }, 7500);
    return () => clearInterval(timer);
  }, [paused, userSelectedConsoleTab]);

  const slide = heroSlides[current];

  const handleSelectSlide = (idx: number) => {
    setCurrent(idx);
    if (!userSelectedConsoleTab) {
      setConsoleTab(heroSlides[idx].consoleTab);
    }
  };

  const handleCopyInstall = () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText("pip install smsly-cli && smsly init").catch(() => {});
      setCopiedCmd(true);
      setTimeout(() => setCopiedCmd(false), 2000);
    }
  };

  return (
    <section
      aria-label="Platform highlights and live architecture console"
      className="relative pt-10 pb-16 md:pt-16 md:pb-24 border-b border-slate-200 dark:border-slate-800/90 overflow-hidden"
    >
      {/* Subtle architectural coordinate grid backdrop */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_right,rgba(148,163,184,0.08)_1px,transparent_1px),linear-gradient(to_bottom,rgba(148,163,184,0.08)_1px,transparent_1px)] bg-[size:48px_48px]"
      />

      <div
        className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 z-10"
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        onFocus={() => setIsFocused(true)}
        onBlur={() => setIsFocused(false)}
      >
        {/* Top System Telemetry Strip */}
        <div className="flex flex-wrap items-center justify-between gap-3 pb-6 mb-8 border-b border-slate-200/80 dark:border-slate-800/80 text-xs font-mono">
          <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            <span className="inline-flex items-center gap-2 px-2.5 py-1 rounded-md bg-emerald-500/10 border border-emerald-500/30 text-emerald-800 dark:text-emerald-300 font-semibold">
              <span
                className="h-2 w-2 rounded-full bg-emerald-600 dark:bg-emerald-400 animate-pulse motion-reduce:animate-none"
                aria-hidden="true"
              />
              TRULAY GRID v2.0 · MIT SOVEREIGN PAAS
            </span>
            <span className="hidden sm:inline text-slate-400 dark:text-slate-600">|</span>
            <span className="text-slate-600 dark:text-slate-400">
              {slide.code}
            </span>
          </div>

          <div className="flex items-center gap-3 text-slate-600 dark:text-slate-400">
            <Link
              href="/status"
              className="inline-flex items-center gap-1.5 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors"
            >
              <Activity className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
              <span>Live Telemetry</span>
            </Link>
            <span className="text-slate-300 dark:text-slate-700">·</span>
            <Link
              href="/docs/security"
              className="inline-flex items-center gap-1.5 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors"
            >
              <Shield className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
              <span>11 Defense Layers</span>
            </Link>
          </div>
        </div>

        {/* Main Asymmetric Split Layout: 7-col Editorial Spec + 5-col Live Hardware Console */}
        <div className="grid lg:grid-cols-12 gap-10 lg:gap-12 items-start">
          {/* LEFT 7 COLUMNS: Asymmetric Editorial Narrative & Controls */}
          <div className="lg:col-span-7 text-left">
            {/* Mode Selector Pills (>=44px touch height) */}
            <div className="flex flex-wrap items-center gap-1.5 mb-6" role="group" aria-label="Architecture focus">
              {heroSlides.map((item, i) => {
                const active = i === current;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => handleSelectSlide(i)}
                    aria-label={`Show highlight ${i + 1}: ${item.badge}`}
                    aria-current={active ? "true" : undefined}
                    className={`inline-flex items-center gap-2 min-h-[44px] px-3.5 py-2 rounded-lg text-xs font-mono font-semibold transition-all border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                      active
                        ? "bg-slate-900 text-white border-slate-900 dark:bg-emerald-500/15 dark:text-emerald-300 dark:border-emerald-500/40"
                        : "bg-slate-100/80 text-slate-700 border-slate-200 hover:border-slate-400 dark:bg-slate-900/60 dark:text-slate-300 dark:border-slate-800 dark:hover:border-slate-700"
                    }`}
                  >
                    <span
                      className={`h-1.5 w-1.5 rounded-full ${
                        active ? "bg-emerald-400" : "bg-slate-400 dark:bg-slate-600"
                      }`}
                      aria-hidden="true"
                    />
                    <span>{item.badge}</span>
                  </button>
                );
              })}

              <button
                type="button"
                onClick={() => setIsUserPaused((p) => !p)}
                aria-label={isUserPaused ? "Resume highlight rotation" : "Pause highlight rotation"}
                aria-pressed={isUserPaused}
                className="inline-flex items-center justify-center min-w-[44px] min-h-[44px] rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-100/70 dark:bg-slate-900/60 text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {isUserPaused ? (
                  <Play className="w-4 h-4" aria-hidden="true" />
                ) : (
                  <Pause className="w-4 h-4" aria-hidden="true" />
                )}
              </button>
            </div>

            {/* Primary H1 */}
            <h1 className="text-4xl sm:text-5xl lg:text-[3.4rem] font-extrabold text-slate-950 dark:text-white tracking-tight leading-[1.08] mb-5">
              <AnimatePresence mode="wait">
                <motion.span
                  key={slide.id}
                  initial={prefersReducedMotion ? false : { opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, y: -10 }}
                  transition={{ duration: 0.22 }}
                  className="block"
                >
                  {slide.lead}
                  <span className="text-emerald-700 dark:text-emerald-400 underline decoration-emerald-500/35 decoration-2 underline-offset-8">
                    {slide.accent}
                  </span>
                </motion.span>
              </AnimatePresence>
            </h1>

            <p className="text-base sm:text-lg text-slate-600 dark:text-slate-300 max-w-2xl leading-relaxed font-medium mb-8">
              {slide.subtitle}
            </p>

            {/* Primary Actions + Copyable CLI Command */}
            <div className="flex flex-col sm:flex-row flex-wrap items-stretch sm:items-center gap-3.5 mb-8">
              <Link
                href="/register"
                className="inline-flex items-center justify-center gap-2 min-h-[48px] px-7 py-3.5 text-base font-bold text-white bg-emerald-700 hover:bg-emerald-800 rounded-xl transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Deploy Your Cluster <ArrowRight className="w-4 h-4" aria-hidden="true" />
              </Link>

              <Link
                href="#features"
                className="inline-flex items-center justify-center gap-2 min-h-[48px] px-6 py-3.5 text-base font-bold text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-xl hover:border-emerald-600 dark:hover:border-emerald-500 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Explore 7 Capability Domains
              </Link>

              <button
                type="button"
                onClick={handleCopyInstall}
                aria-label="Copy smsly CLI install command"
                className="inline-flex items-center justify-between gap-3 min-h-[48px] px-4 py-3 rounded-xl bg-slate-900 dark:bg-slate-900/90 text-slate-200 border border-slate-800 hover:border-emerald-500/50 font-mono text-xs sm:text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="flex items-center gap-2 truncate">
                  <Terminal className="w-4 h-4 text-emerald-400 shrink-0" aria-hidden="true" />
                  <span className="truncate">pip install smsly-cli &amp;&amp; smsly init</span>
                </span>
                {copiedCmd ? (
                  <span className="inline-flex items-center gap-1 text-emerald-400 text-xs font-semibold shrink-0">
                    <Check className="w-3.5 h-3.5" aria-hidden="true" /> Copied
                  </span>
                ) : (
                  <Copy className="w-3.5 h-3.5 text-slate-400 shrink-0" aria-hidden="true" />
                )}
              </button>
            </div>

            {/* Architectural Capability Ledger (Tabular Metrics + Direct Proof) */}
            <div className="grid grid-cols-2 sm:grid-cols-4 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden bg-white/80 dark:bg-slate-900/60 divide-y sm:divide-y-0 sm:divide-x divide-slate-200 dark:divide-slate-800">
              {[
                {
                  metric: "5 Engines",
                  label: "Workload Modes",
                  sub: "Git · Docker · Compose · Upload · Functions",
                },
                {
                  metric: "40+ Addons",
                  label: "Managed Data Plane",
                  sub: "Patroni PG · Sentinel · Qdrant · Kafka",
                },
                {
                  metric: "grid-cryptd",
                  label: "Opt-In Zero-Leak",
                  sub: "RAM DEKs · Wire Gates · LUKS2 · SOPS",
                },
                {
                  metric: "17+ LLMs",
                  label: "AI Senate + MCP",
                  sub: "Jules Auto-PR · OCSF XDR · /mcp Server",
                },
              ].map((cell) => (
                <div key={cell.label} className="p-3.5 sm:p-4">
                  <div className="text-lg sm:text-xl font-extrabold font-mono text-emerald-700 dark:text-emerald-400 tabular-nums">
                    {cell.metric}
                  </div>
                  <div className="text-xs font-bold text-slate-900 dark:text-white mt-0.5">
                    {cell.label}
                  </div>
                  <div className="text-xs text-slate-600 dark:text-slate-400 mt-0.5 leading-snug">
                    {cell.sub}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* RIGHT 5 COLUMNS: Interactive Live Control Plane Console */}
          <div className="lg:col-span-5 w-full">
            <div className="rounded-2xl border border-slate-800 bg-[#0a0f1d] text-slate-100 shadow-2xl overflow-hidden">
              {/* Console Top Bar + Interactive Tab Switcher */}
              <div className="px-4 py-3 bg-slate-900/95 border-b border-slate-800 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" aria-hidden="true" />
                  <span className="text-xs font-mono font-bold text-slate-200">
                    grid-control-plane://eu-central-1
                  </span>
                </div>
                <span className="text-xs font-mono text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 rounded">
                  LIVE TELEMETRY
                </span>
              </div>

              {/* Console Mode Tabs */}
              <div
                className="grid grid-cols-3 border-b border-slate-800 bg-slate-950/80 text-xs font-mono"
                role="tablist"
                aria-label="Control plane telemetry views"
              >
                {[
                  { id: "cluster" as const, label: "1. Cluster & HA", icon: Server },
                  { id: "cryptd" as const, label: "2. grid-cryptd", icon: KeyRound },
                  { id: "cnapp" as const, label: "3. CNAPP & AI", icon: ShieldCheck },
                ].map((tab) => {
                  const active = consoleTab === tab.id;
                  return (
                    <button
                      key={tab.id}
                      type="button"
                      role="tab"
                      aria-selected={active}
                      onClick={() => {
                        setConsoleTab(tab.id);
                        setUserSelectedConsoleTab(true);
                      }}
                      className={`flex items-center justify-center gap-1.5 min-h-[44px] px-2 py-2.5 font-semibold border-b-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                        active
                          ? "border-emerald-400 text-emerald-300 bg-emerald-500/10"
                          : "border-transparent text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      <tab.icon className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                      <span className="truncate">{tab.label}</span>
                    </button>
                  );
                })}
              </div>

              {/* TAB 1: CLUSTER, HA DATABASES, MESH & SCALE-TO-ZERO */}
              {consoleTab === "cluster" && (
                <div className="p-4 sm:p-5 space-y-4 font-mono text-xs">
                  <div className="grid grid-cols-3 gap-2">
                    <div className="p-2.5 rounded-lg bg-slate-900/90 border border-slate-800">
                      <div className="text-slate-400">PostgreSQL HA</div>
                      <div className="text-sm font-bold text-white mt-0.5 tabular-nums">0.2ms lag</div>
                      <div className="text-emerald-400 mt-0.5">Patroni + PgCat</div>
                    </div>
                    <div className="p-2.5 rounded-lg bg-slate-900/90 border border-slate-800">
                      <div className="text-slate-400">Redis Sentinel</div>
                      <div className="text-sm font-bold text-white mt-0.5 tabular-nums">3/3 Quorum</div>
                      <div className="text-emerald-400 mt-0.5">Auto-failover</div>
                    </div>
                    <div className="p-2.5 rounded-lg bg-slate-900/90 border border-slate-800">
                      <div className="text-slate-400">WireGuard + SPIRE</div>
                      <div className="text-sm font-bold text-white mt-0.5 tabular-nums">8 Peers</div>
                      <div className="text-emerald-400 mt-0.5">mTLS SVIDs OK</div>
                    </div>
                  </div>

                  <div className="space-y-2">
                    {[
                      {
                        name: "api-gateway",
                        runtime: "runc + Envoy mTLS",
                        route: "api.trulay.cloud",
                        state: "HEALTHY",
                        cpu: "18%",
                        ram: "640MB",
                      },
                      {
                        name: "payments-worker",
                        runtime: "gVisor (runsc)",
                        route: "payments.mesh.local",
                        state: "ENCRYPTED",
                        cpu: "31%",
                        ram: "1.1GB",
                      },
                      {
                        name: "nextjs-storefront",
                        runtime: "gVisor + Blue-Green",
                        route: "shop.trulay.cloud",
                        state: "v48 LIVE",
                        cpu: "11%",
                        ram: "420MB",
                      },
                      {
                        name: "preview-pr-142",
                        runtime: "Sablier Scale-to-Zero",
                        route: "pr-142.preview.cloud",
                        state: "SLEEP (0MB)",
                        cpu: "0%",
                        ram: "wake <180ms",
                      },
                    ].map((svc) => (
                      <div
                        key={svc.name}
                        className="p-3 rounded-xl bg-slate-900/75 border border-slate-800/90 flex items-center justify-between gap-2"
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="h-2 w-2 rounded-full bg-emerald-400 shrink-0" aria-hidden="true" />
                            <span className="font-bold text-white truncate">{svc.name}</span>
                            <span className="px-1.5 py-0.5 rounded bg-slate-800 text-emerald-300 text-xs">
                              {svc.state}
                            </span>
                          </div>
                          <div className="text-slate-400 truncate mt-1">
                            {svc.runtime} · {svc.route}
                          </div>
                        </div>
                        <div className="text-right shrink-0 tabular-nums">
                          <div className="text-slate-200">{svc.ram}</div>
                          <div className="text-slate-400">{svc.cpu} CPU</div>
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="pt-2 border-t border-slate-800 flex flex-wrap items-center justify-between gap-2 text-slate-400">
                    <span>Caddy + Traefik Dual Edge · FRP Tunnels Ready</span>
                    <span className="text-emerald-400">PgBouncer Tenants: Pooled</span>
                  </div>
                </div>
              )}

              {/* TAB 2: OPT-IN GRID-CRYPTD & EXTERNAL ROOT VAULT */}
              {consoleTab === "cryptd" && (
                <div className="p-4 sm:p-5 space-y-4 font-mono text-xs">
                  <div className="p-3 rounded-xl bg-slate-900 border border-slate-800 flex items-center justify-between gap-3">
                    <div>
                      <div className="text-slate-400">External Root Vault Lease</div>
                      <div className="text-sm font-bold text-white mt-0.5 flex items-center gap-2">
                        {vaultLockedSim ? (
                          <>
                            <Lock className="w-4 h-4 text-rose-400" aria-hidden="true" />
                            <span className="text-rose-400">REVOKED · FAIL-CLOSED</span>
                          </>
                        ) : (
                          <>
                            <Unlock className="w-4 h-4 text-emerald-400" aria-hidden="true" />
                            <span className="text-emerald-400">UNLOCKED · EPOCH #42</span>
                          </>
                        )}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setVaultLockedSim((v) => !v)}
                      className={`min-h-[44px] px-3 py-2 rounded-lg font-bold border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                        vaultLockedSim
                          ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-300 hover:bg-emerald-500/25"
                          : "bg-rose-500/15 border-rose-500/40 text-rose-300 hover:bg-rose-500/25"
                      }`}
                    >
                      {vaultLockedSim ? "Restore Vault Lease" : "Simulate Lease Revoke"}
                    </button>
                  </div>

                  <div className="space-y-2">
                    {[
                      {
                        layer: "RAM Keystore (mlock)",
                        spec: "HKDF-SHA256: Project → Service → Table DEK",
                        state: vaultLockedSim ? "ZEROED IN RAM" : "ACTIVE (0 disk bytes)",
                        ok: !vaultLockedSim,
                      },
                      {
                        layer: "PostgreSQL Wire Gate (:15432)",
                        spec: "Point-of-contact lease check + AEAD envelope",
                        state: vaultLockedSim ? "CONNECTIONS SEVERED" : "PROXYING → PgCat :6432",
                        ok: !vaultLockedSim,
                      },
                      {
                        layer: "Redis Wire Gate (:16379)",
                        spec: "RESP command gate + opt-in payload encryption",
                        state: vaultLockedSim ? "CONNECTIONS SEVERED" : "PROXYING → Sentinel :6379",
                        ok: !vaultLockedSim,
                      },
                      {
                        layer: "LUKS2 Volume & SOPS+age",
                        spec: "DM-Crypt loop volumes + per-service age keys",
                        state: vaultLockedSim ? "VOLUME LOCKED" : "MOUNTED (aes-xts-plain64)",
                        ok: !vaultLockedSim,
                      },
                    ].map((row) => (
                      <div
                        key={row.layer}
                        className="p-3 rounded-xl bg-slate-900/75 border border-slate-800 flex items-center justify-between gap-2"
                      >
                        <div className="min-w-0">
                          <div className="font-bold text-white truncate">{row.layer}</div>
                          <div className="text-slate-400 truncate mt-0.5">{row.spec}</div>
                        </div>
                        <span
                          className={`px-2 py-1 rounded text-xs font-bold shrink-0 ${
                            row.ok
                              ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                              : "bg-rose-500/15 text-rose-300 border border-rose-500/30"
                          }`}
                        >
                          {row.state}
                        </span>
                      </div>
                    ))}
                  </div>

                  <div className="pt-2 border-t border-slate-800 text-slate-400 flex items-center justify-between">
                    <span>100% Opt-In per service or addon</span>
                    <span className="text-emerald-400">ChaCha20-Poly1305 / AES-256-GCM</span>
                  </div>
                </div>
              )}

              {/* TAB 3: CNAPP, ASPM, XDR, AI SENATE & MCP SERVER */}
              {consoleTab === "cnapp" && (
                <div className="p-4 sm:p-5 space-y-4 font-mono text-xs">
                  <div className="grid grid-cols-3 gap-2">
                    <div className="p-2.5 rounded-lg bg-slate-900/90 border border-slate-800">
                      <div className="text-slate-400">ASPM + SBOM</div>
                      <div className="text-sm font-bold text-white mt-0.5">SLSA v1.0</div>
                      <div className="text-emerald-400 mt-0.5">CycloneDX + Cosign</div>
                    </div>
                    <div className="p-2.5 rounded-lg bg-slate-900/90 border border-slate-800">
                      <div className="text-slate-400">Compliance</div>
                      <div className="text-sm font-bold text-white mt-0.5 tabular-nums">5 Frameworks</div>
                      <div className="text-emerald-400 mt-0.5">SOC2 · ISO · HIPAA</div>
                    </div>
                    <div className="p-2.5 rounded-lg bg-slate-900/90 border border-slate-800">
                      <div className="text-slate-400">AI Senate + MCP</div>
                      <div className="text-sm font-bold text-white mt-0.5 tabular-nums">17+ LLMs</div>
                      <div className="text-emerald-400 mt-0.5">Jules Auto-PR</div>
                    </div>
                  </div>

                  <div className="space-y-2">
                    {[
                      {
                        tag: "ASPM",
                        msg: "Pre-build secret, Dockerfile/K8s IaC, SAST & Trivy CVE gate passed (0 high/critical)",
                        badge: "VERIFIED",
                      },
                      {
                        tag: "XDR",
                        msg: "Falco eBPF + CrowdSec + open-appsec WAF correlated into OCSF event graph",
                        badge: "0 ALERTS",
                      },
                      {
                        tag: "AI-SPM",
                        msg: "Outbound LLM prompt inspected — AWS/Stripe/JWT tokens auto-redacted before inference",
                        badge: "SHIELDED",
                      },
                      {
                        tag: "JULES",
                        msg: "Loki Log Bridge detected OOM on worker-billing → Jules opened PR #84 with memory fix",
                        badge: "AUTO-HEALED",
                      },
                    ].map((ev) => (
                      <div
                        key={ev.tag}
                        className="p-3 rounded-xl bg-slate-900/75 border border-slate-800 flex items-start justify-between gap-2"
                      >
                        <div className="min-w-0">
                          <span className="text-emerald-400 font-bold">[{ev.tag}]</span>{" "}
                          <span className="text-slate-200">{ev.msg}</span>
                        </div>
                        <span className="px-1.5 py-0.5 rounded bg-slate-800 text-emerald-300 shrink-0">
                          {ev.badge}
                        </span>
                      </div>
                    ))}
                  </div>

                  <div className="pt-2 border-t border-slate-800 flex items-center justify-between text-slate-400">
                    <span>Native MCP Server: /mcp (Claude, Cursor, Agents)</span>
                    <span className="text-emerald-400">OCSF v1.1 Ready</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
