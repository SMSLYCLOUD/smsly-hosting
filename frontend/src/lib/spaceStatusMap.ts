import { SpaceOpsMode } from "@/context/SpaceOpsContext";

export interface SpaceVisualState {
  baseSpeedMultiplier: number;
  particleDensity: number;
  coreColor: [number, number, number];
  coreGlow: [number, number, number];
  anomalyColor?: [number, number, number];
  showBlackHole?: boolean;
  showWhiteHole?: boolean;
  cometFrequency: number; // probability multiplier
  meteorFrequency: number;
  auroraOpacityMultiplier: number;
}

export const spaceStatusMap: Record<SpaceOpsMode, SpaceVisualState> = {
  idle: {
    baseSpeedMultiplier: 1,
    particleDensity: 1,
    coreColor: [255, 255, 220],
    coreGlow: [255, 200, 50],
    cometFrequency: 1,
    meteorFrequency: 1,
    auroraOpacityMultiplier: 1,
  },
  analyzing: {
    baseSpeedMultiplier: 1.5,
    particleDensity: 1.2,
    coreColor: [200, 240, 255],
    coreGlow: [100, 180, 255],
    cometFrequency: 2,
    meteorFrequency: 3,
    auroraOpacityMultiplier: 1.5,
  },
  deploying: {
    baseSpeedMultiplier: 2.5,
    particleDensity: 1.5,
    coreColor: [180, 255, 200],
    coreGlow: [80, 255, 120],
    cometFrequency: 5, // Lots of comets (satellites moving)
    meteorFrequency: 2,
    auroraOpacityMultiplier: 2,
  },
  success: {
    baseSpeedMultiplier: 1.2,
    particleDensity: 1.1,
    coreColor: [150, 255, 255],
    coreGlow: [50, 200, 255], // Soft blue/green light burst
    cometFrequency: 1.5,
    meteorFrequency: 1.5,
    auroraOpacityMultiplier: 2.5,
  },
  warning: {
    baseSpeedMultiplier: 1.3,
    particleDensity: 1.5,
    coreColor: [255, 200, 100],
    coreGlow: [255, 150, 0],
    anomalyColor: [255, 100, 0], // Amber stars/asteroids
    cometFrequency: 0.5,
    meteorFrequency: 1,
    auroraOpacityMultiplier: 0.8,
  },
  failed: {
    baseSpeedMultiplier: 0.5, // Slow down
    particleDensity: 0.8,
    coreColor: [255, 100, 100],
    coreGlow: [255, 50, 50], // Red failing star
    anomalyColor: [255, 0, 0],
    cometFrequency: 0,
    meteorFrequency: 0.2,
    auroraOpacityMultiplier: 0.3,
  },
  critical: {
    baseSpeedMultiplier: 3, // Fast chaotic movement before being sucked in
    particleDensity: 2,
    coreColor: [20, 20, 20], // Dark core
    coreGlow: [100, 0, 0], // Dark red gravity well glow
    showBlackHole: true,
    anomalyColor: [200, 0, 0],
    cometFrequency: 0,
    meteorFrequency: 0.1,
    auroraOpacityMultiplier: 0.1,
  },
  recovering: {
    baseSpeedMultiplier: 4, // Fast reverse or restoration burst
    particleDensity: 2,
    coreColor: [255, 255, 255],
    coreGlow: [200, 240, 255],
    showWhiteHole: true,
    cometFrequency: 10,
    meteorFrequency: 5,
    auroraOpacityMultiplier: 3,
  }
};
