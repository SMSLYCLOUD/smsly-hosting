"use client";

import React, { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";
import { alertsApi, systemApi } from "@/lib/api";
import { Loader2, Save, Mail, Send, TestTube } from "lucide-react";

interface SmtpConfigCardProps {
  config: any;
  onConfigChange: (config: any) => void;
}

export function SmtpConfigCard({ config, onConfigChange }: SmtpConfigCardProps) {
  const { toast } = useToast();
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testEmail, setTestEmail] = useState("");
  const [showTest, setShowTest] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await systemApi.updateDomainConfig(config);
      toast({ title: "SMTP settings saved" });
    } catch (err: any) {
      toast({ title: "Failed to save SMTP settings", description: err.response?.data?.error || err.message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    if (!testEmail) return;
    setTesting(true);
    try {
      const result = await alertsApi.testSmtp(testEmail);
      if (result.error) {
        toast({ title: "SMTP test failed", description: result.error, variant: "destructive" });
      } else {
        toast({ title: "SMTP test passed", description: result.message });
        setShowTest(false);
      }
    } catch (err: any) {
      toast({ title: "SMTP test failed", description: err.response?.data?.error || err.message, variant: "destructive" });
    } finally {
      setTesting(false);
    }
  };

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Mail className="h-5 w-5 text-blue-500" /> SMTP / Email Configuration
          </CardTitle>
          <CardDescription>Configure the email server used for alert notifications and platform emails.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>SMTP Host</Label>
              <Input
                placeholder="smtp.gmail.com"
                value={config.smtp_host || ""}
                onChange={(e) => onConfigChange({ ...config, smtp_host: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>Port</Label>
              <Input
                type="number"
                placeholder="587"
                value={config.smtp_port || 587}
                onChange={(e) => onConfigChange({ ...config, smtp_port: parseInt(e.target.value) || 587 })}
              />
            </div>
            <div className="space-y-2">
              <Label>Username</Label>
              <Input
                placeholder="your@email.com"
                value={config.smtp_username || ""}
                onChange={(e) => onConfigChange({ ...config, smtp_username: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>Password</Label>
              <Input
                type="password"
                placeholder={config.smtp_password_set ? "•••••••• (Saved)" : "Enter password"}
                onChange={(e) => onConfigChange({ ...config, smtp_password: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>From Email</Label>
              <Input
                placeholder="alerts@yourdomain.com"
                value={config.smtp_from_email || ""}
                onChange={(e) => onConfigChange({ ...config, smtp_from_email: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>From Name</Label>
              <Input
                placeholder="SMSLY"
                value={config.smtp_from_name || "SMSLY"}
                onChange={(e) => onConfigChange({ ...config, smtp_from_name: e.target.value })}
              />
            </div>
          </div>
          <div className="flex items-center justify-between pt-2">
            <div className="flex items-center gap-3">
              <Switch
                checked={config.smtp_use_tls !== false}
                onCheckedChange={(v) => onConfigChange({ ...config, smtp_use_tls: v })}
              />
              <Label>Enable STARTTLS encryption</Label>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setShowTest(true)}>
                <TestTube className="h-4 w-4 mr-1" /> Send Test Email
              </Button>
              <Button size="sm" onClick={handleSave} disabled={saving}>
                {saving ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Save className="h-4 w-4 mr-1" />}
                Save SMTP Settings
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Dialog open={showTest} onOpenChange={setShowTest}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Send Test Email</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Enter an email address to send a test email using the current SMTP configuration.
            </p>
            <Input
              placeholder="test@example.com"
              type="email"
              value={testEmail}
              onChange={(e) => setTestEmail(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowTest(false)}>Cancel</Button>
            <Button onClick={handleTest} disabled={testing || !testEmail}>
              {testing ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Send className="h-4 w-4 mr-1" />}
              Send Test
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
