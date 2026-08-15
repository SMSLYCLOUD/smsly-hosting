"use client";

import React from "react";
import { TrulayAdBanner } from "@/components/dashboard/TrulayAdBanner";

/**
 * DashboardShell — Unified wrapper for all authenticated/dashboard pages.
 * Uses the SpaceOpsBackground (stars/nebula) from RootLayout for consistency.
 */
export function DashboardShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-screen flex flex-col overflow-x-hidden relative">
      {/* Subtle readability glass layer to keep text readable over active SpaceOps background */}
      <div className="fixed inset-0 pointer-events-none bg-background/30 z-0" aria-hidden="true" />

      {/* Content */}
      <div className="relative z-10 flex-1 flex flex-col mt-4 sm:mt-0 pb-safe">
        <div className="px-4 sm:px-6 pt-3 max-w-7xl mx-auto w-full">
          <TrulayAdBanner />
        </div>
        {children}
      </div>
    </main>
  );
}
