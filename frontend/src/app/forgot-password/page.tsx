"use client";

import { useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { ArrowLeft, Loader2, Mail } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import api from "@/lib/api";

export default function ForgotPasswordPage() {
  const { toast } = useToast();
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api.post("/auth/password/reset/", { email: email.trim() });
      setSent(true);
      toast({
        title: "Reset link sent",
        description: "If an account exists with this email, you'll receive a reset link.",
      });
    } catch (err: unknown) {
      const eObj = err as { response?: { status?: number } };
      const status = eObj?.response?.status;
      // Only show generic network error for 5xx/offline; 400/404 still confirm neutrally to avoid user enumeration
      if (!status || status >= 500 || status === 429) {
        setError("Unable to process your request right now. Please try again shortly.");
      } else {
        setSent(true);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-[calc(100vh-3.5rem)] flex items-center justify-center p-4">
      <Card className="w-full max-w-md border border-border bg-card">
        <CardHeader className="text-center space-y-2">
          <div className="mx-auto w-12 h-12 bg-emerald-700 rounded-xl flex items-center justify-center mb-3" aria-hidden="true">
            <Mail className="text-white h-6 w-6" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Reset Password</h1>
          <CardDescription>
            {sent
              ? "Check your inbox for a password reset link."
              : "Enter your email to receive a password reset link."}
          </CardDescription>
        </CardHeader>

        <CardContent>
          {!sent ? (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  placeholder="you@example.com"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={loading}
                />
              </div>

              {error && (
                <div role="alert" className="text-sm text-red-700 dark:text-red-400 bg-red-500/10 border border-red-500/25 p-3 rounded">
                  {error}
                </div>
              )}

              <Button
                type="submit"
                className="w-full min-h-[44px] bg-emerald-700 hover:bg-emerald-800 text-white font-semibold"
                disabled={loading}
              >
                {loading ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                    Sending...
                  </>
                ) : (
                  "Send Reset Link"
                )}
              </Button>
            </form>
          ) : (
            <div role="status" aria-live="polite" className="text-center py-4">
              <p className="text-emerald-700 dark:text-emerald-400 font-semibold mb-2">
                Reset instructions queued
              </p>
              <p className="text-sm text-muted-foreground">
                If an account exists with this email, check your spam folder if it does not arrive within a few minutes.
              </p>
            </div>
          )}

          <div className="mt-6 text-center">
            <Link
              href="/login"
              className="text-sm text-muted-foreground hover:text-primary inline-flex items-center justify-center gap-2 min-h-[44px] px-3"
            >
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              Back to Login
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
