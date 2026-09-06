"use client";

import { useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { clearAuthCookies } from "@/lib/auth-cookies";
import { resetRedirectGuard } from "@/lib/paths";

// Prevent static prerendering — this page needs runtime URL params
export const dynamic = "force-dynamic";

async function fetchSessionToken(): Promise<{ token: string | null; unauthorized: boolean; requires2fa: boolean }> {
  try {
    const response = await fetch("/api/v1/auth/session-token/", {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      return { token: null, unauthorized: response.status === 401 || response.status === 403, requires2fa: false };
    }
    const data = await response.json();
    return {
      token: typeof data?.token === "string" ? data.token : null,
      unauthorized: false,
      // 2FA-enrolled SSO users get their token only after the TOTP
      // step — the login page shows the challenge when flagged.
      requires2fa: data?.requires_2fa === true,
    };
  } catch (error) {
    console.error("Failed to fetch session token:", error);
    return { token: null, unauthorized: false, requires2fa: false };
  }
}

function CallbackContent() {
  const searchParams = useSearchParams();
  const queryToken = searchParams.get("auth_token");

  useEffect(() => {
    let active = true;

    const completeAuth = async () => {
      // Backward-compatible fallback only: older backends may still
      // pass token in query. Newer backends set the HttpOnly cookie
      // on /api/v1/accounts/<provider>/login/callback/ via Set-Cookie
      // and the browser attaches it automatically; we do not need to
      // read or store the token client-side.
      const sessionResult = queryToken
        ? { token: null, unauthorized: false, requires2fa: false }
        : await fetchSessionToken();
      const token = queryToken || sessionResult.token;

      if (!active) {
        return;
      }

      if (sessionResult.requires2fa) {
        // OAuth identity verified, but the account has 2FA enrolled:
        // the pending handshake lives in the session, so the login
        // page can present the TOTP challenge directly.
        resetRedirectGuard();
        window.location.replace("/login?2fa=1");
        return;
      }

      if (token) {
        // Older backends may return a token in the body. We use it
        // only as a sign that the auth flow completed; the actual
        // credential is whatever cookie the backend's Set-Cookie
        // header attached. No localStorage write, no client-side
        // cookie write — the backend owns the cookie lifecycle.
        resetRedirectGuard();
        window.location.replace("/dashboard");
        return;
      }

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
