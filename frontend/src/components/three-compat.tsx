'use client';

// NOTE: This component used to monkey-patch the global `console.warn` to suppress a
// deprecation warning from `THREE.Clock` (used transitively by `react-force-graph-3d`).
// Mutating globals is a footgun in SSR/RSC and conflicts with the React strict mode
// double-invoke. Instead, the warning is now filtered at the import site (or simply
// tolerated in dev). This component is kept as a no-op for now to avoid touching
// every import site, but it is safe to remove once the upstream package upgrades.
export function ThreeCompat() {
  return null;
}
