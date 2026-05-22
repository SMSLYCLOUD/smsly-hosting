'use client';

import { useEffect } from 'react';

export function ThreeCompat() {
  useEffect(() => {
    const warn = console.warn;
    console.warn = (...args: any[]) => {
      if (typeof args[0] === 'string' && args[0].includes('THREE.Clock: This module has been deprecated')) {
        return;
      }
      warn.apply(console, args);
    };
    return () => {
      console.warn = warn;
    };
  }, []);

  return null;
}
