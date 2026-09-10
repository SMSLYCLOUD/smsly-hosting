"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { Loader2, CheckCircle2, XCircle, ExternalLink } from "lucide-react";
import api from "@/lib/api";

/**
 * /auth/github/setup-callback
 *
 * GitHub redirects here after the operator creates the platform's GitHub
 * App via the manifest flow (?code=<one-time code>). This page POSTs the
 * code to the backend, which exchanges it for the full credential set
 * (app id, OAuth client id/secret, private key, webhook secret), stores
 * everything, and ensures the SocialApp — zero manual pasting.
 *
 * On success the operator is sent straight to the App installation page
 * (the only remaining step), then back to settings.
 */
function GitHubSetupCallbackContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [status, setStatus] = useState<"loading" | "success" | "error">("loading");
  const [message, setMessage] = useState("Creating your GitHub App...");
  const [installUrl, setInstallUrl] = useState<string | null>(null);

  useEffect(() => {
    const code = searchParams.get("code");
    const error = searchParams.get("error");

    if (error) {
      setStatus("error");
      setMessage(
        searchParams.get("error_description") ||
          "GitHub App creation was denied."
      );
      return;
    }

    if (!code) {
      setStatus("error");
      setMessage("No setup code received from GitHub.");
      return;
    }

    const exchangeCode = async () => {
      try {
        const res = await api.post("/integrations/github/app-manifest/setup/", {
          code,
        });
        const url: string | null = res.data?.install_url || null;
        setInstallUrl(url);
        setStatus("success");
        setMessage(
          `GitHub App created${res.data?.app_slug ? ` (@${res.data.app_slug})` : ""}! ` +
            `All credentials were stored automatically.`
        );
      } catch (e: unknown) {
        setStatus("error");
        const axiosErr = e as { response?: { data?: { error?: string; detail?: string } } };
        const detail =
          axiosErr?.response?.data?.error ||
          axiosErr?.response?.data?.detail ||
          "Failed to complete GitHub App setup.";
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
            {installUrl ? (
              <a
                href={installUrl}
                className="mt-4 inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
              >
                Install App on your repositories <ExternalLink className="h-4 w-4" />
              </a>
            ) : null}
            <p className="mt-3 text-sm text-muted-foreground">
              After installing,{" "}
              <button
                onClick={() => router.push("/settings?tab=git")}
                className="text-primary underline hover:no-underline"
              >
                return to Settings
              </button>
            </p>
          </>
        )}
        {status === "error" && (
          <>
            <XCircle className="mx-auto h-10 w-10 text-destructive" />
            <p className="mt-4 font-medium text-foreground">
              GitHub App Setup Failed
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

export default function GitHubSetupCallbackPage() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center"><Loader2 className="h-8 w-8 animate-spin text-muted-foreground" /></div>}>
      <GitHubSetupCallbackContent />
    </Suspense>
  );
}
