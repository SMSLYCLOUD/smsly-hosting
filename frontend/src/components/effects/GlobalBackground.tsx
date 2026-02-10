'use client';

import { Starfield } from './Starfield';

/**
 * Global background layer: real starfield canvas + nebula cloud overlays.
 * Placed in layout.tsx so every page gets the same cosmic background.
 */
export function GlobalBackground() {
  return (
    <div className="fixed inset-0 z-0 pointer-events-none overflow-hidden" aria-hidden="true">
      {/* Real stars on canvas */}
      <Starfield />

      {/* Nebula clouds — soft cosmic color washes */}
      <div className="absolute inset-0">
        {/* Deep purple nebula — top left */}
        <div
          className="absolute w-[700px] h-[500px] rounded-full opacity-[0.07] -top-40 -left-40"
          style={{
            background: 'radial-gradient(ellipse, rgba(139,92,246,0.6) 0%, rgba(139,92,246,0) 70%)',
            animation: 'cloud-drift 40s ease-in-out infinite alternate',
          }}
        />

        {/* Teal nebula — center right */}
        <div
          className="absolute w-[600px] h-[450px] rounded-full opacity-[0.06] top-[30%] -right-32"
          style={{
            background: 'radial-gradient(ellipse, rgba(6,182,212,0.5) 0%, rgba(6,182,212,0) 70%)',
            animation: 'cloud-drift 35s ease-in-out infinite alternate-reverse',
            animationDelay: '8s',
          }}
        />

        {/* Sapphire nebula — bottom center */}
        <div
          className="absolute w-[800px] h-[400px] rounded-full opacity-[0.05] bottom-[10%] left-[20%]"
          style={{
            background: 'radial-gradient(ellipse, rgba(59,130,246,0.5) 0%, rgba(59,130,246,0) 70%)',
            animation: 'cloud-drift 45s ease-in-out infinite alternate',
            animationDelay: '15s',
          }}
        />

        {/* Emerald aurora band — subtle horizontal sweep */}
        <div
          className="absolute w-[200%] h-[120px] rounded-full opacity-[0.04] top-[15%] -left-[20%]"
          style={{
            background: 'linear-gradient(90deg, transparent, rgba(52,211,153,0.4), transparent)',
            animation: 'aurora-flow 30s ease-in-out infinite alternate',
          }}
        />

        {/* Violet aurora band */}
        <div
          className="absolute w-[200%] h-[80px] rounded-full opacity-[0.03] top-[60%] -left-[10%]"
          style={{
            background: 'linear-gradient(90deg, transparent, rgba(168,85,247,0.3), transparent)',
            animation: 'aurora-flow 25s ease-in-out infinite alternate-reverse',
            animationDelay: '12s',
          }}
        />
      </div>
    </div>
  );
}
