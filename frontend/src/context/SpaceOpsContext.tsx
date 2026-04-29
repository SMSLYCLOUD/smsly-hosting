"use client";

import React, { createContext, useContext, useState, ReactNode } from "react";

export type SpaceOpsMode = "idle" | "analyzing" | "deploying" | "success" | "failed" | "warning" | "critical" | "recovering";
export type SpaceOpsIntensity = "low" | "medium" | "high";

interface SpaceOpsState {
  mode: SpaceOpsMode;
  intensity: SpaceOpsIntensity;
  label?: string;
  setSpaceOpsState: (state: Partial<Omit<SpaceOpsState, "setSpaceOpsState">>) => void;
  resetSpaceOpsState: () => void;
}

const SpaceOpsContext = createContext<SpaceOpsState | undefined>(undefined);

export function SpaceOpsProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<SpaceOpsMode>("idle");
  const [intensity, setIntensity] = useState<SpaceOpsIntensity>("low");
  const [label, setLabel] = useState<string | undefined>(undefined);

  const setSpaceOpsState = (state: Partial<Omit<SpaceOpsState, "setSpaceOpsState">>) => {
    if (state.mode !== undefined) setMode(state.mode);
    if (state.intensity !== undefined) setIntensity(state.intensity);
    if (state.label !== undefined) setLabel(state.label);
  };

  const resetSpaceOpsState = () => {
    setMode("idle");
    setIntensity("low");
    setLabel(undefined);
  };

  return (
    <SpaceOpsContext.Provider value={{ mode, intensity, label, setSpaceOpsState, resetSpaceOpsState }}>
      {children}
    </SpaceOpsContext.Provider>
  );
}

export function useSpaceOps() {
  const context = useContext(SpaceOpsContext);
  if (context === undefined) {
    throw new Error("useSpaceOps must be used within a SpaceOpsProvider");
  }
  return context;
}
