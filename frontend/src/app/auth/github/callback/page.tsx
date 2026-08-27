"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { Loader2, CheckCircle2, XCircle } from "lucide-react";
import api from "@/lib/api";

/**
 * /auth/github/callback
 *
 * GitHub redirects here after the user authorizes the OAuth app.
 * This page extracts the `code` param and POSTs it to the backend API
 * to exchange it for an access token and link the GitHub account.
 *
 * No server-side sessions or cookies are needed — the auth token
 * from localStorage is sent via the Authorization header.
 */
function GitHubCallbackContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [status, setStatus] = useState<"loading" | "success" | "error">("loading");
  const [message, setMessage] = useState("Connecting your GitHub account...");

  useEffect(() => {
    const code = searchParams.get("code");
    const state = searchParams.get("state");
    const error = searchParams.get("error");

    if (error) {
      setStatus("error");
      setMessage(
        searchParams.get("error_description") ||
          "GitHub authorization was denied."
      );
      return;
    }

    if (!code) {
      setStatus("error");
      setMessage("No authorization code received from GitHub.");
      return;
    }

    // Exchange the code with our backend API
    const exchangeCode = async () => {
      try {
        const res = await api.post("/integrations/github/oauth-callback/", {
          code, state,
        });
        setStatus("success");
        setMessage(
          `GitHub connected as ${res.data?.account?.login || "your account"}!`
        );
        // Redirect to Git & Deploy tab after a brief moment
        setTimeout(() => router.push("/settings?tab=git"), 2000);
      } catch (e: unknown) {
        setStatus("error");
        const axiosErr = e as { response?: { data?: { error?: string; detail?: string } } };
        const detail =
          axiosErr?.response?.data?.error ||
          axiosErr?.response?.data?.detail ||
          "Failed to connect GitHub account.";
        setMessage(String(detail));
      }
    };

    exchangeCode();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="w-full max-w-md rounded-xl border bg-card p-8 text-center shadow-sm">
        {status === "loading" && (
          <>
            <Loader2 className="mx-auto h-10 w-10 animate-spin text-muted-foreground" />
            <p className="mt-4 text-muted-foreground">{message}</p>
          </>
        )}
        {status === "success" && (
          <>
            <CheckCircle2 className="mx-auto h-10 w-10 text-green-500" />
            <p className="mt-4 font-medium text-foreground">{message}</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Redirecting to settings...
            </p>
          </>
        )}
        {status === "error" && (
          <>
            <XCircle className="mx-auto h-10 w-10 text-destructive" />
            <p className="mt-4 font-medium text-foreground">
              GitHub Connection Failed
            </p>
            <p className="mt-1 text-sm text-muted-foreground">{message}</p>
            <button
              onClick={() => router.push("/settings?tab=git")}
              className="mt-4 text-sm text-primary underline hover:no-underline"
            >
              Return to Settings
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export default function GitHubCallbackPage() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center"><Loader2 className="h-8 w-8 animate-spin text-muted-foreground" /></div>}>
      <GitHubCallbackContent />
    </Suspense>
  );
}
