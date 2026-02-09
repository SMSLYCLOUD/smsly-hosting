"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

export default function AuthCallbackPage() {
  const router = useRouter();

  useEffect(() => {
    // In session-based auth (django-allauth default), the cookie is set by the backend response.
    // We just need to redirect to the dashboard.
    // If we were using token auth, we would parse ?token=... from URL here.

    // Slight delay to ensure cookies are settled
    const timer = setTimeout(() => {
        router.push("/dashboard");
    }, 1000);

    return () => clearTimeout(timer);
  }, [router]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-slate-900">
      <div className="flex flex-col items-center space-y-4">
        <Loader2 className="h-10 w-10 text-indigo-600 animate-spin" />
        <p className="text-muted-foreground font-medium">Authenticating...</p>
      </div>
    </div>
  );
}
