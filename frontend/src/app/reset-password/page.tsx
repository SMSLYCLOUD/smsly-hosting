"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { ArrowLeft, CheckCircle2, KeyRound, Loader2 } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import api from "@/lib/api";

export default function ResetPasswordPage() {
  const { toast } = useToast();
  const [uid, setUid] = useState("");
  const [token, setToken] = useState("");
  const [newPassword1, setNewPassword1] = useState("");
  const [newPassword2, setNewPassword2] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setUid(params.get("uid") || "");
    setToken(params.get("token") || "");
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (!uid.trim() || !token.trim()) {
      setError("Invalid or incomplete password reset link. Please request a new reset email.");
      return;
    }

    if (newPassword1 !== newPassword2) {
      setError("Passwords do not match.");
      return;
    }

    setLoading(true);
    try {
      await api.post("/auth/password/reset/confirm/", {
        uid: uid.trim(),
        token: token.trim(),
        new_password1: newPassword1,
        new_password2: newPassword2,
      });
      setDone(true);
      toast({
        title: "Password updated",
        description: "Your password has been reset. You can now sign in.",
      });
    } catch (err: unknown) {
      const eObj = err as { response?: { data?: Record<string, unknown> } };
      const data = eObj?.response?.data;
      const msg =
        (Array.isArray(data?.new_password2) ? String(data.new_password2[0]) : undefined) ||
        (Array.isArray(data?.new_password1) ? String(data.new_password1[0]) : undefined) ||
        (Array.isArray(data?.token) ? String(data.token[0]) : undefined) ||
        (Array.isArray(data?.uid) ? String(data.uid[0]) : undefined) ||
        (Array.isArray(data?.non_field_errors) ? String(data.non_field_errors[0]) : undefined) ||
        (typeof data?.detail === "string" ? data.detail : undefined) ||
        "Reset link is invalid or expired. Please request a new one.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-[calc(100vh-3.5rem)] flex items-center justify-center p-4">
      <Card className="w-full max-w-md border border-border bg-card">
        <CardHeader className="text-center space-y-2">
          <div className="mx-auto w-12 h-12 bg-emerald-700 rounded-xl flex items-center justify-center mb-3" aria-hidden="true">
            {done ? <CheckCircle2 className="text-white h-6 w-6" /> : <KeyRound className="text-white h-6 w-6" />}
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            {done ? "Password Reset Complete" : "Set New Password"}
          </h1>
          <CardDescription>
            {done
              ? "Your password has been updated successfully."
              : "Choose a strong new password for your account."}
          </CardDescription>
        </CardHeader>

        <CardContent>
          {!done ? (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="new_password1">New Password</Label>
                <Input
                  id="new_password1"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={newPassword1}
                  onChange={(e) => setNewPassword1(e.target.value)}
                  disabled={loading}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="new_password2">Confirm New Password</Label>
                <Input
                  id="new_password2"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={newPassword2}
                  onChange={(e) => setNewPassword2(e.target.value)}
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
                    Resetting password...
                  </>
                ) : (
                  "Reset Password"
                )}
              </Button>
            </form>
          ) : (
            <div role="status" aria-live="polite" className="text-center py-2 space-y-4">
              <p className="text-sm text-muted-foreground">
                You can now sign in with your new password.
              </p>
              <Link
                href="/login"
                className="inline-flex items-center justify-center w-full min-h-[44px] rounded-md bg-emerald-700 hover:bg-emerald-800 text-white font-semibold text-sm"
              >
                Continue to Login
              </Link>
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
