"use client";

import { useRef, ReactNode } from "react";
import { motion, useInView, Variants } from "framer-motion";

type AnimationVariant =
  | "fadeUp"
  | "scaleIn"
  | "slideLeft"
  | "slideRight"
  | "rotateIn"
  | "blurReveal"
  | "clipWipe"
  | "staggerContainer"
  | "springBounce"
  | "parallax"
  | "gentleRise"
  | "dramaticReveal";

interface ScrollRevealProps {
  children: ReactNode;
  variant?: AnimationVariant;
  delay?: number;
  duration?: number;
  className?: string;
  parallaxOffset?: number;
  margin?: string;
}

// Smooth expo-out easing — buttery deceleration
const EXPO_OUT = [0.22, 1, 0.36, 1] as const;
// Gentler ease for large movements
const SMOOTH_OUT = [0.25, 1, 0.5, 1] as const;
// Dramatic overshoot
const OVERSHOOT = [0.34, 1.56, 0.64, 1] as const;

const variantMap: Record<string, Variants> = {
  fadeUp: {
    hidden: { opacity: 0, y: 50, filter: "blur(4px)" },
    visible: { opacity: 1, y: 0, filter: "blur(0px)" },
  },
  scaleIn: {
    hidden: { opacity: 0, scale: 0.85, filter: "blur(6px)" },
    visible: { opacity: 1, scale: 1, filter: "blur(0px)" },
  },
  slideLeft: {
    hidden: { opacity: 0, x: -80, filter: "blur(3px)" },
    visible: { opacity: 1, x: 0, filter: "blur(0px)" },
  },
  slideRight: {
    hidden: { opacity: 0, x: 80, filter: "blur(3px)" },
    visible: { opacity: 1, x: 0, filter: "blur(0px)" },
  },
  rotateIn: {
    hidden: { opacity: 0, rotate: -8, scale: 0.92, y: 30 },
    visible: { opacity: 1, rotate: 0, scale: 1, y: 0 },
  },
  blurReveal: {
    hidden: { opacity: 0, filter: "blur(24px)", y: 20, scale: 0.97 },
    visible: { opacity: 1, filter: "blur(0px)", y: 0, scale: 1 },
  },
  clipWipe: {
    hidden: { opacity: 0, y: 30, scaleY: 0.9 },
    visible: { opacity: 1, y: 0, scaleY: 1 },
  },
  springBounce: {
    hidden: { opacity: 0, y: 60, scale: 0.9 },
    visible: { opacity: 1, y: 0, scale: 1 },
  },
  gentleRise: {
    hidden: { opacity: 0, y: 24 },
    visible: { opacity: 1, y: 0 },
  },
  dramaticReveal: {
    hidden: { opacity: 0, y: 40, scale: 0.92, rotate: -2, filter: "blur(8px)" },
    visible: { opacity: 1, y: 0, scale: 1, rotate: 0, filter: "blur(0px)" },
  },
};

function getTransition(variant: AnimationVariant, duration: number) {
  switch (variant) {
    case "springBounce":
      return { type: "spring" as const, stiffness: 120, damping: 14, mass: 0.8 };
    case "blurReveal":
      return { duration: 0.9, ease: EXPO_OUT };
    case "clipWipe":
      return { duration: 0.9, ease: EXPO_OUT };
    case "rotateIn":
      return { duration: 0.9, ease: OVERSHOOT };
    case "dramaticReveal":
      return { duration: 1.0, ease: EXPO_OUT };
    case "gentleRise":
      return { duration: 0.7, ease: SMOOTH_OUT };
    default:
      return { duration, ease: EXPO_OUT };
  }
}

/** ScrollReveal — orchestrates entrance animation when element enters viewport. */
export function ScrollReveal({
  children,
  variant = "fadeUp",
  delay = 0,
  duration = 0.8,
  className,
  margin = "-60px",
}: ScrollRevealProps) {
  const ref = useRef(null);
  const inView = useInView(ref, { once: true, margin: margin as any });

  if (variant === "staggerContainer") {
    return (
      <motion.div
        ref={ref}
        variants={variantMap.fadeUp}
        initial="hidden"
        animate={inView ? "visible" : "hidden"}
        transition={{ staggerChildren: 0.14, delayChildren: 0.08 }}
        className={className}
      >
        {children}
      </motion.div>
    );
  }

  return (
    <motion.div
      ref={ref}
      initial="hidden"
      animate={inView ? "visible" : "hidden"}
      variants={variantMap[variant]}
      transition={{ ...getTransition(variant, duration), delay }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

/** StaggerChild — use inside a ScrollReveal with variant="staggerContainer". */
export function StaggerChild({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <motion.div
      variants={{
        hidden: { opacity: 0, y: 30, scale: 0.96 },
        visible: { opacity: 1, y: 0, scale: 1 },
      }}
      transition={{ type: "spring" as const, stiffness: 140, damping: 18, mass: 0.7 }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

/** ParallaxReveal — smooth parallax slide-up with subtle scale. */
export function ParallaxReveal({
  children,
  className,
  parallaxOffset = 50,
}: {
  children: ReactNode;
  className?: string;
  parallaxOffset?: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: parallaxOffset, scale: 0.97 }}
      whileInView={{ opacity: 1, y: 0, scale: 1 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ duration: 0.9, ease: EXPO_OUT }}
      className={className}
    >
      {children}
    </motion.div>
  );
}
