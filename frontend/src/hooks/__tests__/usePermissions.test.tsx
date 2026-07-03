import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { type ReactNode } from "react";

// Mock AuthProvider module so importing AuthProvider.tsx does NOT trigger the
// real `useEffect` that calls `/auth/user/`. We only need the AuthContext
// export itself; the hook under test reads it with useContext.
vi.mock("@/components/auth-provider", async () => {
  const actual = await vi.importActual<typeof import("@/components/auth-provider")>(
    "@/components/auth-provider",
  );
  return {
    AuthContext: actual.AuthContext,
    AuthProvider: ({ children }: { children: ReactNode }) => children,
    useAuth: () => ({ user: null, loading: true }),
  };
});

import { AuthContext, type AuthContextType } from "@/components/auth-provider";
import { usePermissions, PERMISSION } from "@/hooks/usePermissions";

/** Build a wrapper that injects a controlled AuthContext value. */
function makeWrapper(value: AuthContextType | null) {
  // The hook reads `useContext(AuthContext)`; if we pass `undefined` via a
  // Provider with `value={undefined}`, useContext returns the context's
  // _default_ value (the `createContext({ user: null, loading: true })`
  // literal). To exercise the truly-absent-context branch we just don't
  // wrap at all and rely on the default value (which is the same shape the
  // provider would emit when no user is loaded: `user: null`).
  return function Wrapper({ children }: { children: ReactNode }) {
    if (value === null) {
      // Default-value branch: do NOT provide a context. Children will see
      // the createContext default `{ user: null, loading: true }`.
      return <>{children}</>;
    }
    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
  };
}

const baseUser = {
  pk: 1,
  username: "alice",
  email: "alice@example.com",
  first_name: "Alice",
  last_name: "A",
};

