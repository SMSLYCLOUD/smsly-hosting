'use client';

import { useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import { logout } from '@/lib/auth';

export default function LogoutPage() {
  useEffect(() => {
    logout();
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

