"use client";

import { useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Github, Chrome, Mail, ArrowLeft, Loader2, GitBranch, Code2 } from "lucide-react";
import { resetRedirectGuard } from "@/lib/paths";
import { GridCard } from "@/components/ui/GridCard";

export default function LoginPage() {
  const [showEmailForm, setShowEmailForm] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [formData, setFormData] = useState({ username: "", password: "" });

  const BACKEND_URL = typeof window !== 'undefined'
    ? window.location.origin
    : process.env.NEXT_PUBLIC_API_URL || "https://cloud.Trulay.co";

  const handleEmailLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError("");

    try {
      const identifier = formData.username.trim();
      const isEmail = identifier.includes("@");

      const response = await fetch(`${BACKEND_URL}/api/v1/auth/login/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          username: isEmail ? undefined : identifier,
          email: isEmail ? identifier : undefined,
          password: formData.password,
        }),
      });

      if (response.ok) {
        const data = await response.json();
        if (!data?.key && !data?.token) {
          setError("Login succeeded but no auth token was returned. Please try again.");
          return;
        }
        resetRedirectGuard();
        window.location.assign("/dashboard");
      } else {
        const errorData = await response.json().catch(() => ({}));
        if (response.status === 429 || errorData?.code === "throttled") {
          const waitSec = Number(errorData?.wait_seconds);
          setError(
            Number.isFinite(waitSec) && waitSec > 0
              ? `Too many login attempts. Please wait ${Math.ceil(waitSec / 60)} minute(s) before trying again.`
              : "Too many login attempts. Please wait a moment before trying again."
          );
          return;
        }
        setError(
          errorData.non_field_errors?.[0] ||
            errorData.detail ||
            errorData.error ||
            "Invalid username/email or password"
        );
      }
    } catch {
      setError("Unable to connect to server. Please try again.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col relative overflow-x-hidden premium-bg">
      <div className="flex-1 flex items-center justify-center p-4 relative z-10">

      <GridCard className="w-full max-w-md rounded border border-border bg-card">
        <div className="p-6 text-center space-y-2">
          <div className="mx-auto mb-4 flex flex-col items-center gap-2">
            <Image src="/images/logo.png" alt="Grid" width={190} height={78} className="h-20 w-auto max-w-full rounded object-contain" />
          </div>
          <h2 className="text-2xl font-bold tracking-tight">
            {showEmailForm ? "Sign in with Email" : "Welcome back"}
          </h2>
          <p className="text-sm text-muted-foreground">
            {showEmailForm
              ? "Enter your username or email and password to continue"
              : "Sign in to Grid to manage your infrastructure."}
          </p>
        </div>

        <div className="px-6 pb-6">
          {!showEmailForm ? (
            <div className="rounded overflow-hidden border border-border">
              <a
                href={`${BACKEND_URL}/accounts/github/login/`}
                className="flex items-center justify-center gap-2 h-11 px-4 text-sm font-medium hover:bg-muted/50 transition border-b border-border"
              >
                <Github className="h-4 w-4" />
                Sign in with GitHub
              </a>
              <a
                href={`${BACKEND_URL}/accounts/google/login/`}
                className="flex items-center justify-center gap-2 h-11 px-4 text-sm font-medium hover:bg-muted/50 transition border-b border-border"
              >
                <Chrome className="h-4 w-4" />
                Sign in with Google
              </a>
              <a
                href={`${BACKEND_URL}/accounts/gitlab/login/`}
                className="flex items-center justify-center gap-2 h-11 px-4 text-sm font-medium hover:bg-muted/50 transition border-b border-border"
              >
                <GitBranch className="h-4 w-4 text-orange-500" />
                Sign in with GitLab
              </a>
              <a
                href={`${BACKEND_URL}/accounts/bitbucket_oauth2/login/`}
                className="flex items-center justify-center gap-2 h-11 px-4 text-sm font-medium hover:bg-muted/50 transition"
              >
                <Code2 className="h-4 w-4 text-blue-500" />
                Sign in with Bitbucket
              </a>
            </div>
          ) : (
            <>
              <button
                onClick={() => {
                  setShowEmailForm(false);
                  setError("");
                }}
                className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition mb-4"
              >
                <ArrowLeft className="h-4 w-4" />
                Back to options
              </button>

              <form onSubmit={handleEmailLogin} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="username">Username or Email</Label>
                  <Input
                    id="username"
                    type="text"
                    placeholder="admin or admin@Trulay.co"
                    required
                    value={formData.username}
                    onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                    disabled={isLoading}
                  />
                </div>
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="password">Password</Label>
                    <Link
                      href="/forgot-password"
                      className="text-xs text-muted-foreground hover:text-primary underline"
                    >
                      Forgot password?
                    </Link>
                  </div>
                  <Input
                    id="password"
                    type="password"
                    required
                    value={formData.password}
                    onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                    disabled={isLoading}
                  />
                </div>

                {error && (
                  <div className="text-sm text-red-500 bg-red-500/10 border border-red-500/20 p-3 rounded">
                    {error}
                  </div>
                )}

                <Button
                  type="submit"
                  className="w-full h-11 bg-primary hover:bg-primary/90 text-primary-foreground font-semibold rounded transition"
                  disabled={isLoading}
                >
                  {isLoading ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Signing in...
                    </>
                  ) : (
                    "Sign in"
                  )}
                </Button>
              </form>
            </>
          )}

          {!showEmailForm && (
            <div className="mt-4">
              <div className="text-center text-xs uppercase text-muted-foreground mb-3">
                Or continue with email
              </div>
              <button
                onClick={() => setShowEmailForm(true)}
                className="w-full h-11 rounded bg-primary hover:bg-primary/90 text-primary-foreground font-semibold transition flex items-center justify-center gap-2"
              >
                <Mail className="h-4 w-4" />
                Sign in with Email
              </button>
            </div>
          )}
        </div>

        <div className="px-6 pb-6 text-center text-sm text-muted-foreground">
          Don&apos;t have an account?&nbsp;
          <Link href="/register" className="underline hover:text-primary">
            Sign up
          </Link>
        </div>
      </GridCard>
      </div>
    </div>
  );
}
