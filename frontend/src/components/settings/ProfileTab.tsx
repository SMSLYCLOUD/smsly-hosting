"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import api from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { useToast } from "@/components/ui/use-toast";

export function ProfileTab() {
  const { toast } = useToast();
  const { isSuperuser } = usePermissions();
  const [saving, setSaving] = useState(false);
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");

  const fetchProfile = useCallback(async () => {
    try {
      const res = await api.get("/auth/user/");
      setFirstName(res.data.first_name || "");
      setLastName(res.data.last_name || "");
      setEmail(res.data.email || "");
    } catch (err) {
      console.error("Failed to fetch profile", err);
    }
  }, []);

  useEffect(() => {
    fetchProfile();
  }, [fetchProfile]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload: Record<string, string> = {
        first_name: firstName,
        last_name: lastName,
      };
      if (isSuperuser) {
        payload.email = email;
      }
      await api.patch("/auth/user/", payload);
      toast({ title: "Profile saved", description: "Your profile has been updated." });
    } catch (err: any) {
      const data = err?.response?.data;
      const msg = (Array.isArray(data?.email) ? data.email[0] : data?.email) || data?.detail || "Failed to save profile.";
      toast({ title: "Error", description: String(msg), variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Profile Settings</CardTitle>
        <CardDescription>Update your personal information.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label>First Name</Label>
            <Input placeholder="John" value={firstName} onChange={(e) => setFirstName(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label>Last Name</Label>
            <Input placeholder="Doe" value={lastName} onChange={(e) => setLastName(e.target.value)} />
          </div>
        </div>
        <div className="space-y-2">
          <Label>Email</Label>
          <Input
            type="email"
            placeholder="john@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={!isSuperuser}
            readOnly={!isSuperuser}
          />
          {!isSuperuser && (
            <p className="text-xs text-muted-foreground">
              Email is tied to your login identity and cannot be changed directly.
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <Button onClick={handleSave} disabled={saving}>
            {saving ? "Saving..." : "Save Changes"}
          </Button>
          <Button variant="outline" asChild>
            <Link href="/dashboard">Cancel</Link>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
