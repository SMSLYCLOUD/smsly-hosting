"use client";

import { useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";

// Prevent static prerendering — this page needs runtime URL params
export const dynamic = "force-dynamic";

function CallbackContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    // Parse the auth_token injected by CustomAccountAdapter / CustomSocialAccountAdapter
    const token = searchParams.get("auth_token");

    if (token) {
      // Store the DRF token for API calls
      localStorage.setItem("auth_token", token);

      // Also set a cookie so the middleware can detect authenticated state
      document.cookie = `sessionid=${token}; path=/; max-age=${60 * 60 * 24 * 30}; SameSite=Lax`;
    }

    // Redirect to dashboard after token is stored
    const timer = setTimeout(() => {
      router.push("/dashboard");
    }, 500);

    return () => clearTimeout(timer);
  }, [router, searchParams]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-slate-900">
      <div className="flex flex-col items-center space-y-4">
        <Loader2 className="h-10 w-10 text-emerald-600 animate-spin" />
        <p className="text-muted-foreground font-medium">Authenticating...</p>
      </div>
    </div>
  );
}

export default function AuthCallbackPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-slate-900">
        <Loader2 className="h-10 w-10 text-emerald-600 animate-spin" />
      </div>
    }>
      <CallbackContent />
    </Suspense>
  );
}
