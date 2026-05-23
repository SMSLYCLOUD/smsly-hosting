"use client";

import { createContext, useContext, useEffect, useState } from "react";
import api from "@/lib/api";
import { setAuthTokenCookie, clearAuthCookies } from "@/lib/auth-cookies";

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
    const exchangeSessionForToken = async (): Promise<string | null> => {
      try {
        const response = await fetch("/api/v1/auth/session-token/", {
          method: "GET",
          credentials: "include",
          headers: { Accept: "application/json" },
        });
        if (!response.ok) {
          return null;
        }
        const data = await response.json();
        return typeof data?.token === "string" ? data.token : null;
      } catch {
        return null;
      }
    };

    const fetchUser = async () => {
      const path = window.location.pathname;
      // Prefer API token, but recover from a valid session after OAuth flows.
      let token = localStorage.getItem('auth_token');
      let tokenFromSession = false;
      if (!token) {
        const recovered = await exchangeSessionForToken();
        if (recovered) {
          token = recovered;
          tokenFromSession = true;
          localStorage.setItem('auth_token', recovered);
          setAuthTokenCookie(recovered);
        }
      }
      if (!token) {
        if (process.env.NODE_ENV === "development") {
          setUser({
            pk: 1,
            username: "dev_user",
            email: "dev@example.com",
            first_name: "Dev",
            last_name: "User",
          });
          setLoading(false);
          return;
        }
        setUser(null);
        setLoading(false);
        if (isProtectedPath(path)) {
          window.location.replace("/login");
        }
        return;
      }

      try {
        // Keep the middleware cookie in sync (middleware can't read localStorage).
        setAuthTokenCookie(token);
        const res = await api.get("/auth/user/");
        setUser(res.data);

        if (path === "/login" || path === "/register") {
          // Avoid Next.js route-cache edge cases: force a navigation.
          window.location.replace("/dashboard");
        }
      } catch (error) {
        // Token invalid or expired — clear everything.
        localStorage.removeItem('auth_token');
        clearAuthCookies();
        setUser(null);

        // Guard against infinite redirect loops: if a session-exchanged token
        // immediately fails /auth/user/, don't redirect again as it will just
        // loop. Also use a sessionStorage flag to prevent rapid re-redirects.
        const loopKey = '__auth_redirect_ts';
        const lastRedirect = Number(sessionStorage.getItem(loopKey) || 0);
        const now = Date.now();
        const tooRecent = now - lastRedirect < 5000; // within 5 seconds

        if (isProtectedPath(path) && !tokenFromSession && !tooRecent) {
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
