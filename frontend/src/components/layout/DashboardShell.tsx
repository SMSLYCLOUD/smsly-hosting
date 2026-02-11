"use client";

import React from "react";
import { Navbar } from "./Navbar";

/**
 * DashboardShell — Unified wrapper for all authenticated/dashboard pages.
 * Provides:
 *  - Consistent cloud-themed premium background (gradient mesh + floating orbs)
 *  - Aurora band + cloud wisp decorations
 *  - Navbar
 *  - Responsive content area with proper z-indexing
 *
 * Usage:
 *   <DashboardShell>
 *     <YourPageContent />
 *   </DashboardShell>
 */
export function DashboardShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-screen flex flex-col premium-bg overflow-x-hidden">
      <Navbar />

      {/* Cloud / Aurora decorations — calming, non-distracting */}
      <div className="floating-orb w-[500px] h-[500px] bg-emerald-500/[0.04] -top-32 -right-32" style={{ animationDelay: '0s' }} />
      <div className="floating-orb w-[400px] h-[400px] bg-cyan-500/[0.03] bottom-1/4 -left-24" style={{ animationDelay: '6s' }} />
      <div className="floating-orb w-[350px] h-[350px] bg-violet-500/[0.03] -bottom-20 right-1/4" style={{ animationDelay: '12s' }} />

      {/* Aurora band — slow, subtle color sweep */}
      <div className="aurora-band top-[15%] -left-[20%] bg-gradient-to-r from-emerald-400/10 via-cyan-400/5 to-transparent" />

      {/* Cloud wisp */}
      <div className="cloud-wisp w-[600px] h-[300px] bg-gradient-to-br from-slate-300/20 to-transparent top-[40%] right-[-5%]" style={{ animationDelay: '3s' }} />

      {/* Content */}
      <div className="relative z-10 flex-1 flex flex-col">
        {children}
      </div>
    </main>
  );
}
