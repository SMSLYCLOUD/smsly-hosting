'use client';

import { useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import { clearAuthCookies } from '@/lib/auth-cookies';

function getAuthToken(): string | null {
  if (typeof window === 'undefined') {
    return null;
  }
  return localStorage.getItem('auth_token');
}

export default function LogoutPage() {
  useEffect(() => {
    let active = true;

    const performLogout = async () => {
      const token = getAuthToken();

      try {
        await fetch('/api/v1/auth/logout/', {
          method: 'POST',
          credentials: 'include',
          headers: {
            Accept: 'application/json',
            ...(token ? { Authorization: `Token ${token}` } : {}),
          },
        });
      } catch (error) {
        console.warn('Logout request failed:', error);
      } finally {
        localStorage.removeItem('auth_token');
        clearAuthCookies();
        if (active) {
          window.location.replace('/login');
        }
      }
    };

    performLogout();

    return () => {
      active = false;
    };
  }, []);

  return (
    <main className="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-slate-900">
      <div className="flex items-center gap-3 text-slate-700 dark:text-slate-200">
        <Loader2 className="h-5 w-5 animate-spin" />
        <span>Signing you out...</span>
      </div>
    </main>
  );
}

