"use client";

import { createContext, useContext, useEffect, useState } from "react";
import api from "@/lib/api";

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
        const res = await api.get("/auth/user/");
        setUser(res.data);
      } catch (error) {
        // Token invalid or expired
        localStorage.removeItem('auth_token');
        setUser(null);
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
