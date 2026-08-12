"use client";

import { useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { Github, Chrome, Loader2, GitBranch, Code2 } from "lucide-react";
import { resetRedirectGuard } from "@/lib/paths";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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

  const BACKEND_URL =
    typeof window !== "undefined"
      ? window.location.origin
      : process.env.NEXT_PUBLIC_API_URL || "https://cloud.trulay.co";

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
        // Rate-limit responses use the shared {error, code, status} envelope
        // with code="throttled" and an optional wait_seconds field. Show a
        // distinct message instead of the generic "check your input" fallback.
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

      // The auth token is delivered as an HttpOnly cookie by the
      // backend's Set-Cookie header; the browser stores and attaches
      // it automatically on subsequent requests because of
      // ``credentials: "include"``. We do not write the token to
      // localStorage — the cookie is the only credential.
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
    <div className="min-h-screen flex flex-col relative overflow-x-hidden cloud-bg">
      {/* Decorative Orbs */}
      <div className="floating-orb w-[400px] h-[400px] bg-primary/10 -top-20 -left-20" />
      <div className="floating-orb w-[300px] h-[300px] bg-cyan-500/10 bottom-20 right-20" style={{ animationDelay: '-4s' }} />

      <div className="flex-1 flex items-center justify-center p-4 relative z-10">
        <Card className="w-full max-w-md card-premium rounded-2xl">
          <CardHeader className="text-center space-y-2">
            <div className="mx-auto mb-4 flex flex-col items-center gap-2">
              <Image
                src="/images/logo.png"
                alt="Grid"
                width={190}
                height={78}
                className="h-20 w-auto max-w-full rounded-xl object-contain shadow-md"
              />
            </div>
            <CardTitle className="text-2xl font-bold tracking-tight">Create an account</CardTitle>
            <CardDescription>
              Sign up for Grid to deploy your applications.
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-4">
            <Button variant="outline" className="w-full h-11 relative" asChild>
              <a href={`${BACKEND_URL}/accounts/github/login/`}>
                <Github className="mr-2 h-4 w-4" />
                Sign up with GitHub
              </a>
            </Button>
            <Button variant="outline" className="w-full h-11 relative" asChild>
              <a href={`${BACKEND_URL}/accounts/google/login/`}>
                <Chrome className="mr-2 h-4 w-4" />
                Sign up with Google
              </a>
            </Button>
            <Button variant="outline" className="w-full h-11 relative" asChild>
              <a href={`${BACKEND_URL}/accounts/gitlab/login/`}>
                <GitBranch className="mr-2 h-4 w-4 text-orange-500" />
                Sign up with GitLab
              </a>
            </Button>
            <Button variant="outline" className="w-full h-11 relative" asChild>
              <a href={`${BACKEND_URL}/accounts/bitbucket_oauth2/login/`}>
                <Code2 className="mr-2 h-4 w-4 text-blue-500" />
                Sign up with Bitbucket
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

            <form onSubmit={handleRegister} className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="username">Username</Label>
                <Input
                  id="username"
                  required
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
                  value={formData.password2}
                  onChange={(e) => setFormData({ ...formData, password2: e.target.value })}
                  disabled={isSubmitting}
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
                disabled={isSubmitting}
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Creating account...
                  </>
                ) : (
                  "Sign up with Email"
                )}
              </Button>
            </form>
          </CardContent>

          <CardFooter className="flex justify-center text-sm text-muted-foreground">
            Already have an account?&nbsp;
            <Link href="/login" className="underline hover:text-primary">
              Sign in
            </Link>
          </CardFooter>
        </Card>
      </div>
    </div>
  );
}
