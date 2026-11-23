"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Brain, CheckCircle2, Globe, Pause, Play, Shield } from "lucide-react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";

const heroSlides = [
  {
    badge: "Open Source PaaS",
    lead: "The sovereign PaaS for ",
    accent: "modern infrastructure.",
    subtitle:
      "Connect your VPS. Deploy apps, services, databases, and workers. PostgreSQL HA with Patroni. Redis Sentinel failover. AI auto-remediation. WireGuard VPN mesh. No vendor lock-in.",
  },
  {
    badge: "Multi-Server by Design",
    lead: "One control plane, ",
    accent: "infinite possibilities.",
    subtitle:
      "Orchestrate a mesh of VPS nodes through a single dashboard. Zero-trust WireGuard VPN, automated peer discovery, and built-in load balancing. Grow from one server to one hundred with zero drift.",
  },
  {
    badge: "AI-Powered Reliability",
    lead: "Your infrastructure ",
    accent: "heals itself.",
    subtitle:
      "The AI Senate continuously monitors your fleet. When a node degrades, it auto-remediates before you notice. Self-healing PostgreSQL HA, Redis Sentinel failover, and predictive disk alerts.",
  },
];

export function HeroCarousel() {
  const [current, setCurrent] = useState(0);
  const [isHovered, setIsHovered] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const [isUserPaused, setIsUserPaused] = useState(false);
  const prefersReducedMotion = useReducedMotion();

  const paused = Boolean(isHovered || isFocused || isUserPaused || prefersReducedMotion);

  useEffect(() => {
    if (paused) return;
    const timer = setInterval(() => {
      setCurrent((prev) => (prev + 1) % heroSlides.length);
    }, 7000);
    return () => clearInterval(timer);
  }, [paused]);

  const slide = heroSlides[current];

  return (
    <section
      aria-label="Platform highlights"
      className="relative pt-24 pb-24 md:pt-32 md:pb-32 overflow-hidden"
    >
      <div
        className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center z-10"
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        onFocus={() => setIsFocused(true)}
        onBlur={() => setIsFocused(false)}
      >
        {/* Status pill */}
        <div className="inline-flex items-center gap-2 px-4 py-1.5 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/25 rounded-full mb-8">
          <span className="flex h-2 w-2 rounded-full bg-emerald-600 dark:bg-emerald-400 animate-pulse motion-reduce:animate-none" aria-hidden="true" />
          <span className="text-sm font-semibold text-emerald-800 dark:text-emerald-300">{slide.badge}</span>
        </div>

        {/* Stable accessible H1 — animates inner copy gently when motion enabled */}
        <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold text-slate-900 dark:text-white tracking-tight leading-tight mb-6">
          <AnimatePresence mode="wait">
            <motion.span
              key={slide.lead}
              initial={prefersReducedMotion ? false : { opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, y: -12 }}
              transition={{ duration: 0.25 }}
              className="inline"
            >
              {slide.lead}
              <span className="text-emerald-700 dark:text-emerald-400">{slide.accent}</span>
            </motion.span>
          </AnimatePresence>
        </h1>

        <p className="text-lg md:text-xl text-slate-600 dark:text-slate-300 max-w-3xl mx-auto leading-relaxed font-medium">
          {slide.subtitle}
        </p>

        {/* Carousel controls with >=44px touch targets & Pause toggle (WCAG 2.2.2) */}
        <div className="mt-6 flex items-center justify-center gap-1">
          {heroSlides.map((item, i) => (
            <button
              key={item.badge}
              type="button"
              onClick={() => setCurrent(i)}
              aria-label={`Show slide ${i + 1}: ${item.badge}`}
              aria-current={i === current ? "true" : undefined}
              className="inline-flex items-center justify-center min-w-[44px] min-h-[44px] rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span
                className={`block h-2.5 rounded-full transition-all duration-300 ${
                  i === current
                    ? "bg-emerald-700 dark:bg-emerald-400 w-8"
                    : "bg-slate-300 dark:bg-slate-600 w-2.5 hover:bg-emerald-500"
                }`}
              />
            </button>
          ))}
          <button
            type="button"
            onClick={() => setIsUserPaused((p) => !p)}
            aria-label={isUserPaused ? "Resume highlight rotation" : "Pause highlight rotation"}
            aria-pressed={isUserPaused}
            className="inline-flex items-center justify-center min-w-[44px] min-h-[44px] text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {isUserPaused ? <Play className="w-4 h-4" aria-hidden="true" /> : <Pause className="w-4 h-4" aria-hidden="true" />}
          </button>
        </div>

        {/* Primary & Secondary CTAs */}
        <div className="mt-8 flex flex-col sm:flex-row gap-4 justify-center items-center">
          <Link
            href="/register"
            className="inline-flex items-center justify-center gap-2 min-h-[48px] px-8 py-3.5 text-base font-bold text-white bg-emerald-700 hover:bg-emerald-800 rounded-xl transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Get Grid Free <ArrowRight className="w-5 h-5" aria-hidden="true" />
          </Link>
          <Link
            href="/docs"
            className="inline-flex items-center justify-center gap-2 min-h-[48px] px-8 py-3.5 text-base font-bold text-slate-800 dark:text-slate-100 bg-white dark:bg-white/5 border border-slate-300 dark:border-slate-700 rounded-xl hover:border-emerald-500 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Read Documentation
          </Link>
        </div>

        {/* Key Guarantees */}
        <div className="mt-12 pt-8 border-t border-slate-200 dark:border-slate-800 flex flex-wrap justify-center gap-x-10 gap-y-4 text-sm font-semibold text-slate-700 dark:text-slate-300">
          <span className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
            Free &amp; Open Source (MIT)
          </span>
          <span className="flex items-center gap-2">
            <Shield className="w-4 h-4 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
            Zero-Trust Multi-Server
          </span>
          <span className="flex items-center gap-2">
            <Brain className="w-4 h-4 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
            AI Senate Auto-Remediation
          </span>
          <span className="flex items-center gap-2">
            <Globe className="w-4 h-4 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
            Deploy Anywhere
          </span>
        </div>
      </div>
    </section>
  );
}
