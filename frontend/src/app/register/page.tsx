"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import Image from "next/image";
import { Github, Chrome, Loader2, GitBranch, Code2 } from "lucide-react";
import { resetRedirectGuard } from "@/lib/paths";
import { GridCard } from "@/components/ui/GridCard";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function RegisterPage() {
  const [formData, setFormData] = useState({
    username: "",
    email: "",
    password1: "",
    password2: "",
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [oauthProviders, setOauthProviders] = useState<Record<string, boolean> | null>(null);

  const BACKEND_URL =
    typeof window !== "undefined"
      ? window.location.origin
      : process.env.NEXT_PUBLIC_API_URL || "https://grid.trulay.co";

  useEffect(() => {
    let active = true;
    fetch(`${BACKEND_URL}/api/v1/oauth/providers/`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (active && data && typeof data === "object") setOauthProviders(data);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [BACKEND_URL]);

  const showProvider = (key: string) =>
    oauthProviders === null || oauthProviders[key] !== false;

  const hasAnyOauth =
    showProvider("github") ||
    showProvider("google") ||
    showProvider("gitlab") ||
    showProvider("bitbucket_oauth2");

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (formData.password1 !== formData.password2) {
      setError("Passwords do not match.");
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await fetch(`${BACKEND_URL}/api/v1/auth/registration/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          username: formData.username.trim(),
          email: formData.email.trim(),
          password1: formData.password1,
          password2: formData.password2,
        }),
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        if (response.status === 429 || payload?.code === "throttled") {
          const waitSec = Number(payload?.wait_seconds);
          setError(
            Number.isFinite(waitSec) && waitSec > 0
              ? `Too many registration attempts. Please wait ${Math.ceil(waitSec / 60)} minute(s) before trying again.`
              : "Too many registration attempts. Please wait a moment before trying again."
          );
          return;
        }
        const message =
          payload?.detail ||
          payload?.non_field_errors?.[0] ||
          payload?.username?.[0] ||
          payload?.email?.[0] ||
          payload?.password1?.[0] ||
          payload?.password2?.[0] ||
          payload?.error ||
          "Registration failed. Please check your input.";
        setError(String(message));
        return;
      }

      const data = await response.json().catch(() => ({}));
      if (!data?.key && !data?.token) {
        setError("Account created, but token retrieval failed. Please log in.");
        window.location.assign("/login");
        return;
      }

      resetRedirectGuard();
      window.location.assign("/dashboard");
    } catch {
      setError("Unable to connect to server. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-[calc(100vh-3.5rem)] flex flex-col relative overflow-x-hidden premium-bg">
      <div className="flex-1 flex items-center justify-center p-4 relative z-10">
        <GridCard className="w-full max-w-md rounded border border-border bg-card">
          <div className="p-6 text-center space-y-2">
            <div className="mx-auto mb-4 flex flex-col items-center gap-2">
              <Image
                src="/images/logo.png"
                alt="Grid"
                width={190}
                height={78}
                className="h-20 w-auto max-w-full rounded object-contain"
              />
            </div>
            <h1 className="text-2xl font-bold tracking-tight">Create an account</h1>
            <p className="text-sm text-muted-foreground">
              Sign up for Grid to deploy your applications.
            </p>
          </div>

          <div className="px-6 pb-6">
            {hasAnyOauth && (
              <>
                <div className="rounded overflow-hidden border border-border mb-4">
                  {showProvider("github") && (
                    <a
                      href={`${BACKEND_URL}/accounts/github/login/`}
                      className="flex items-center justify-center gap-2 h-11 px-4 text-sm font-medium hover:bg-muted/50 transition border-b border-border last:border-b-0"
                    >
                      <Github className="h-4 w-4" aria-hidden="true" />
                      Sign up with GitHub
                    </a>
                  )}
                  {showProvider("google") && (
                    <a
                      href={`${BACKEND_URL}/accounts/google/login/`}
                      className="flex items-center justify-center gap-2 h-11 px-4 text-sm font-medium hover:bg-muted/50 transition border-b border-border last:border-b-0"
                    >
                      <Chrome className="h-4 w-4" aria-hidden="true" />
                      Sign up with Google
                    </a>
                  )}
                  {showProvider("gitlab") && (
                    <a
                      href={`${BACKEND_URL}/accounts/gitlab/login/`}
                      className="flex items-center justify-center gap-2 h-11 px-4 text-sm font-medium hover:bg-muted/50 transition border-b border-border last:border-b-0"
                    >
                      <GitBranch className="h-4 w-4 text-orange-500" aria-hidden="true" />
                      Sign up with GitLab
                    </a>
                  )}
                  {showProvider("bitbucket_oauth2") && (
                    <a
                      href={`${BACKEND_URL}/accounts/bitbucket_oauth2/login/`}
                      className="flex items-center justify-center gap-2 h-11 px-4 text-sm font-medium hover:bg-muted/50 transition"
                    >
                      <Code2 className="h-4 w-4 text-blue-500" aria-hidden="true" />
                      Sign up with Bitbucket
                    </a>
                  )}
                </div>

                <div className="text-center text-xs uppercase text-muted-foreground mb-4">
                  Or continue with email
                </div>
              </>
            )}

            <form onSubmit={handleRegister} className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="username">Username</Label>
                <Input
                  id="username"
                  required
                  autoComplete="username"
                  value={formData.username}
                  onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                  disabled={isSubmitting}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  required
                  autoComplete="email"
                  value={formData.email}
                  onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                  disabled={isSubmitting}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="password1">Password</Label>
                <Input
                  id="password1"
                  type="password"
                  required
                  autoComplete="new-password"
                  value={formData.password1}
                  onChange={(e) => setFormData({ ...formData, password1: e.target.value })}
                  disabled={isSubmitting}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="password2">Confirm Password</Label>
                <Input
                  id="password2"
                  type="password"
                  required
                  autoComplete="new-password"
                  value={formData.password2}
                  onChange={(e) => setFormData({ ...formData, password2: e.target.value })}
                  disabled={isSubmitting}
                />
              </div>

              {error && (
                <div role="alert" className="text-sm text-red-700 dark:text-red-400 bg-red-500/10 border border-red-500/25 p-3 rounded">
                  {error}
                </div>
              )}

              <Button
                type="submit"
                className="w-full h-11 bg-primary hover:bg-primary/90 text-primary-foreground font-semibold rounded transition"
                disabled={isSubmitting}
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                    Creating account...
                  </>
                ) : (
                  "Sign up with Email"
                )}
              </Button>
            </form>
          </div>

          <div className="px-6 pb-6 text-center text-sm text-muted-foreground">
            Already have an account?&nbsp;
            <Link href="/login" className="underline hover:text-primary py-1">
              Sign in
            </Link>
          </div>
        </GridCard>
      </div>
    </div>
  );
}
