'use client';

import { Starfield } from './Starfield';

/**
 * Global background layer: full space experience canvas.
 * Stars, asteroids, satellites, aurora, shooting stars, comets — all on canvas.
 * Placed in layout.tsx so every page gets the same cosmic background.
 */
export function GlobalBackground() {
  return (
    <div className="fixed inset-0 z-0 pointer-events-none overflow-hidden" aria-hidden="true">
      <Starfield />
    </div>
  );
}
