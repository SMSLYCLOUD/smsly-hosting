'use client';

import { useRef } from 'react';
import { useScroll, useTransform, motion, useSpring } from 'framer-motion';
import { cn } from '@/lib/utils';

interface ParallaxLayerProps {
  children: React.ReactNode;
  speed?: number;
  className?: string;
  style?: React.CSSProperties;
}

export function ParallaxLayer({ children, speed = 0.3, className, style }: ParallaxLayerProps) {
  const ref = useRef<HTMLDivElement>(null);

  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ['start end', 'end start'],
  });

  const rawY = useTransform(scrollYProgress, [0, 1], [speed * 60, speed * -60]);
  const y = useSpring(rawY, { damping: 25, stiffness: 120, mass: 0.2 });

  return (
    <div ref={ref} className={cn('relative', className)} style={style}>
      <motion.div style={{ y, willChange: 'transform' }} className="transform-gpu">
        {children}
      </motion.div>
    </div>
  );
}
