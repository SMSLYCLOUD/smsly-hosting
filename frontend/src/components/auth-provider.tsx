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

interface User {
  pk: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
}

interface AuthContextType {
  user: User | null;
  loading: boolean;
}

const AuthContext = createContext<AuthContextType>({ user: null, loading: true });

/** Interval between periodic /auth/user/ revalidation (ms). */
const AUTH_REVALIDATE_INTERVAL = 60_000;

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const revalidateTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const fetchUser = async (isRevalidate = false) => {
      try {
        const res = await api.get("/auth/user/");
        setUser(res.data);
        if (!isRevalidate) resetRedirectGuard();

        const path = window.location.pathname;
        if (isAuthPage(path)) {
          if (canRedirectToLogin()) {
            window.location.replace("/dashboard");
          }
        }
      } catch {
        // Clear HttpOnly cookies by calling backend logout before redirecting.
        // Without this the __Host-auth_token cookie survives in the browser,
        // the middleware sees it on /login and redirects back to /dashboard.
        fetch('/api/v1/auth/logout/', { method: 'POST', credentials: 'include' }).catch(() => {});
        clearAuthCookies();
        setUser(null);

        const path = window.location.pathname;
        if (isProtectedPath(path) && canRedirectToLogin()) {
          window.location.replace("/login");
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
