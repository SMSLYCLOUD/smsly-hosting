'use client';

import { useEffect, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Loader2 } from 'lucide-react';

function CallbackContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const token = searchParams.get('auth_token');
    const error = searchParams.get('error');

    if (token) {
      // Store token
      localStorage.setItem('auth_token', token);
      document.cookie = `auth_token=${token}; path=/; max-age=604800; SameSite=Lax`;

      // Redirect to home
      router.push('/');
    } else if (error) {
        // Handle error (maybe redirect to login with error)
        router.push(`/login?error=${encodeURIComponent(error)}`);
    } else {
        // Fallback: If no token found after short delay, go to login
        const timer = setTimeout(() => {
            if (!localStorage.getItem('auth_token')) {
                 router.push('/login');
            }
        }, 3000);
        return () => clearTimeout(timer);
    }
  }, [router, searchParams]);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-background">
      <Loader2 className="h-10 w-10 animate-spin text-primary mb-4" />
      <p className="text-muted-foreground">Completing authentication...</p>
    </div>
  );
}

export default function AuthCallbackPage() {
    return (
        <Suspense fallback={<div className="min-h-screen flex items-center justify-center"><Loader2 className="animate-spin" /></div>}>
            <CallbackContent />
        </Suspense>
    )
}
