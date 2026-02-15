"use client";

import { useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await api.post('/auth/password/reset/', { email });
    } catch {
      // Silently succeed to prevent user enumeration
    } finally {
      setLoading(false);
      setSent(true);
      toast({
        title: "Reset link sent",
        description: "If an account exists with this email, you'll receive a reset link."
      });
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-slate-900 p-4">
      <Card className="w-full max-w-md shadow-xl">
        <CardHeader className="text-center space-y-2">
          <div className="mx-auto w-12 h-12 bg-emerald-600 rounded-xl flex items-center justify-center mb-4">
            <Mail className="text-white h-6 w-6" />
          </div>
          <CardTitle className="text-2xl font-bold">Reset Password</CardTitle>
          <CardDescription>
            {sent 
              ? "Check your email for a reset link."
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
                  placeholder="you@example.com"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={loading}
                />
              </div>

              <Button
                type="submit"
                className="w-full bg-emerald-600 hover:bg-emerald-700 text-white"
                disabled={loading}
              >
                {loading ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Sending...
                  </>
                ) : (
                  "Send Reset Link"
                )}
              </Button>
            </form>
          ) : (
            <div className="text-center py-4">
              <p className="text-emerald-600 font-medium mb-4">✓ Email sent successfully</p>
              <p className="text-sm text-muted-foreground">
                Didn&apos;t receive it? Check your spam folder or try again.
              </p>
            </div>
          )}

          <div className="mt-6 text-center">
            <Link 
              href="/login" 
              className="text-sm text-muted-foreground hover:text-primary flex items-center justify-center gap-2"
            >
              <ArrowLeft className="h-4 w-4" />
              Back to Login
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
