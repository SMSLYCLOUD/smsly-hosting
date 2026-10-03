"use client";

import React from "react";
import { cn } from "@/lib/utils";

/**
 * SleepingZzz — floating "z Z Z" spill for sleeping containers/services.
 *
 * Pure CSS (see .smsly-zzz keyframes in globals.css): three letters rise,
 * grow and fade in staggered loops. Zero JS timers, zero WebGL — safe to
 * render dozens of times on topology/autoscaler grids.
 */
export function SleepingZzz({ className, title = "Sleeping — wakes on request" }: { className?: string; title?: string }) {
  return (
    <span className={cn("smsly-zzz", className)} title={title} aria-label={title} role="img">
      <span>z</span>
      <span>Z</span>
      <span>Z</span>
    </span>
  );
}
