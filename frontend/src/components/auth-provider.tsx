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
      if (!token) {
        const recovered = await exchangeSessionForToken();
        if (recovered) {
          token = recovered;
          localStorage.setItem('auth_token', recovered);
          setAuthTokenCookie(recovered);
        }
      }
      if (!token) {
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
        // Token invalid or expired
        localStorage.removeItem('auth_token');
        clearAuthCookies();
        setUser(null);

        // Only force-login when a protected page is requested.
        if (isProtectedPath(path)) {
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
