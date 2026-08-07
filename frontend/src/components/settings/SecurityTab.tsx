"use client";

import React, { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { Loader2, Shield, Key, Smartphone, Trash2, AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

interface SecurityTabProps {
  currentPassword: string;
  setCurrentPassword: (v: string) => void;
  newPassword: string;
  setNewPassword: (v: string) => void;
  confirmPassword: string;
  setConfirmPassword: (v: string) => void;
  changingPassword: boolean;
  handleChangePassword: () => void;
}

export function SecurityTab({
  currentPassword, setCurrentPassword,
  newPassword, setNewPassword,
  confirmPassword, setConfirmPassword,
  changingPassword, handleChangePassword,
}: SecurityTabProps) {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [devices, setDevices] = useState<any[]>([]);
  const [recoveryPhrase, setRecoveryPhrase] = useState<string | null>(null);

  const fetchDevices = async () => {
    try {
      const res = await api.get("/devices/");
      const data = res.data;
      setDevices(Array.isArray(data) ? data : (data?.devices || data?.results || []));
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

  const handleRevokeDevice = async (id: number) => {
    try {
      await api.delete(`/devices/${id}/revoke/`);
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
            <Key className="h-5 w-5" /> Change Password
          </CardTitle>
          <CardDescription>Update your account password.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="current-password">Current Password</Label>
            <Input
              id="current-password"
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-password">New Password</Label>
            <Input
              id="new-password"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="confirm-password">Confirm New Password</Label>
            <Input
              id="confirm-password"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
            />
          </div>
          <Button onClick={handleChangePassword} disabled={changingPassword}>
            {changingPassword ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Changing...</> : "Change Password"}
          </Button>
        </CardContent>
      </Card>

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
            <Badge variant="secondary" className="ml-2 text-xs bg-yellow-500/10 text-yellow-600 border-yellow-500/20">
              Beta
            </Badge>
          </CardTitle>
          <CardDescription>Manage devices that have been fingerprinted and trusted.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-start gap-3 p-3 rounded-lg bg-yellow-500/5 border border-yellow-500/20">
            <AlertTriangle className="h-4 w-4 text-yellow-600 mt-0.5 shrink-0" />
            <div className="text-sm text-yellow-600/80">
              <p className="font-medium text-yellow-600">Beta Feature</p>
              <p className="mt-1">
                Device trust fingerprinting is experimental. When enabled in Platform Settings,
                unrecognized devices will need to register before accessing the API.
                This can lock you out if you lose access to your registered devices.
                Test thoroughly before enabling in production.
              </p>
            </div>
          </div>

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
                    <TableCell className="font-medium">{device.label || device.user_agent || 'Unknown device'}</TableCell>
                    <TableCell>{device.ip_address}</TableCell>
                    <TableCell>{device.last_seen_at ? new Date(device.last_seen_at).toLocaleDateString() : '—'}</TableCell>
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
