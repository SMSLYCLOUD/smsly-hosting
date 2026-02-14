"use client";

import { createContext, useContext, useEffect, useState } from "react";
import api from "@/lib/api";

const AUTH_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30;

function setAuthTokenCookie(token: string) {
  const isSecure = typeof window !== "undefined" && window.location.protocol === "https:";
  const cookieParts = [
    `auth_token=${encodeURIComponent(token)}`,
    "path=/",
    `max-age=${AUTH_COOKIE_MAX_AGE_SECONDS}`,
    "SameSite=Lax",
  ];
  if (isSecure) {
    cookieParts.push("Secure");
  }
  document.cookie = cookieParts.join("; ");
}

function clearAuthCookies() {
  document.cookie = "auth_token=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT; SameSite=Lax";
  document.cookie = "sessionid=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT; SameSite=Lax";
}

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

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchUser = async () => {
      // Only fetch user if there's a token - avoids 403 on public pages
      const token = localStorage.getItem('auth_token');
      if (!token) {
        setUser(null);
        setLoading(false);
        return;
      }

      try {
        // Keep the middleware cookie in sync (middleware can't read localStorage).
        setAuthTokenCookie(token);
        const res = await api.get("/auth/user/");
        setUser(res.data);

        const path = window.location.pathname;
        if (path === "/login" || path === "/register") {
          // Avoid Next.js route-cache edge cases: force a navigation.
          window.location.replace("/dashboard");
        }
      } catch (error) {
        // Token invalid or expired
        localStorage.removeItem('auth_token');
        clearAuthCookies();
        setUser(null);

        // If user was trying to access a protected page, send them to login.
        const path = window.location.pathname;
        if (path !== "/login" && path !== "/register") {
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
