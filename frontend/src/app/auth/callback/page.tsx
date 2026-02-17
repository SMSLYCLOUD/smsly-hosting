"use client";

import { useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { setAuthTokenCookie, clearAuthCookies } from "@/lib/auth-cookies";

// Prevent static prerendering — this page needs runtime URL params
export const dynamic = "force-dynamic";

async function fetchSessionToken(): Promise<string | null> {
  try {
    const response = await fetch("/api/v1/auth/session-token/", {
      method: "GET",
      credentials: "include",
      headers: {
        Accept: "application/json",
      },
    });
    if (!response.ok) {
      return null;
    }
    const data = await response.json();
    return typeof data?.token === "string" ? data.token : null;
  } catch (error) {
    console.error("Failed to fetch session token:", error);
    return null;
  }
}

function CallbackContent() {
  const searchParams = useSearchParams();

  useEffect(() => {
    let active = true;

    const completeAuth = async () => {
      // Backward-compatible fallback only: older backends may still pass token in query.
      const queryToken = searchParams.get("auth_token");
      const token = queryToken || await fetchSessionToken();

      if (!active) {
        return;
      }

      if (token) {
        localStorage.setItem("auth_token", token);
        setAuthTokenCookie(token);

        // Full reload avoids Next.js route-cache edge cases around auth redirects.
        window.location.replace("/dashboard");
        return;
      }

      localStorage.removeItem("auth_token");
      clearAuthCookies();
      window.location.replace("/login");
    };

    completeAuth();

    return () => {
      active = false;
    };
  }, [searchParams]);

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
