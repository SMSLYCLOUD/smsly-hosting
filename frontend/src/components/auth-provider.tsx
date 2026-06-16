"use client";

import { createContext, useContext, useEffect, useState } from "react";
import api from "@/lib/api";
import { clearAuthCookies } from "@/lib/auth-cookies";

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

function isProtectedPath(path: string): boolean {
  const protectedPrefixes = [
    "/dashboard",
    "/services",
    "/deployments",
    "/new",
    "/project",
    "/settings",
    "/billing",
    "/servers",
    "/tunnels",
    "/intelligence",
    "/backups",
    "/transfers",
    "/admin-dashboard",
    "/topology",
    "/functions",
  ];
  return protectedPrefixes.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchUser = async () => {
      const path = window.location.pathname;

      // The auth token is an HttpOnly cookie that the browser attaches
      // automatically. There is no client-side token to read, write, or
      // sync into localStorage. A successful /auth/user/ response means
      // the cookie was valid; a 401 means the user is logged out.
      try {
        const res = await api.get("/auth/user/");
        setUser(res.data);

        if (path === "/login" || path === "/register") {
          // Avoid Next.js route-cache edge cases: force a navigation.
          window.location.replace("/dashboard");
        }
      } catch (error) {
        // Not authenticated. Clear any legacy client-side state from
        // older builds (the HttpOnly cookie itself is set/cleared by
        // the backend, not here).
        clearAuthCookies();
        setUser(null);

        // Guard against infinite redirect loops: if a session-exchanged
        // token immediately fails /auth/user/, don't redirect again as
        // it will just loop. Use a sessionStorage flag to prevent
        // rapid re-redirects.
        const loopKey = '__auth_redirect_ts';
        const lastRedirect = Number(sessionStorage.getItem(loopKey) || 0);
        const now = Date.now();
        const tooRecent = now - lastRedirect < 5000; // within 5 seconds

        if (isProtectedPath(path) && !tooRecent) {
          sessionStorage.setItem(loopKey, String(now));
          window.location.replace("/login");
        }
      } finally {
        setLoading(false);
      }
    };
    fetchUser();
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
