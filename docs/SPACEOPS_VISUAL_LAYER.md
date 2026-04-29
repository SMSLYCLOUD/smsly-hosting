# SpaceOps Visual Layer

The SpaceOps Visual Layer is a live, cinematic infrastructure visualization system that spans the CloudNeuron PaaS dashboard, deployment watch modes, and Code Map experiences. It turns application state (deploying, failing, recovering, idle) into an interactive and meaningful space environment.

## Goal
"Enterprise power, founder-level simplicity — with a live visual cloud universe."

## Components & Locations

*   **`SpaceOpsContext.tsx`** (`frontend/src/context/SpaceOpsContext.tsx`)
    *   React Context providing global visual state (`mode`, `intensity`, `label`).
    *   Used to transition the background into different modes (e.g., `analyzing`, `deploying`, `failed`, `success`).
*   **`SpaceOpsBackground.tsx`** (`frontend/src/components/effects/SpaceOpsBackground.tsx`)
    *   The wrapper component placed in `RootLayout` / `DashboardShell`. Replaces the old static `GlobalBackground`.
    *   Consumes the context and passes `visualState` properties down to the canvas.
*   **`Starfield.tsx`** (`frontend/src/components/effects/Starfield.tsx`)
    *   The core canvas renderer for the visual layer.
    *   Renders stars, asteroids, satellites, meteors, comets, auroras, and a dynamic solar system.
*   **`spaceStatusMap.ts`** (`frontend/src/lib/spaceStatusMap.ts`)
    *   The central mapping dictionary. Converts `SpaceOpsMode` strings to concrete visual variables (e.g., `baseSpeedMultiplier`, `particleDensity`, colors).
*   **`SpaceOpsLegend.tsx`** (`frontend/src/components/effects/SpaceOpsLegend.tsx`)
    *   A small, dismissible visual legend added to the dashboard to explain the status-to-visual mapping for developers.
*   **`CodeMapView.tsx`** (`frontend/src/components/intelligence/CodeMapView.tsx`)
    *   Controls the codebase 3D analysis UI and pushes state to `SpaceOpsContext` during analysis.
*   **`DeploymentWatchPage`** (`frontend/src/app/deployments/[id]/page.tsx`)
    *   Dedicated mission control screen for tracking active deployments, pushing state (`deploying`, `failed`, `success`) to the global background.

## Status-to-Visual Mapping

*   **Idle / Healthy:** Calm planet orbit, slow particle movement.
*   **Deploying:** Speed multiplier increases, comets (satellites) appear frequently.
*   **Analyzing:** Faster movement, intense blue glow.
*   **Success:** Soft blue/green burst, speed returns to normal.
*   **Failed:** Dark red core glow, particle speed decreases, meteors slow down.
*   **Critical Outage:** Black hole mode. Dark core, intense red gravity well, particles sucked inward.
*   **Recovering / Rollback:** White hole mode. Fast particle expansion, white bright core.
*   **Warning / Anomaly:** Amber colored stars and particles.

## Performance Safeguards

1.  **Reduced Motion (`prefers-reduced-motion`)**: The canvas respects OS-level reduced motion preferences, capping speeds to ~30% of normal.
2.  **Density Throttling**: Particle counts (stars, asteroids) are dynamically adjusted based on the `particleDensity` status multiplier.
3.  **No React Rerenders on Canvas Loop**: `Starfield.tsx` is completely isolated. The `requestAnimationFrame` loop mutates internal canvas state without causing React to re-render, keeping UI interactions fluid.
4.  **Reference Updates**: The visual state updates are passed via a `useRef` inside `Starfield.tsx`, allowing fluid transitions without re-mounting the canvas.

## Testing & Future Improvements

When working with `CodeMapView` and `DeploymentWatchPage`, ensure you reset the `SpaceOpsContext` back to `idle` upon unmount or completion using `resetSpaceOpsState()` to prevent background states from leaking across routes.

### Needed Backend APIs for Full Integration
- Real-time event streaming for Code Map progress (currently simulates 7 stages on the frontend).
- WebSockets/SSE for real-time log ingestion in `DeploymentWatchPage`.
