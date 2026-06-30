/**
 * Maps frontend routes to required permissions for role-based navigation.
 *
 * When the AuthProvider detects that the current user lacks the required
 * permission for the current route, it redirects to the specified fallback.
 */
export const ROLE_ROUTE_MAP: Record<
  string,
  { permissions: string[]; redirect: string }
> = {
  "/admin-dashboard": {
    permissions: ["admin.access"],
    redirect: "/dashboard",
  },
  "/admin-dashboard/users": {
    permissions: ["admin.access"],
    redirect: "/dashboard",
  },
  "/admin-dashboard/pricing": {
    permissions: ["admin.access"],
    redirect: "/dashboard",
  },
  "/admin-dashboard/customers": {
    permissions: ["admin.access"],
    redirect: "/dashboard",
  },
  "/admin-dashboard/pnl": {
    permissions: ["admin.access"],
    redirect: "/dashboard",
  },
  "/admin-dashboard/costs": {
    permissions: ["admin.access"],
    redirect: "/dashboard",
  },
  "/admin-dashboard/ai-providers": {
    permissions: ["admin.access"],
    redirect: "/dashboard",
  },
  "/admin-dashboard/licensing": {
    permissions: ["admin.access"],
    redirect: "/dashboard",
  },
  "/billing": {
    permissions: ["billing.view"],
    redirect: "/dashboard",
  },
  "/autoscaler": {
    permissions: ["admin.access"],
    redirect: "/dashboard",
  },
  "/grafana": {
    permissions: ["admin.access"],
    redirect: "/dashboard",
  },
  "/backups": {
    permissions: ["admin.access"],
    redirect: "/dashboard",
  },
  "/network": {
    permissions: ["admin.access"],
    redirect: "/dashboard",
  },
  "/replication": {
    permissions: ["admin.access"],
    redirect: "/dashboard",
  },
  "/settings/billing": {
    permissions: ["billing.view"],
    redirect: "/settings",
  },
  "/settings/audit-logs": {
    permissions: ["audit.view"],
    redirect: "/settings",
  },
  "/settings/slow-queries": {
    permissions: ["admin.access"],
    redirect: "/settings",
  },
  "/settings/updates": {
    permissions: ["settings.manage"],
    redirect: "/settings",
  },
};

/**
 * Returns the required permissions for a given path, or null if no
 * role restriction applies.
 */
export function getRequiredPermissions(
  pathname: string
): { permissions: string[]; redirect: string } | null {
  for (const [prefix, entry] of Object.entries(ROLE_ROUTE_MAP)) {
    if (pathname === prefix || pathname.startsWith(prefix + "/")) {
      return entry;
    }
  }
  return null;
}
