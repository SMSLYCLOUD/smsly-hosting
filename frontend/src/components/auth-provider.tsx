"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Loader2 } from "lucide-react";
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
  const pathname = usePathname() || "/";
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [transientError, setTransientError] = useState(false);
  const revalidateTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const consecutiveFailures = useRef(0);

  const fetchUser = useCallback(async (isRevalidate = false) => {
    try {
      const res = await api.get("/auth/user/");
      setUser(res.data);
      setTransientError(false);
      consecutiveFailures.current = 0;
      if (!isRevalidate) resetRedirectGuard();
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 401) {
        if (revalidateTimer.current) {
          clearInterval(revalidateTimer.current);
          revalidateTimer.current = null;
        }
        setTransientError(false);
        clearAuthCookies();
        setUser(null);

        const path = window.location.pathname;
        if (isProtectedPath(path) && canRedirectToLogin()) {
          window.location.replace("/login");
        }
        return;
      }

      consecutiveFailures.current += 1;
      if (!isRevalidate) {
        setTransientError(true);
      }

      // Stop the revalidation timer once we exceed the failure limit so
      // prolonged outages don't hammer the server, without clearing cookies.
      if (isRevalidate && consecutiveFailures.current > MAX_REVALIDATION_FAILURES) {
        if (revalidateTimer.current) {
          clearInterval(revalidateTimer.current);
          revalidateTimer.current = null;
        }
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchUser(false);
  }, [fetchUser]);

  // Gate periodic revalidation on an active authenticated user so public
  // pages (/login, /register, /store) never poll /auth/user/ unnecessarily.
  const hasUser = Boolean(user);
  useEffect(() => {
    if (!hasUser) {
      if (revalidateTimer.current) {
        clearInterval(revalidateTimer.current);
        revalidateTimer.current = null;
      }
      return;
    }

    revalidateTimer.current = setInterval(() => {
      void fetchUser(true);
    }, AUTH_REVALIDATE_INTERVAL);

    return () => {
      if (revalidateTimer.current) {
        clearInterval(revalidateTimer.current);
        revalidateTimer.current = null;
      }
    };
  }, [hasUser, fetchUser]);

  // Re-evaluate route auth and role permissions on every client-side navigation.
  useEffect(() => {
    if (loading) return;

    if (!user) {
      if (!transientError && isProtectedPath(pathname) && canRedirectToLogin()) {
        window.location.replace("/login");
      }
      return;
    }

    if (isAuthPage(pathname) && canRedirectToLogin()) {
      window.location.replace("/dashboard");
      return;
    }

    const req = getRequiredPermissions(pathname);
    if (req) {
      const userPerms: string[] = user.permissions ?? [];
      const hasAccess =
        user.is_superuser ||
        req.permissions.some((p: string) => userPerms.includes(p));
      if (!hasAccess && canRedirectToLogin()) {
        window.location.replace(req.redirect);
      }
    }
  }, [pathname, user, loading, transientError]);

  const hideProtectedContent = isProtectedPath(pathname) && (loading || !user);

  return (
    <AuthContext.Provider value={{ user, loading }}>
      {hideProtectedContent ? (
        <div className="min-h-screen flex items-center justify-center bg-background">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Checking session" />
        </div>
      ) : (
        children
      )}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
