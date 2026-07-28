"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { Loader2, CheckCircle2, XCircle } from "lucide-react";
import api from "@/lib/api";

/**
 * /auth/github/app/callback
 *
 * GitHub redirects here after the user installs the GitHub App.
 * This page extracts the `installation_id` param and POSTs it to
 * the backend to link the installation to the current user.
 */
function GitHubAppCallbackContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [status, setStatus] = useState<"loading" | "success" | "error">("loading");
  const [message, setMessage] = useState("Linking GitHub App installation...");

  useEffect(() => {
    const installationId = searchParams.get("installation_id");
    const setupAction = searchParams.get("setup_action");

    if (setupAction === "update") {
      setStatus("success");
      setMessage("GitHub App installation updated!");
      setTimeout(() => router.push("/settings"), 1500);
      return;
    }

    if (!installationId) {
      setStatus("error");
      setMessage("No installation ID received from GitHub.");
      return;
    }

    const numId = Number(installationId);
    if (!Number.isFinite(numId) || numId <= 0) {
      setStatus("error");
      setMessage("Invalid installation ID received from GitHub.");
      return;
    }

    const linkInstallation = async () => {
      try {
        const res = await api.post("/integrations/github/app/callback/", {
          installation_id: numId,
        });
        setStatus("success");
        setMessage(
          `GitHub App linked to ${res.data?.account_login || "your account"}! ` +
          `${res.data?.repositories?.length || 0} repositories accessible.`
        );
        setTimeout(() => router.push("/settings"), 2000);
      } catch (e: unknown) {
        const axiosErr = e as { response?: { status?: number; data?: { error?: string; detail?: string } } };
        const statusCode = axiosErr?.response?.status;
        if (statusCode === 401) {
          setStatus("error");
          setMessage("Your session has expired. Redirecting to login...");
          setTimeout(() => router.push("/login"), 2000);
          return;
        }
        setStatus("error");
        const detail =
          axiosErr?.response?.data?.error ||
          axiosErr?.response?.data?.detail ||
          "Failed to link GitHub App installation.";
        setMessage(String(detail));
      }
    };

    linkInstallation();
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
              GitHub App Link Failed
            </p>
            <p className="mt-1 text-sm text-muted-foreground">{message}</p>
            <button
              onClick={() => router.push("/settings")}
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

export default function GitHubAppCallbackPage() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center"><Loader2 className="h-8 w-8 animate-spin text-muted-foreground" /></div>}>
      <GitHubAppCallbackContent />
    </Suspense>
  );
}
