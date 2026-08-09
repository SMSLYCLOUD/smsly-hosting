"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import api from "@/lib/api";
import { clearAuthCookies } from "@/lib/auth-cookies";
import {
  isProtectedPath,
  isAuthPage,
  canRedirectToLogin,
  resetRedirectGuard,
} from "@/lib/paths";
import { getRequiredPermissions } from "@/lib/role-routes";

interface User {
  pk: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  is_staff?: boolean;
  is_superuser?: boolean;
  permissions?: string[];
  roles?: {
    teams?: Array<{
      team_id: string;
      team_name: string;
      role: string;
      can_manage_billing: boolean;
    }>;
    orgs?: Array<{
      org_id: string;
      org_name: string;
      role: string;
      can_manage_billing: boolean;
    }>;
  };
}

interface AuthContextType {
  user: User | null;
  loading: boolean;
}

export const AuthContext = createContext<AuthContextType>({ user: null, loading: true });

/** Interval between periodic /auth/user/ revalidation (ms). */
const AUTH_REVALIDATE_INTERVAL = 60_000;

/** How many consecutive revalidation failures before we give up and redirect. */
const MAX_REVALIDATION_FAILURES = 2;

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const revalidateTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const consecutiveFailures = useRef(0);

  useEffect(() => {
    const fetchUser = async (isRevalidate = false) => {
      try {
        const res = await api.get("/auth/user/");
        setUser(res.data);
        consecutiveFailures.current = 0;
        if (!isRevalidate) resetRedirectGuard();

        const path = window.location.pathname;
        if (isAuthPage(path)) {
          if (canRedirectToLogin()) {
            window.location.replace("/dashboard");
          }
        }

        // Role-based redirect: if the user lacks permission for the current
        // route, redirect to the fallback page.
        if (!isRevalidate) {
          const req = getRequiredPermissions(path);
          if (req) {
            const userPerms: string[] = res.data?.permissions ?? [];
            const hasAccess =
              res.data?.is_superuser ||
              req.permissions.some((p: string) => userPerms.includes(p));
            if (!hasAccess && canRedirectToLogin()) {
              window.location.replace(req.redirect);
            }
          }
        }
      } catch {
        consecutiveFailures.current += 1;

        // Stop the revalidation timer once we exceed the failure limit.
        // This prevents dead sessions from hammering the server every 60s.
        if (isRevalidate && consecutiveFailures.current > MAX_REVALIDATION_FAILURES) {
          if (revalidateTimer.current) {
            clearInterval(revalidateTimer.current);
            revalidateTimer.current = null;
          }
        }

        // On the first revalidation failure, don't kill the session yet —
        // it might be a transient error (network blip, Docker restart, etc.).
        // Only kill the session on the initial load or after exceeding the
        // failure limit.
        if (!isRevalidate || consecutiveFailures.current > MAX_REVALIDATION_FAILURES) {
          // The api interceptor already called POST /api/v1/auth/logout/ to
          // clear the HttpOnly cookie. We just need to clear client state
          // and redirect.
          clearAuthCookies();
          setUser(null);

          const path = window.location.pathname;
          if (isProtectedPath(path) && canRedirectToLogin()) {
            window.location.replace("/login");
          }
        }
      } finally {
        setLoading(false);
      }
    };

    fetchUser();

    // Periodic revalidation — detects stale sessions without waiting
    // for an API call to return 401.  If the token has expired, the
    // user is redirected to /login before they see broken UI.
    revalidateTimer.current = setInterval(() => {
      fetchUser(true);
    }, AUTH_REVALIDATE_INTERVAL);

    return () => {
      if (revalidateTimer.current) {
        clearInterval(revalidateTimer.current);
      }
    };
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
