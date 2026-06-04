"use client";

import React from "react";
import { Sidebar } from "@/components/sidebar";

/**
 * DashboardShell — Unified wrapper for all authenticated/dashboard pages.
 * Uses the SpaceOpsBackground (stars/nebula) from RootLayout for consistency.
 */
export function DashboardShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-screen flex flex-col overflow-x-hidden relative">
      {/* Subtle readability glass layer to keep text readable over active SpaceOps background */}
      <div className="fixed inset-0 pointer-events-none bg-background/30 backdrop-blur-[2px] z-0" aria-hidden="true" />

      {/* Content */}
      <div className="relative z-10 flex-1 flex flex-col md:flex-row mt-4 sm:mt-0 pb-safe">
        <aside className="hidden md:flex w-60 shrink-0 sticky top-14 h-[calc(100vh-3.5rem)] flex-col border-r border-white/5">
          <Sidebar />
        </aside>
        <div className="flex-1 min-w-0">
          {children}
        </div>
      </div>
    </main>
  );
}
