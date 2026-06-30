"use client";

import { useContext, useMemo } from "react";
import { AuthContext } from "@/components/auth-provider";

/**
 * Permission code constants matching backend `apps.permissions.codes`.
 * Use these for type-safe permission checks throughout the UI.
 */
export const PERMISSION = {
  SERVICE_CREATE: "service.create",
  SERVICE_UPDATE: "service.update",
  SERVICE_DELETE: "service.delete",
  SERVICE_DEPLOY: "service.deploy",
  SERVICE_STOP: "service.stop",
  SERVICE_RESTART: "service.restart",
  DEPLOYMENT_VIEW: "deployment.view",
  DEPLOYMENT_CREATE: "deployment.create",
  DEPLOYMENT_APPROVE: "deployment.approve",
  DEPLOYMENT_ROLLBACK: "deployment.rollback",
  DOMAIN_MANAGE: "domain.manage",
  ENV_VAR_MANAGE: "env_var.manage",
  ADDON_MANAGE: "addon.manage",
  BILLING_VIEW: "billing.view",
  BILLING_MANAGE: "billing.manage",
  BILLING_ADMIN: "billing.admin",
  TEAM_MANAGE: "team.manage",
  MEMBER_INVITE: "member.invite",
  MEMBER_REMOVE: "member.remove",
  MEMBER_ROLE_CHANGE: "member.role_change",
  PROJECT_CREATE: "project.create",
  PROJECT_DELETE: "project.delete",
  PROJECT_MANAGE: "project.manage",
  SETTINGS_MANAGE: "settings.manage",
  ADMIN_ACCESS: "admin.access",
  AUDIT_VIEW: "audit.view",
  ANALYTICS_VIEW: "analytics.view",
} as const;

export type PermissionCode = (typeof PERMISSION)[keyof typeof PERMISSION];

export interface TeamRole {
  team_id: string;
  team_name: string;
  role: "ADMIN" | "MEMBER" | "VIEWER";
  can_manage_billing: boolean;
}

export interface OrgRole {
  org_id: string;
  org_name: string;
  role: "OWNER" | "ADMIN" | "MEMBER";
  can_manage_billing: boolean;
}

export interface PermissionState {
  /** All permission codes the current user has. */
  permissions: string[];
  /** Team memberships with roles. */
  teamRoles: TeamRole[];
  /** Organization memberships with roles. */
  orgRoles: OrgRole[];
  /** Whether the user is a Django superuser. */
  isSuperuser: boolean;
  /** Whether the user is a Django staff member. */
  isStaff: boolean;
  /** Check if user has a specific permission code. */
  has: (code: string) => boolean;
  /** Check if user has ANY of the given permission codes. */
  hasAny: (...codes: string[]) => boolean;
  /** Check if user has ALL of the given permission codes. */
  hasAll: (...codes: string[]) => boolean;
  /** Whether user is a team admin on any team. */
  isTeamAdmin: boolean;
  /** Whether user is an org owner on any org. */
  isOrgOwner: boolean;
}

/**
 * Hook to access RBAC permissions from the AuthProvider context.
 *
 * Uses the ``permissions`` and ``roles`` fields returned by
 * ``GET /api/v1/auth/user/`` (extended by ``CustomUserDetailsSerializer``).
 */
export function usePermissions(): PermissionState {
  const auth = useContext(AuthContext);

  const permissions: string[] = auth?.user?.permissions ?? [];
  const teamRoles: TeamRole[] = auth?.user?.roles?.teams ?? [];
  const orgRoles: OrgRole[] = auth?.user?.roles?.orgs ?? [];
  const isSuperuser = auth?.user?.is_superuser ?? false;
  const isStaff = auth?.user?.is_staff ?? false;

  return useMemo(() => {
    const has = (code: string): boolean => {
      if (isSuperuser) return true;
      return permissions.includes(code);
    };

    const hasAny = (...codes: string[]): boolean => {
      if (isSuperuser) return true;
      return codes.some((c) => permissions.includes(c));
    };

    const hasAll = (...codes: string[]): boolean => {
      if (isSuperuser) return true;
      return codes.every((c) => permissions.includes(c));
    };

    const isTeamAdmin = teamRoles.some((t) => t.role === "ADMIN");
    const isOrgOwner = orgRoles.some((o) => o.role === "OWNER");

    return {
      permissions,
      teamRoles,
      orgRoles,
      isSuperuser,
      isStaff,
      has,
      hasAny,
      hasAll,
      isTeamAdmin,
      isOrgOwner,
    };
  }, [permissions, teamRoles, orgRoles, isSuperuser, isStaff]);
}
