'use client';

import React, { useEffect, useRef, useState } from 'react';
import { ResponsiveContainer } from 'recharts';

interface ChartContainerProps {
  children: React.ReactElement;
  className?: string;
  minHeight?: number;
}

export function ChartContainer({ children, className, minHeight = 200 }: ChartContainerProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          setReady(true);
          observer.disconnect();
        }
      }
    });

    observer.observe(el);

    return () => observer.disconnect();
  }, []);

  return (
    <div ref={ref} className={className} style={{ minHeight }}>
      {ready ? (
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
      ) : null}
    </div>
  );
}
