"use client";

import { useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Github, Chrome, Mail, ArrowLeft, Loader2, GitBranch, Code2 } from "lucide-react";
import { resetRedirectGuard } from "@/lib/paths";

export default function LoginPage() {
  const [showEmailForm, setShowEmailForm] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [formData, setFormData] = useState({ username: "", password: "" });

  // Use absolute URL for production - NEXT_PUBLIC vars are baked at build time
  const BACKEND_URL = typeof window !== 'undefined'
    ? window.location.origin
    : process.env.NEXT_PUBLIC_API_URL || "https://cloud.trulay.co";

  const handleEmailLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError("");

    try {
      const identifier = formData.username.trim();
      const isEmail = identifier.includes("@");

      // dj-rest-auth validates the email field format, so only include it when it
      // actually looks like an email. Otherwise you'll get a 400 ("Enter a valid email address.").
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
        // The backend's response is the canonical token — it is also
        // delivered as an HttpOnly cookie via Set-Cookie, which the
        // browser stores and attaches automatically thanks to
        // ``credentials: "include"``. We no longer write the token to
        // localStorage; the body is kept around only so we can confirm
        // the login succeeded.
        const data = await response.json();
        if (!data?.key && !data?.token) {
          setError("Login succeeded but no auth token was returned. Please try again.");
          return;
        }

        resetRedirectGuard();
        // Full reload avoids Next.js route-cache edge cases around auth redirects.
        window.location.assign("/dashboard");
      } else {
        const errorData = await response.json().catch(() => ({}));
        // Rate-limit responses use the shared {error, code, status} envelope
        // with code="throttled" and an optional wait_seconds field. Show a
        // distinct message instead of the generic "wrong password" fallback.
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
    <div className="min-h-screen flex flex-col relative overflow-x-hidden cloud-bg">
      {/* Decorative Orbs */}
      <div className="floating-orb w-[400px] h-[400px] bg-primary/10 -top-20 -left-20" />
      <div className="floating-orb w-[300px] h-[300px] bg-cyan-500/10 bottom-20 right-20" style={{ animationDelay: '-4s' }} />

      <div className="flex-1 flex items-center justify-center p-4 relative z-10">

      <Card className="w-full max-w-md card-premium rounded-2xl">
        <CardHeader className="text-center space-y-2">
          <div className="mx-auto mb-4 flex flex-col items-center gap-2">
            <Image src="/images/logo.png" alt="Grid" width={190} height={78} className="h-20 w-auto max-w-full rounded-xl object-contain shadow-md" />
          </div>
          <CardTitle className="text-2xl font-bold tracking-tight">
            {showEmailForm ? "Sign in with Email" : "Welcome back"}
          </CardTitle>
          <CardDescription>
            {showEmailForm
              ? "Enter your username or email and password to continue"
              : "Sign in to Grid to manage your infrastructure."}
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          {!showEmailForm ? (
            <>
              {/* Social Login Buttons */}
              <Button variant="outline" className="w-full h-11 relative" asChild>
                <a href={`${BACKEND_URL}/accounts/github/login/`}>
                  <Github className="mr-2 h-4 w-4" />
                  Sign in with GitHub
                </a>
              </Button>
              <Button variant="outline" className="w-full h-11 relative" asChild>
                <a href={`${BACKEND_URL}/accounts/google/login/`}>
                  <Chrome className="mr-2 h-4 w-4" />
                  Sign in with Google
                </a>
              </Button>
              <Button variant="outline" className="w-full h-11 relative" asChild>
                <a href={`${BACKEND_URL}/accounts/gitlab/login/`}>
                  <GitBranch className="mr-2 h-4 w-4 text-orange-500" />
                  Sign in with GitLab
                </a>
              </Button>
              <Button variant="outline" className="w-full h-11 relative" asChild>
                <a href={`${BACKEND_URL}/accounts/bitbucket_oauth2/login/`}>
                  <Code2 className="mr-2 h-4 w-4 text-blue-500" />
                  Sign in with Bitbucket
                </a>
              </Button>

              <div className="relative">
                <div className="absolute inset-0 flex items-center">
                  <span className="w-full border-t" />
                </div>
                <div className="relative flex justify-center text-xs uppercase">
                  <span className="bg-background px-2 text-muted-foreground">
                    Or continue with email
                  </span>
                </div>
              </div>

              <Button
                className="w-full h-11 btn-shimmer bg-gradient-to-r from-emerald-600 to-green-600 hover:from-emerald-700 hover:to-green-700 text-white font-semibold shadow-lg shadow-emerald-500/20"
                onClick={() => setShowEmailForm(true)}
              >
                <Mail className="mr-2 h-4 w-4" />
                Sign in with Email
              </Button>
            </>
          ) : (
            <>
              {/* Email Login Form */}
              <Button
                variant="ghost"
                size="sm"
                className="mb-2 -ml-2"
                onClick={() => {
                  setShowEmailForm(false);
                  setError("");
                }}
              >
                <ArrowLeft className="mr-2 h-4 w-4" />
                Back to options
              </Button>

              <form onSubmit={handleEmailLogin} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="username">Username or Email</Label>
                  <Input
                    id="username"
                    type="text"
                    placeholder="admin or admin@trulay.co"
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
                  <div className="text-sm text-red-500 bg-red-50 dark:bg-red-950/50 p-3 rounded-md">
                    {error}
                  </div>
                )}

                <Button
                  type="submit"
                  className="w-full h-11 btn-shimmer bg-gradient-to-r from-emerald-600 to-green-600 hover:from-emerald-700 hover:to-green-700 text-white font-semibold shadow-lg shadow-emerald-500/20"
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
        </CardContent>

        <CardFooter className="flex justify-center text-sm text-muted-foreground">
          Don&apos;t have an account?&nbsp;
          <Link href="/register" className="underline hover:text-primary">
            Sign up
          </Link>
        </CardFooter>
      </Card>
      </div>
    </div>
  );
}
