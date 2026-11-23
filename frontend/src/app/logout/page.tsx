'use client';

import { useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import { logout } from '@/lib/auth';

export default function LogoutPage() {
  useEffect(() => {
    logout();
  }, []);

  return (
    <div
      role="status"
      aria-live="polite"
      className="min-h-[calc(100vh-3.5rem)] flex items-center justify-center"
    >
      <div className="flex items-center gap-3 text-slate-700 dark:text-slate-200">
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
        <span>Signing you out...</span>
      </div>
    </div>
  );
}
