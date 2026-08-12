"use client";

import React, { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { licensingApi } from "@/lib/api";
import { Loader2, Key, ShieldCheck, AlertTriangle } from "lucide-react";

export default function LicensingPage() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [license, setLicense] = useState<any>(null);
  const [newKey, setNewKey] = useState("");

  const fetchLicense = async () => {
    try {
      const data = await licensingApi.getStatus();
      setLicense(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLicense();
  }, []);

  const handleUpdate = async () => {
    if (!newKey.trim()) return;
    try {
      setLoading(true);
      await licensingApi.activate(newKey);
      await fetchLicense();
      setNewKey("");
      toast({ title: "License updated successfully" });
    } catch (e: unknown) {
      toast({ title: "Error updating license", description: e instanceof Error ? e.message : 'Unknown error', variant: "destructive" });
      setLoading(false);
    }
  };

  if (loading && !license) return <div className="flex justify-center p-8"><Loader2 className="h-8 w-8 animate-spin text-muted-foreground" /></div>;

  const isEnterprise = license?.tier === 'enterprise';

  return (
    <div className="space-y-6 max-w-4xl mx-auto p-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">Platform Licensing</h2>
        <p className="text-muted-foreground">Manage your Trulay platform license and tier.</p>
      </div>

      <Card className={isEnterprise ? "border-emerald-500/50 shadow-[0_0_15px_rgba(16,185,129,0.1)]" : ""}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className={`h-5 w-5 ${isEnterprise ? "text-emerald-500" : "text-zinc-500"}`} />
            Current License Status
          </CardTitle>
          <CardDescription>View your active tier and limits.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="p-4 bg-muted/20 rounded-md">
              <p className="text-sm font-medium text-zinc-400">Current Tier</p>
              <p className={`text-2xl font-bold capitalize mt-1 ${isEnterprise ? 'text-emerald-400' : 'text-white'}`}>
                {license?.tier || 'Community'}
              </p>
            </div>
            <div className="p-4 bg-muted/20 rounded-md">
              <p className="text-sm font-medium text-zinc-400">License Key</p>
              <p className="text-lg font-mono text-zinc-300 mt-1 truncate">
                {license?.license_key ? (license.license_key.substring(0, 15) + '...') : 'None'}
              </p>
            </div>
          </div>
          
          {!isEnterprise && (
            <div className="flex items-start gap-3 p-4 bg-amber-500/10 border border-amber-500/20 rounded-md mt-4">
              <AlertTriangle className="h-5 w-5 text-amber-500 shrink-0 mt-0.5" />
              <div>
                <h4 className="font-medium text-amber-500">Community Edition Limitations</h4>
                <p className="text-sm text-amber-500/80 mt-1">
                  You are currently running the open-source community edition. Some enterprise features like SSO, AI Intelligence, and Mesh Networking may be limited.
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Key className="h-5 w-5" />
            Update License Key
          </CardTitle>
          <CardDescription>Enter a valid Pro or Enterprise license key to unlock features.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4 max-w-md">
            <div className="space-y-2">
              <Label>License Key</Label>
              <Input 
                value={newKey}
                onChange={(e) => setNewKey(e.target.value)}
                placeholder="sk_live_..."
                type="password"
              />
            </div>
          </div>
        </CardContent>
        <CardFooter>
          <Button onClick={handleUpdate} disabled={!newKey.trim() || loading}>
            {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Activate License
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
