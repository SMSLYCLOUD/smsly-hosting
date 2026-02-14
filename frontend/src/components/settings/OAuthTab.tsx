"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Loader2, Check, Eye, EyeOff, Github } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import api from "@/lib/api";

interface OAuthStatus {
  github: boolean;
  google: boolean;
}

interface OAuthCreds {
  github: { configured: boolean; client_id?: string };
  google: { configured: boolean; client_id?: string };
}

export function OAuthTab() {
  const { toast } = useToast();
  const [status, setStatus] = useState<OAuthStatus>({ github: false, google: false });
  const [creds, setCreds] = useState<OAuthCreds>({
    github: { configured: false },
    google: { configured: false },
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showSecrets, setShowSecrets] = useState({ github: false, google: false });
  const [isAdmin, setIsAdmin] = useState(true);

  // Form state
  const [githubClientId, setGithubClientId] = useState("");
  const [githubSecret, setGithubSecret] = useState("");
  const [googleClientId, setGoogleClientId] = useState("");
  const [googleSecret, setGoogleSecret] = useState("");

  useEffect(() => {
    fetchStatus();
    fetchCreds();
  }, []);

  const fetchStatus = async () => {
    try {
      const res = await api.get("/oauth/status/");
      setStatus(res.data);
    } catch (err) {
      const statusCode = (err as any)?.response?.status;
      if (statusCode === 403) setIsAdmin(false);
      console.error("Failed to fetch OAuth status", err);
    }
  };

  const fetchCreds = async () => {
    try {
      const res = await api.get("/oauth/credentials/");
      setCreds(res.data);
      setIsAdmin(true);
      if (res.data.github?.client_id) setGithubClientId(res.data.github.client_id);
      if (res.data.google?.client_id) setGoogleClientId(res.data.google.client_id);
    } catch (err) {
      // Non-admin users will get 403 — that's fine
      console.error("Failed to fetch OAuth credentials (admin-only)", err);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async (provider: "github" | "google") => {
    if (!isAdmin) {
      toast({
        title: "Forbidden",
        description: "Admin access required.",
        variant: "destructive",
      });
      return;
    }

    setSaving(true);
    try {
      const payload: Record<string, unknown> = {};
      if (provider === "github") {
        payload.github = { client_id: githubClientId, client_secret: githubSecret };
      } else {
        payload.google = { client_id: googleClientId, client_secret: googleSecret };
      }

      await api.post("/oauth/credentials/", payload);
      toast({
        title: `${provider === "github" ? "GitHub" : "Google"} OAuth saved`,
        description: "Credentials updated. Users can now sign in with this provider.",
      });
      fetchStatus();
      fetchCreds();
      // Clear secret fields after save
      if (provider === "github") setGithubSecret("");
      else setGoogleSecret("");
    } catch (err) {
      const statusCode = (err as any)?.response?.status;
      const apiDetail = (err as any)?.response?.data?.detail || (err as any)?.response?.data?.error;

      let description = "Failed to save OAuth credentials.";
      if (statusCode === 401) description = "Unauthorized. Please log in again.";
      else if (statusCode === 403) description = apiDetail || "Forbidden. Admin access required.";
      else if (apiDetail) description = apiDetail;

      toast({ title: "Error", description, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">OAuth (Admin Only)</CardTitle>
          <CardDescription>Only admins can configure OAuth providers for the platform.</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Log in with an admin account to manage GitHub/Google OAuth credentials.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {/* GitHub OAuth */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Github className="h-6 w-6" />
              <div>
                <CardTitle className="text-lg">GitHub OAuth</CardTitle>
                <CardDescription>Allow users to sign in with their GitHub account.</CardDescription>
              </div>
            </div>
            <Badge
              variant={status.github ? "default" : "secondary"}
              className={status.github ? "bg-green-500/10 text-green-500 border-green-500/30" : ""}
            >
              {status.github ? "Configured" : "Not Configured"}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="gh-client-id">Client ID</Label>
            <Input
              id="gh-client-id"
              placeholder="Ov23li..."
              value={githubClientId}
              onChange={(e) => setGithubClientId(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="gh-secret">Client Secret</Label>
            <div className="relative">
              <Input
                id="gh-secret"
                type={showSecrets.github ? "text" : "password"}
                placeholder={status.github ? "••••••••••••• (already set)" : "Enter client secret"}
                value={githubSecret}
                onChange={(e) => setGithubSecret(e.target.value)}
              />
              <button
                type="button"
                onClick={() => setShowSecrets((s) => ({ ...s, github: !s.github }))}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                {showSecrets.github ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">
              Callback URL: <code className="text-[11px] bg-muted px-1 py-0.5 rounded">{typeof window !== "undefined" ? window.location.origin : ""}/accounts/github/login/callback/</code>
            </p>
            <Button onClick={() => handleSave("github")} disabled={saving || !githubClientId}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Check className="mr-2 h-4 w-4" />}
              Save GitHub
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Google OAuth */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none">
                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4"/>
                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
              </svg>
              <div>
                <CardTitle className="text-lg">Google OAuth</CardTitle>
                <CardDescription>Allow users to sign in with their Google account.</CardDescription>
              </div>
            </div>
            <Badge
              variant={status.google ? "default" : "secondary"}
              className={status.google ? "bg-green-500/10 text-green-500 border-green-500/30" : ""}
            >
              {status.google ? "Configured" : "Not Configured"}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="gg-client-id">Client ID</Label>
            <Input
              id="gg-client-id"
              placeholder="123456789-abc..."
              value={googleClientId}
              onChange={(e) => setGoogleClientId(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="gg-secret">Client Secret</Label>
            <div className="relative">
              <Input
                id="gg-secret"
                type={showSecrets.google ? "text" : "password"}
                placeholder={status.google ? "••••••••••••• (already set)" : "Enter client secret"}
                value={googleSecret}
                onChange={(e) => setGoogleSecret(e.target.value)}
              />
              <button
                type="button"
                onClick={() => setShowSecrets((s) => ({ ...s, google: !s.google }))}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                {showSecrets.google ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">
              Redirect URI: <code className="text-[11px] bg-muted px-1 py-0.5 rounded">{typeof window !== "undefined" ? window.location.origin : ""}/accounts/google/login/callback/</code>
            </p>
            <Button onClick={() => handleSave("google")} disabled={saving || !googleClientId}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Check className="mr-2 h-4 w-4" />}
              Save Google
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
