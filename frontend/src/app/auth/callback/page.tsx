"use client";

import { useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { setAuthTokenCookie, clearAuthCookies } from "@/lib/auth-cookies";

// Prevent static prerendering — this page needs runtime URL params
export const dynamic = "force-dynamic";

function getExistingToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  const fromStorage = localStorage.getItem("auth_token");
  if (fromStorage) {
    return fromStorage;
  }
  const match = document.cookie.match(/(?:^|;\s*)auth_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

async function fetchSessionToken(
  fallbackToken: string | null
): Promise<{ token: string | null; unauthorized: boolean }> {
  try {
    const response = await fetch("/api/v1/auth/session-token/", {
      method: "GET",
      credentials: "include",
      headers: {
        Accept: "application/json",
        ...(fallbackToken ? { Authorization: `Token ${fallbackToken}` } : {}),
      },
    });
    if (!response.ok) {
      return { token: null, unauthorized: response.status === 401 || response.status === 403 };
    }
    const data = await response.json();
    return { token: typeof data?.token === "string" ? data.token : null, unauthorized: false };
  } catch (error) {
    console.error("Failed to fetch session token:", error);
    return { token: null, unauthorized: false };
  }
}

function CallbackContent() {
  const searchParams = useSearchParams();
  const queryToken = searchParams.get("auth_token");

  useEffect(() => {
    let active = true;

    const completeAuth = async () => {
      const existingToken = getExistingToken();

      // Backward-compatible fallback only: older backends may still pass token in query.
      const sessionResult = queryToken
        ? { token: null, unauthorized: false }
        : await fetchSessionToken(existingToken);
      let token = queryToken || sessionResult.token;

      // During reconnect, keep an existing token if the session exchange failed
      // for a transient reason (network/proxy race), but never when explicitly unauthorized.
      if (!token && existingToken && !sessionResult.unauthorized) {
        token = existingToken;
      }

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
  }, [queryToken]);

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