describe("usePermissions", () => {
  describe("no auth context (default createContext value)", () => {
    it("returns empty permissions and zeroed flags when no user is loaded", () => {
      const { result } = renderHook(() => usePermissions(), {
        wrapper: makeWrapper(null),
      });

      expect(result.current.permissions).toEqual([]);
      expect(result.current.teamRoles).toEqual([]);
      expect(result.current.orgRoles).toEqual([]);
      expect(result.current.isSuperuser).toBe(false);
      expect(result.current.isStaff).toBe(false);
      expect(result.current.isTeamAdmin).toBe(false);
      expect(result.current.isOrgOwner).toBe(false);
    });

    it("has/hasAny/hasAll all return false for every code", () => {
      const { result } = renderHook(() => usePermissions(), {
        wrapper: makeWrapper(null),
      });

      expect(result.current.has("service.create")).toBe(false);
      expect(result.current.has("anything.at.all")).toBe(false);
      expect(result.current.hasAny("a", "b")).toBe(false);
      expect(result.current.hasAll("a", "b")).toBe(false);
      expect(result.current.hasAll()).toBe(true); // vacuously true
      expect(result.current.hasAny()).toBe(false); // .some on [] is false
    });
  });

  describe("superuser bypass", () => {
    it("grants every permission when is_superuser is true, even with empty list", () => {
      const { result } = renderHook(() => usePermissions(), {
        wrapper: makeWrapper({
          user: { ...baseUser, is_superuser: true, permissions: [] },
          loading: false,
        }),
      });

      expect(result.current.isSuperuser).toBe(true);
      expect(result.current.has("literally.anything")).toBe(true);
      expect(result.current.hasAny("a", "b", "c")).toBe(true);
      expect(result.current.hasAll("a", "b", "c")).toBe(true);
    });
  });

  describe("regular user with explicit permissions", () => {
    it("checks membership in the permissions array exactly", () => {
      const { result } = renderHook(() => usePermissions(), {
        wrapper: makeWrapper({
          user: {
            ...baseUser,
            is_superuser: false,
            permissions: ["service.create", "deployment.view"],
          },
          loading: false,
        }),
      });

      expect(result.current.has("service.create")).toBe(true);
      expect(result.current.has("service.delete")).toBe(false);
      expect(result.current.has("deployment.view")).toBe(true);
    });

    it("hasAny returns true when at least one code matches", () => {
      const { result } = renderHook(() => usePermissions(), {
        wrapper: makeWrapper({
          user: {
            ...baseUser,
            is_superuser: false,
            permissions: ["service.create"],
          },
          loading: false,
        }),
      });

      expect(
        result.current.hasAny("service.create", "service.delete"),
      ).toBe(true);
      expect(result.current.hasAny("service.delete", "service.update")).toBe(
        false,
      );
    });

    it("hasAll returns true only when every code matches", () => {
      const { result } = renderHook(() => usePermissions(), {
        wrapper: makeWrapper({
          user: {
            ...baseUser,
            is_superuser: false,
            permissions: ["service.create"],
          },
          loading: false,
        }),
      });

      expect(
        result.current.hasAll("service.create", "service.delete"),
      ).toBe(false);
      expect(result.current.hasAll("service.create")).toBe(true);
      expect(result.current.hasAll()).toBe(true);
    });
  });

  describe("team roles", () => {
    it("isTeamAdmin is true when any team membership has role ADMIN", () => {
      const { result } = renderHook(() => usePermissions(), {
        wrapper: makeWrapper({
          user: {
            ...baseUser,
            is_superuser: false,
            roles: {
              teams: [
                {
                  team_id: "1",
                  team_name: "A",
                  role: "ADMIN",
                  can_manage_billing: true,
                },
              ],
              orgs: [],
            },
          },
          loading: false,
        }),
      });

      expect(result.current.isTeamAdmin).toBe(true);
      expect(result.current.teamRoles).toHaveLength(1);
      expect(result.current.teamRoles[0].team_id).toBe("1");
    });

    it("isTeamAdmin is false for MEMBER / VIEWER roles", () => {
      const { result } = renderHook(() => usePermissions(), {
        wrapper: makeWrapper({
          user: {
            ...baseUser,
            is_superuser: false,
            roles: {
              teams: [
                {
                  team_id: "1",
                  team_name: "A",
                  role: "MEMBER",
                  can_manage_billing: false,
                },
                {
                  team_id: "2",
                  team_name: "B",
                  role: "VIEWER",
                  can_manage_billing: false,
                },
              ],
              orgs: [],
            },
          },
          loading: false,
        }),
      });

      expect(result.current.isTeamAdmin).toBe(false);
    });
  });

  describe("org roles", () => {
    it("isOrgOwner is true when any org membership has role OWNER", () => {
      const { result } = renderHook(() => usePermissions(), {
        wrapper: makeWrapper({
          user: {
            ...baseUser,
            is_superuser: false,
            roles: {
              teams: [],
              orgs: [
                {
                  org_id: "1",
                  org_name: "A",
                  role: "OWNER",
                  can_manage_billing: true,
                },
              ],
            },
          },
          loading: false,
        }),
      });

      expect(result.current.isOrgOwner).toBe(true);
      expect(result.current.orgRoles).toHaveLength(1);
      expect(result.current.orgRoles[0].org_id).toBe("1");
    });

    it("isOrgOwner is false for ADMIN / MEMBER roles", () => {
      const { result } = renderHook(() => usePermissions(), {
        wrapper: makeWrapper({
          user: {
            ...baseUser,
            is_superuser: false,
            roles: {
              teams: [],
              orgs: [
                {
                  org_id: "1",
                  org_name: "A",
                  role: "ADMIN",
                  can_manage_billing: true,
                },
              ],
            },
          },
          loading: false,
        }),
      });

      expect(result.current.isOrgOwner).toBe(false);
    });
  });

  describe("memoization", () => {
    it("returns a stable object reference across rerenders when user is unchanged", () => {
      const { result, rerender } = renderHook(() => usePermissions(), {
        wrapper: makeWrapper({
          user: {
            ...baseUser,
            is_superuser: false,
            permissions: ["service.create"],
          },
          loading: false,
        }),
      });

      const first = result.current;
      rerender();
      const second = result.current;
      rerender();
      const third = result.current;

      expect(second).toBe(first);
      expect(third).toBe(first);
    });
  });

  describe("PERMISSION constants", () => {
    it("exposes the canonical backend permission codes", () => {
      expect(PERMISSION.SERVICE_CREATE).toBe("service.create");
      expect(PERMISSION.SERVICE_DELETE).toBe("service.delete");
      expect(PERMISSION.BILLING_ADMIN).toBe("billing.admin");
      expect(PERMISSION.ADMIN_ACCESS).toBe("admin.access");
    });
  });
});
