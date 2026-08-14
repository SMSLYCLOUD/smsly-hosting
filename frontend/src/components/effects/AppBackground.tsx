"use client";

import { usePathname } from "next/navigation";
import { OperationalMesh } from "@/components/effects/OperationalMesh";
import { PublicBackground, isPublicRoute } from "@/components/effects/PublicBackground";

/**
 * AppBackground — Route-aware background switcher.
 *
 * Public/landing pages get the lightweight CSS-only PublicBackground.
 * Auth/dashboard/app pages get the animated OperationalMesh canvas.
 */
export function AppBackground() {
  const pathname = usePathname() || "/";

  if (isPublicRoute(pathname)) {
    return <PublicBackground />;
  }

  return <OperationalMesh />;
}
