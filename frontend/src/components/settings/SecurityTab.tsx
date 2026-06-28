"use client";

import React, { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { Loader2, Shield, Key, Smartphone, Trash2 } from "lucide-react";
import api from "@/lib/api";

export function SecurityTab() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [devices, setDevices] = useState<any[]>([]);
  const [recoveryPhrase, setRecoveryPhrase] = useState<string | null>(null);

  const fetchDevices = async () => {
    try {
      const res = await api.get("/devices/");
      setDevices(res.data);
    } catch (err) {
      // Ignored if endpoint not ready
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDevices();
  }, []);

  const handleGenerateRecovery = async () => {
    try {
      const res = await api.post("/auth/recovery/generate/");
      setRecoveryPhrase(res.data.phrase);
      toast({ title: "Recovery phrase generated. Please save it securely!" });
    } catch (err: any) {
      toast({ title: "Failed to generate phrase", description: err.message, variant: "destructive" });
    }
  };

  const handleRevokeDevice = async (id: string) => {
    try {
      await api.post(`/devices/${id}/revoke/`);
      toast({ title: "Device revoked" });
      fetchDevices();
    } catch (err: any) {
      toast({ title: "Failed to revoke device", description: err.message, variant: "destructive" });
    }
  };

  return (
    <div className="space-y-6 max-w-4xl">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Shield className="h-5 w-5" /> Two-Factor Authentication (2FA)
          </CardTitle>
          <CardDescription>Secure your account with TOTP-based two-factor authentication.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Two-factor authentication adds an extra layer of security to your account.
          </p>
          <Button variant="outline">Setup 2FA App</Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Key className="h-5 w-5" /> Recovery Phrase
          </CardTitle>
          <CardDescription>Generate a 12-word BIP39 recovery phrase as a last resort login method.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {recoveryPhrase ? (
            <div className="p-4 bg-secondary rounded-lg border font-mono text-center tracking-wide">
              {recoveryPhrase}
            </div>
          ) : (
            <Button onClick={handleGenerateRecovery} variant="outline">
              Generate Recovery Phrase
            </Button>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Smartphone className="h-5 w-5" /> Trusted Devices
          </CardTitle>
          <CardDescription>Manage devices that have been fingerprinted and trusted.</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Device / Browser</TableHead>
                <TableHead>IP Address</TableHead>
                <TableHead>Last Seen</TableHead>
                <TableHead></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {devices.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={4} className="text-center text-muted-foreground h-24">
                    {loading ? <Loader2 className="h-5 w-5 animate-spin mx-auto" /> : "No trusted devices found."}
                  </TableCell>
                </TableRow>
              ) : (
                devices.map((device) => (
                  <TableRow key={device.id}>
                    <TableCell className="font-medium">{device.user_agent}</TableCell>
                    <TableCell>{device.last_ip}</TableCell>
                    <TableCell>{new Date(device.last_used_at).toLocaleDateString()}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" onClick={() => handleRevokeDevice(device.id)}>
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
