/**
 * Maps frontend routes to required permissions for role-based navigation.
 *
 * When the AuthProvider detects that the current user lacks the required
 * permission for the current route, it redirects to the specified fallback.
 */
export interface RoleRouteRule {
  prefix: string;
  anyPermissions: string[];
  redirect: string;
}

export const ROLE_ROUTE_RULES: RoleRouteRule[] = [
  { prefix: "/admin-dashboard", anyPermissions: ["admin.access"], redirect: "/dashboard" },
  { prefix: "/admin-dashboard/users", anyPermissions: ["admin.access"], redirect: "/dashboard" },
  { prefix: "/admin-dashboard/pricing", anyPermissions: ["admin.access"], redirect: "/dashboard" },
  { prefix: "/admin-dashboard/customers", anyPermissions: ["admin.access"], redirect: "/dashboard" },
  { prefix: "/admin-dashboard/pnl", anyPermissions: ["admin.access"], redirect: "/dashboard" },
  { prefix: "/admin-dashboard/costs", anyPermissions: ["admin.access"], redirect: "/dashboard" },
  { prefix: "/admin-dashboard/ai-providers", anyPermissions: ["admin.access"], redirect: "/dashboard" },
  { prefix: "/admin-dashboard/licensing", anyPermissions: ["admin.access"], redirect: "/dashboard" },
  { prefix: "/billing", anyPermissions: ["billing.view"], redirect: "/dashboard" },
  { prefix: "/autoscaler", anyPermissions: ["admin.access"], redirect: "/dashboard" },
  { prefix: "/grafana", anyPermissions: ["admin.access"], redirect: "/dashboard" },
  { prefix: "/backups", anyPermissions: ["admin.access"], redirect: "/dashboard" },
  { prefix: "/network", anyPermissions: ["admin.access"], redirect: "/dashboard" },
  { prefix: "/clusters", anyPermissions: ["admin.access"], redirect: "/dashboard" },
  { prefix: "/replication", anyPermissions: ["admin.access"], redirect: "/dashboard" },
  { prefix: "/settings/billing", anyPermissions: ["billing.view"], redirect: "/settings" },
  { prefix: "/settings/audit-logs", anyPermissions: ["audit.view"], redirect: "/settings" },
  { prefix: "/settings/slow-queries", anyPermissions: ["admin.access"], redirect: "/settings" },
  { prefix: "/settings/updates", anyPermissions: ["settings.manage"], redirect: "/settings" },
  { prefix: "/settings/replication", anyPermissions: ["admin.access"], redirect: "/settings" },
];

export const ROLE_ROUTE_MAP: Record<
  string,
  { permissions: string[]; redirect: string }
> = Object.fromEntries(
  ROLE_ROUTE_RULES.map((rule) => [
    rule.prefix,
    { permissions: rule.anyPermissions, redirect: rule.redirect },
  ])
);

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
