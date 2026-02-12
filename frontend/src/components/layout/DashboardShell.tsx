"use client";

import React from "react";
import { Navbar } from "./Navbar";

/**
 * DashboardShell — Unified wrapper for all authenticated/dashboard pages.
 * Uses the GlobalBackground (stars/nebula) from RootLayout for consistency.
 */
export function DashboardShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-screen flex flex-col overflow-x-hidden relative">
      <Navbar />

      {/* Content — transparent to show GlobalBackground */}
      <div className="relative z-10 flex-1 flex flex-col">
        {children}
      </div>
    </main>
  );
}
