'use client';

import { useEffect, useState, type ReactNode } from 'react';

type IdleScheduler = (cb: () => void, opts?: { timeout: number }) => number;
type IdleCanceller = (handle: number) => void;

interface WindowWithIdle {
  requestIdleCallback?: IdleScheduler;
  cancelIdleCallback?: IdleCanceller;
}

interface LazyMountProps {
  children: ReactNode;
  fallback?: ReactNode;
  idleTimeoutMs?: number;
}

export function LazyMount({
  children,
  fallback = null,
  idleTimeoutMs = 2000,
}: LazyMountProps) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const trigger = () => {
      if (!cancelled) setMounted(true);
    };

    const w = window as unknown as WindowWithIdle;

    let handle: number | ReturnType<typeof setTimeout> | null = null;
    if (typeof w.requestIdleCallback === 'function') {
      handle = w.requestIdleCallback(trigger, { timeout: idleTimeoutMs });
    } else {
      handle = setTimeout(trigger, idleTimeoutMs);
    }

    const events: Array<keyof WindowEventMap> = [
      'scroll',
      'click',
      'keydown',
      'touchstart',
    ];
    const onInteraction = () => {
      trigger();
      events.forEach((e) => window.removeEventListener(e, onInteraction, true));
    };
    events.forEach((e) =>
      window.addEventListener(e, onInteraction, { capture: true, passive: true })
    );

    return () => {
      cancelled = true;
      events.forEach((e) => window.removeEventListener(e, onInteraction, true));
      if (typeof handle === 'number' && typeof w.cancelIdleCallback === 'function') {
        w.cancelIdleCallback(handle);
      } else if (handle !== null) {
        clearTimeout(handle as ReturnType<typeof setTimeout>);
      }
    };
  }, [idleTimeoutMs]);

  return <>{mounted ? children : fallback}</>;
}
