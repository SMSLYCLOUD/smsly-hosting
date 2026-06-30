"use client";

import type { ReactNode } from "react";
import { usePermissions } from "@/hooks/usePermissions";

interface RequirePermissionProps {
  /** Permission code required to render children. */
  code?: string;
  /** Any of these permission codes grants access. */
  anyOf?: string[];
  /** All of these permission codes are required. */
  allOf?: string[];
  /** Fallback to render when access is denied. */
  fallback?: ReactNode;
  children: ReactNode;
}

/**
 * Conditionally renders children based on the user's permissions.
 *
 * Usage:
 * ```tsx
 * <RequirePermission code="billing.manage" fallback={<AccessDenied />}>
 *   <BillingManagePanel />
 * </RequirePermission>
 * ```
 */
export function RequirePermission({
  code,
  anyOf,
  allOf,
  fallback = null,
  children,
}: RequirePermissionProps) {
  const { has, hasAny, hasAll } = usePermissions();

  let allowed = true;

  if (code) {
    allowed = has(code);
  } else if (anyOf && anyOf.length > 0) {
    allowed = hasAny(...anyOf);
  } else if (allOf && allOf.length > 0) {
    allowed = hasAll(...allOf);
  }

  if (!allowed) {
    return <>{fallback}</>;
  }

  return <>{children}</>;
}
