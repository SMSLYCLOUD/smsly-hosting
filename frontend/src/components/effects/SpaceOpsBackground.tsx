'use client';

import React from 'react';
import { useSpaceOps } from '@/context/SpaceOpsContext';
import { Starfield } from './Starfield';
import { spaceStatusMap } from '@/lib/spaceStatusMap';

/**
 * Global background layer: full space experience canvas.
 * Manages the state from SpaceOpsContext to update Starfield visuals.
 */
export function SpaceOpsBackground() {
  const { mode } = useSpaceOps();
  const visualState = spaceStatusMap[mode] || spaceStatusMap['idle'];

  return (
    <div className="fixed inset-0 z-0 pointer-events-none overflow-hidden" aria-hidden="true">
      <Starfield visualState={visualState} />
    </div>
  );
}
