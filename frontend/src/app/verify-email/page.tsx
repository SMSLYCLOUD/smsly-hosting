"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { CheckCircle2, Loader2, MailCheck, AlertCircle } from "lucide-react";
import api from "@/lib/api";

export default function VerifyEmailPage() {
  const [status, setStatus] = useState<"verifying" | "success" | "error">("verifying");
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const key = params.get("key") || "";

    if (!key.trim()) {
      setStatus("error");
      setErrorMessage("Verification key is missing. Check the link in your email.");
      return;
    }

    let cancelled = false;
    api
      .post("/auth/registration/verify-email/", { key: key.trim() })
      .then(() => {
        if (!cancelled) {
          setStatus("success");
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const eObj = err as { response?: { data?: Record<string, unknown> } };
        const data = eObj?.response?.data;
        const msg =
          (typeof data?.detail === "string" ? data.detail : undefined) ||
          (typeof data?.error === "string" ? data.error : undefined) ||
          "This verification link is invalid or has already been used.";
        setErrorMessage(msg);
        setStatus("error");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="min-h-[calc(100vh-3.5rem)] flex items-center justify-center p-4">
      <Card className="w-full max-w-md border border-border bg-card">
        <CardHeader className="text-center space-y-2">
          <div className="mx-auto w-12 h-12 bg-emerald-700 rounded-xl flex items-center justify-center mb-3" aria-hidden="true">
            {status === "verifying" ? (
              <MailCheck className="text-white h-6 w-6" />
            ) : status === "success" ? (
              <CheckCircle2 className="text-white h-6 w-6" />
            ) : (
              <AlertCircle className="text-white h-6 w-6" />
            )}
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            {status === "verifying"
              ? "Verifying Email"
              : status === "success"
                ? "Email Verified"
                : "Verification Failed"}
          </h1>
          <CardDescription>
            {status === "verifying"
              ? "Confirming your email address..."
              : status === "success"
                ? "Your email address has been confirmed."
                : "We could not verify your email address."}
          </CardDescription>
        </CardHeader>

        <CardContent className="text-center space-y-4">
          {status === "verifying" && (
            <div className="flex items-center justify-center py-4 text-muted-foreground gap-2">
              <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
              <span className="text-sm">Please wait...</span>
            </div>
          )}

          {status === "success" && (
            <div role="status" aria-live="polite" className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Your account is active. You can now sign in to Grid.
              </p>
              <Button asChild className="w-full min-h-[44px] bg-emerald-700 hover:bg-emerald-800 text-white font-semibold">
                <Link href="/login">Continue to Login</Link>
              </Button>
            </div>
          )}

          {status === "error" && (
            <div role="alert" className="space-y-4">
              <div className="text-sm text-red-700 dark:text-red-400 bg-red-500/10 border border-red-500/25 p-3 rounded">
                {errorMessage}
              </div>
              <Button asChild variant="outline" className="w-full min-h-[44px]">
                <Link href="/login">Back to Login</Link>
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
