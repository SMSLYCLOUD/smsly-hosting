"use client";

import { useRef, useState, useEffect, ReactNode, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";

interface SwipePageProps {
  children: ReactNode[];
  direction?: "left" | "right" | "alternate";
  showDots?: boolean;
  showLabels?: boolean;
  labels?: string[];
  className?: string;
  /** Height multiplier per page (default: 1 — each page = 100vh of scroll distance) */
  scrollHeight?: number;
}

export function SwipePage({
  children,
  direction = "alternate",
  showDots = true,
  showLabels = false,
  labels = [],
  className,
  scrollHeight = 1,
}: SwipePageProps) {
  const [current, setCurrent] = useState(0);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const total = children.length;

  // Scroll-jacking: listen to scroll on the wrapper and snap to pages
  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;

    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        const rect = wrapper.getBoundingClientRect();
        const wrapperTop = -rect.top;
        const wrapperHeight = rect.height - window.innerHeight;
        if (wrapperHeight <= 0) {
          ticking = false;
          return;
        }
        const progress = Math.max(0, Math.min(1, wrapperTop / wrapperHeight));
        const page = Math.round(progress * (total - 1));
        setCurrent(page);
        ticking = false;
      });
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener("scroll", onScroll);
  }, [total]);

  const goTo = useCallback((index: number) => {
    const wrapper = wrapperRef.current;
    if (!wrapper || index < 0 || index >= total) return;
    const rect = wrapper.getBoundingClientRect();
    const wrapperTop = window.scrollY + rect.top;
    const wrapperHeight = rect.height - window.innerHeight;
    const targetScroll = wrapperTop + (index / (total - 1)) * wrapperHeight;
    window.scrollTo({ top: targetScroll, behavior: "smooth" });
  }, [total]);

  const getSlideDirection = (idx: number) => {
    if (direction === "left") return { enter: -1, exit: 1 };
    if (direction === "right") return { enter: 1, exit: -1 };
    return idx % 2 === 0
      ? { enter: 1, exit: -1 }
      : { enter: -1, exit: 1 };
  };

  const slide = getSlideDirection(current);

  const variants = {
    enter: (dir: number) => ({
      x: dir > 0 ? "100%" : "-100%",
      opacity: 0,
    }),
    center: {
      x: 0,
      opacity: 1,
    },
    exit: (dir: number) => ({
      x: dir > 0 ? "-100%" : "100%",
      opacity: 0,
    }),
  };

  return (
    <>
      {/* Scroll spacer — creates scroll distance */}
      <div
        ref={wrapperRef}
        style={{ height: `${total * scrollHeight * 100}vh` }}
        className="relative"
      >
        {/* Sticky viewport */}
        <div className="sticky top-0 h-screen overflow-hidden">
          <AnimatePresence mode="wait" custom={slide.enter}>
            <motion.div
              key={current}
              custom={slide.enter}
              variants={variants}
              initial="enter"
              animate="center"
              exit="exit"
              transition={{
                x: { type: "spring", stiffness: 300, damping: 35, mass: 0.8 },
                opacity: { duration: 0.25 },
              }}
              className="absolute inset-0 overflow-y-auto"
            >
              {children[current]}
            </motion.div>
          </AnimatePresence>

          {/* Progress dots */}
          {showDots && (
            <div className="fixed right-6 top-1/2 -translate-y-1/2 z-50 flex flex-col gap-3">
              {children.map((_, i) => (
                <button
                  key={i}
                  onClick={() => goTo(i)}
                  className={`w-3 h-3 rounded-full transition-all duration-300 border-2 ${
                    i === current
                      ? "bg-emerald-500 border-emerald-500 scale-125 shadow-lg shadow-emerald-500/50"
                      : "bg-transparent border-slate-400/50 hover:border-emerald-400"
                  }`}
                  aria-label={`Go to section ${i + 1}`}
                />
              ))}
            </div>
          )}

          {/* Section label */}
          {showLabels && labels[current] && (
            <motion.div
              key={`label-${current}`}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="fixed bottom-8 left-1/2 -translate-x-1/2 z-50 px-4 py-2 bg-black/60 backdrop-blur-sm rounded-full text-sm font-medium text-white/80"
            >
              {labels[current]}
            </motion.div>
          )}

          {/* Section counter */}
          <div className="fixed bottom-8 right-6 z-50 text-sm font-mono text-white/40">
            {current + 1} / {total}
          </div>
        </div>
      </div>
    </>
  );
}
