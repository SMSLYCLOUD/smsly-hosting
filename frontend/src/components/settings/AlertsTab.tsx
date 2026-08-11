"use client";

import React, { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { alertsApi, systemApi, AlertRule, NotificationChannel } from "@/lib/api";
import {
  Loader2, Plus, Trash2, Save, Activity, Mail, Bell, Webhook,
  MessageSquare, Send, Check, ChevronDown, ChevronUp, TestTube,
} from "lucide-react";

const METRICS = [
  { value: "cpu", label: "CPU Usage" },
  { value: "memory", label: "Memory Usage" },
  { value: "disk", label: "Disk Usage" },
  { value: "status", label: "Service Status" },
  { value: "response_time", label: "Response Time" },
  { value: "error_rate", label: "Error Rate" },
];

const OPERATORS = [
  { value: ">", label: ">" },
  { value: ">=", label: ">=" },
  { value: "<", label: "<" },
  { value: "<=", label: "<=" },
  { value: "==", label: "==" },
  { value: "!=", label: "!=" },
];

const SEVERITIES = [
  { value: "info", label: "Info", color: "bg-blue-500/10 text-blue-500" },
  { value: "warning", label: "Warning", color: "bg-yellow-500/10 text-yellow-500" },
  { value: "critical", label: "Critical", color: "bg-red-500/10 text-red-500" },
];

const CHANNEL_ICONS: Record<string, React.ReactNode> = {
  email: <Mail className="h-4 w-4" />,
  slack: <MessageSquare className="h-4 w-4" />,
  sms: <Bell className="h-4 w-4" />,
  webhook: <Webhook className="h-4 w-4" />,
};

export function AlertsTab() {
  const { toast } = useToast();
  const confirm = useConfirm();

  // Data
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [loading, setLoading] = useState(true);

  // SMTP config
  const [smtpConfig, setSmtpConfig] = useState<any>({});
  const [savingSmtp, setSavingSmtp] = useState(false);
  const [testingSmtp, setTestingSmtp] = useState(false);
  const [smtpTestEmail, setSmtpTestEmail] = useState("");
  const [showSmtpTest, setShowSmtpTest] = useState(false);

  // Channel dialog
  const [showChannelDialog, setShowChannelDialog] = useState(false);
  const [editingChannel, setEditingChannel] = useState<NotificationChannel | null>(null);
  const [channelForm, setChannelForm] = useState<{ name: string; channel_type: NotificationChannel["channel_type"]; target: string }>({ name: "", channel_type: "email", target: "" });
  const [savingChannel, setSavingChannel] = useState(false);
  const [testingChannel, setTestingChannel] = useState<string | null>(null);

  // Rule dialog
  const [showRuleDialog, setShowRuleDialog] = useState(false);
  const [editingRule, setEditingRule] = useState<AlertRule | null>(null);
  const [ruleForm, setRuleForm] = useState<{
    name: string; metric: string; operator: string; threshold: number;
    severity: AlertRule["severity"]; channels: string[]; cooldown_minutes: number; message_template: string;
  }>({
    name: "", metric: "cpu", operator: ">", threshold: 90,
    severity: "warning", channels: [], cooldown_minutes: 5, message_template: "",
  });
  const [savingRule, setSavingRule] = useState(false);

  // Expand/collapse
  const [expandedSection, setExpandedSection] = useState<string | null>("channels");

  const fetchData = useCallback(async () => {
    try {
      const [channelsData, rulesData, smtpData] = await Promise.all([
        alertsApi.listChannels(),
        alertsApi.listRules(),
        systemApi.getDomainConfig(),
      ]);
      setChannels(channelsData);
      setRules(rulesData);
      setSmtpConfig(smtpData);
    } catch {
      toast({ title: "Failed to load alert configuration", variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // ── SMTP ────────────────────────────────────────────────────────

  const handleSaveSmtp = async () => {
    setSavingSmtp(true);
    try {
      await systemApi.updateDomainConfig(smtpConfig);
      toast({ title: "SMTP settings saved" });
    } catch (err: any) {
      toast({ title: "Failed to save SMTP settings", description: err.response?.data?.error || err.message, variant: "destructive" });
    } finally {
      setSavingSmtp(false);
    }
  };

  const handleTestSmtp = async () => {
    if (!smtpTestEmail) return;
    setTestingSmtp(true);
    try {
      const result = await alertsApi.testSmtp(smtpTestEmail);
      if (result.error) {
        toast({ title: "SMTP test failed", description: result.error, variant: "destructive" });
      } else {
        toast({ title: "SMTP test passed", description: result.message });
        setShowSmtpTest(false);
      }
    } catch (err: any) {
      toast({ title: "SMTP test failed", description: err.response?.data?.error || err.message, variant: "destructive" });
    } finally {
      setTestingSmtp(false);
    }
  };

  // ── Channels ────────────────────────────────────────────────────

  const handleSaveChannel = async () => {
    if (!channelForm.name || !channelForm.target) {
      toast({ title: "Name and target are required", variant: "destructive" });
      return;
    }
    setSavingChannel(true);
    try {
      if (editingChannel) {
        await alertsApi.updateChannel(editingChannel.id, channelForm);
        toast({ title: "Channel updated" });
      } else {
        await alertsApi.createChannel(channelForm);
        toast({ title: "Channel created" });
      }
      setShowChannelDialog(false);
      setEditingChannel(null);
      setChannelForm({ name: "", channel_type: "email", target: "" });
      fetchData();
    } catch (err: any) {
      toast({ title: "Failed to save channel", description: err.response?.data?.error || err.message, variant: "destructive" });
    } finally {
      setSavingChannel(false);
    }
  };

  const handleDeleteChannel = async (id: string) => {
    if (!await confirm({ title: "Delete channel?", message: "This will permanently remove this notification channel.", variant: "destructive", confirmText: "Delete" })) return;
    try {
      await alertsApi.deleteChannel(id);
      toast({ title: "Channel deleted" });
      fetchData();
    } catch {
      toast({ title: "Failed to delete channel", variant: "destructive" });
    }
  };

  const handleTestChannel = async (id: string) => {
    setTestingChannel(id);
    try {
      const result = await alertsApi.testChannel(id);
      if (result.error) {
        toast({ title: "Test failed", description: result.error, variant: "destructive" });
      } else {
        toast({ title: "Test passed", description: result.message });
      }
    } catch (err: any) {
      toast({ title: "Test failed", description: err.response?.data?.error || err.message, variant: "destructive" });
    } finally {
      setTestingChannel(null);
    }
  };

  // ── Rules ───────────────────────────────────────────────────────

  const handleSaveRule = async () => {
    if (!ruleForm.name) {
      toast({ title: "Rule name is required", variant: "destructive" });
      return;
    }
    setSavingRule(true);
    try {
      if (editingRule) {
        await alertsApi.updateRule(editingRule.id, ruleForm);
        toast({ title: "Rule updated" });
      } else {
        await alertsApi.createRule(ruleForm);
        toast({ title: "Rule created" });
      }
      setShowRuleDialog(false);
      setEditingRule(null);
      setRuleForm({ name: "", metric: "cpu", operator: ">", threshold: 90, severity: "warning", channels: [], cooldown_minutes: 5, message_template: "" });
      fetchData();
    } catch (err: any) {
      toast({ title: "Failed to save rule", description: err.response?.data?.error || err.message, variant: "destructive" });
    } finally {
      setSavingRule(false);
    }
  };

  const handleDeleteRule = async (id: string) => {
    if (!await confirm({ title: "Delete rule?", message: "This will permanently remove this alert rule.", variant: "destructive", confirmText: "Delete" })) return;
    try {
      await alertsApi.deleteRule(id);
      toast({ title: "Rule deleted" });
      fetchData();
    } catch {
      toast({ title: "Failed to delete rule", variant: "destructive" });
    }
  };

  const handleToggleRule = async (id: string) => {
    try {
      await alertsApi.toggleRule(id);
      fetchData();
    } catch {
      toast({ title: "Failed to toggle rule", variant: "destructive" });
    }
  };

  if (loading) {
    return (
      <div className="flex h-48 items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ── SMTP Configuration ─────────────────────────────────── */}
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
                value={smtpConfig.smtp_host || ""}
                onChange={(e) => setSmtpConfig((p: any) => ({ ...p, smtp_host: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>Port</Label>
              <Input
                type="number"
                placeholder="587"
                value={smtpConfig.smtp_port || 587}
                onChange={(e) => setSmtpConfig((p: any) => ({ ...p, smtp_port: parseInt(e.target.value) || 587 }))}
              />
            </div>
            <div className="space-y-2">
              <Label>Username</Label>
              <Input
                placeholder="your@email.com"
                value={smtpConfig.smtp_username || ""}
                onChange={(e) => setSmtpConfig((p: any) => ({ ...p, smtp_username: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>Password</Label>
              <Input
                type="password"
                placeholder={smtpConfig.smtp_password_set ? "•••••••• (Saved)" : "Enter password"}
                onChange={(e) => setSmtpConfig((p: any) => ({ ...p, smtp_password: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>From Email</Label>
              <Input
                placeholder="alerts@yourdomain.com"
                value={smtpConfig.smtp_from_email || ""}
                onChange={(e) => setSmtpConfig((p: any) => ({ ...p, smtp_from_email: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>From Name</Label>
              <Input
                placeholder="SMSLY"
                value={smtpConfig.smtp_from_name || "SMSLY"}
                onChange={(e) => setSmtpConfig((p: any) => ({ ...p, smtp_from_name: e.target.value }))}
              />
            </div>
          </div>
          <div className="flex items-center justify-between pt-2">
            <div className="flex items-center gap-3">
              <Switch
                checked={smtpConfig.smtp_use_tls !== false}
                onCheckedChange={(v) => setSmtpConfig((p: any) => ({ ...p, smtp_use_tls: v }))}
              />
              <Label>Enable STARTTLS encryption</Label>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setShowSmtpTest(true)}>
                <TestTube className="h-4 w-4 mr-1" /> Send Test Email
              </Button>
              <Button size="sm" onClick={handleSaveSmtp} disabled={savingSmtp}>
                {savingSmtp ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Save className="h-4 w-4 mr-1" />}
                Save SMTP Settings
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* ── Notification Channels ──────────────────────────────── */}
      <Card>
        <CardHeader
          className="cursor-pointer select-none"
          onClick={() => setExpandedSection(expandedSection === "channels" ? null : "channels")}
        >
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <Bell className="h-5 w-5 text-orange-500" /> Notification Channels
              <span className="text-sm font-normal text-muted-foreground">({channels.length})</span>
            </CardTitle>
            {expandedSection === "channels" ? <ChevronUp className="h-5 w-5" /> : <ChevronDown className="h-5 w-5" />}
          </div>
          <CardDescription>Define where alert notifications are delivered.</CardDescription>
        </CardHeader>
        {expandedSection === "channels" && (
          <CardContent className="space-y-4">
            <div className="flex justify-end">
              <Button size="sm" onClick={() => { setEditingChannel(null); setChannelForm({ name: "", channel_type: "email", target: "" }); setShowChannelDialog(true); }}>
                <Plus className="h-4 w-4 mr-1" /> Add Channel
              </Button>
            </div>
            {channels.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                <Bell className="h-8 w-8 mx-auto mb-2 opacity-50" />
                <p className="text-sm">No notification channels configured.</p>
                <p className="text-xs mt-1">Add a channel to start receiving alerts.</p>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Type</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>Target</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {channels.map((c) => (
                    <TableRow key={c.id}>
                      <TableCell className="flex items-center gap-2">
                        {CHANNEL_ICONS[c.channel_type] || <Mail className="h-4 w-4" />}
                        <span className="uppercase text-xs font-medium">{c.channel_type}</span>
                      </TableCell>
                      <TableCell className="font-medium">{c.name}</TableCell>
                      <TableCell className="font-mono text-xs max-w-[300px] truncate">{c.target}</TableCell>
                      <TableCell>
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${c.enabled ? "bg-emerald-500/10 text-emerald-500" : "bg-zinc-500/10 text-zinc-500"}`}>
                          {c.enabled ? "Active" : "Disabled"}
                        </span>
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="ghost" size="sm"
                            onClick={() => handleTestChannel(c.id)}
                            disabled={testingChannel === c.id}
                            title="Send test notification"
                          >
                            {testingChannel === c.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Send className="h-3 w-3" />}
                          </Button>
                          <Button
                            variant="ghost" size="sm"
                            onClick={() => {
                              setEditingChannel(c);
                              setChannelForm({ name: c.name, channel_type: c.channel_type as NotificationChannel["channel_type"], target: c.target });
                              setShowChannelDialog(true);
                            }}
                          >
                            Edit
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => handleDeleteChannel(c.id)}>
                            <Trash2 className="h-3 w-3 text-destructive" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        )}
      </Card>

      {/* ── Alert Rules ────────────────────────────────────────── */}
      <Card>
        <CardHeader
          className="cursor-pointer select-none"
          onClick={() => setExpandedSection(expandedSection === "rules" ? null : "rules")}
        >
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <Activity className="h-5 w-5 text-red-500" /> Alert Rules
              <span className="text-sm font-normal text-muted-foreground">({rules.length})</span>
            </CardTitle>
            {expandedSection === "rules" ? <ChevronUp className="h-5 w-5" /> : <ChevronDown className="h-5 w-5" />}
          </div>
          <CardDescription>Define conditions that trigger alert notifications.</CardDescription>
        </CardHeader>
        {expandedSection === "rules" && (
          <CardContent className="space-y-4">
            <div className="flex justify-end">
              <Button size="sm" onClick={() => {
                setEditingRule(null);
                setRuleForm({ name: "", metric: "cpu", operator: ">", threshold: 90, severity: "warning", channels: [], cooldown_minutes: 5, message_template: "" });
                setShowRuleDialog(true);
              }}>
                <Plus className="h-4 w-4 mr-1" /> Add Rule
              </Button>
            </div>
            {rules.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                <Activity className="h-8 w-8 mx-auto mb-2 opacity-50" />
                <p className="text-sm">No alert rules configured.</p>
                <p className="text-xs mt-1">Create a rule to start monitoring metrics.</p>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Rule Name</TableHead>
                    <TableHead>Condition</TableHead>
                    <TableHead>Severity</TableHead>
                    <TableHead>Channels</TableHead>
                    <TableHead>Cooldown</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rules.map((r) => {
                    const sev = SEVERITIES.find(s => s.value === r.severity);
                    return (
                      <TableRow key={r.id}>
                        <TableCell className="font-medium">
                          <div className="flex items-center gap-2">
                            <Switch
                              checked={r.enabled}
                              onCheckedChange={() => handleToggleRule(r.id)}
                              className="scale-75"
                            />
                            <span className={r.enabled ? "" : "text-muted-foreground"}>{r.name}</span>
                          </div>
                        </TableCell>
                        <TableCell>
                          <code className="px-2 py-1 bg-muted rounded text-xs">
                            {r.metric} {r.operator} {r.threshold}
                          </code>
                        </TableCell>
                        <TableCell>
                          <span className={`px-2 py-1 rounded-full text-xs font-medium ${sev?.color || ""}`}>
                            {sev?.label || r.severity}
                          </span>
                        </TableCell>
                        <TableCell>
                          <div className="flex gap-1">
                            {r.channels.map((chId) => {
                              const ch = channels.find(c => c.id === chId);
                              return ch ? (
                                <span key={chId} className="px-1.5 py-0.5 bg-muted rounded text-[10px] font-medium">
                                  {ch.channel_type}
                                </span>
                              ) : null;
                            })}
                          </div>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">{r.cooldown_minutes}m</TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1">
                            <Button variant="ghost" size="sm" onClick={() => {
                              setEditingRule(r);
                              setRuleForm({
                                name: r.name, metric: r.metric, operator: r.operator,
                                threshold: r.threshold, severity: r.severity as AlertRule["severity"],
                                channels: r.channels, cooldown_minutes: r.cooldown_minutes,
                                message_template: r.message_template,
                              });
                              setShowRuleDialog(true);
                            }}>
                              Edit
                            </Button>
                            <Button variant="ghost" size="sm" onClick={() => handleDeleteRule(r.id)}>
                              <Trash2 className="h-3 w-3 text-destructive" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </CardContent>
        )}
      </Card>

      {/* ── SMTP Test Dialog ───────────────────────────────────── */}
      <Dialog open={showSmtpTest} onOpenChange={setShowSmtpTest}>
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
              value={smtpTestEmail}
              onChange={(e) => setSmtpTestEmail(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowSmtpTest(false)}>Cancel</Button>
            <Button onClick={handleTestSmtp} disabled={testingSmtp || !smtpTestEmail}>
              {testingSmtp ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Send className="h-4 w-4 mr-1" />}
              Send Test
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Channel Dialog ─────────────────────────────────────── */}
      <Dialog open={showChannelDialog} onOpenChange={setShowChannelDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingChannel ? "Edit Channel" : "Add Notification Channel"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Channel Name</Label>
              <Input
                placeholder="e.g. Ops Email, Slack Alerts"
                value={channelForm.name}
                onChange={(e) => setChannelForm(p => ({ ...p, name: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>Type</Label>
              <Select value={channelForm.channel_type} onValueChange={(v) => setChannelForm(p => ({ ...p, channel_type: v as NotificationChannel["channel_type"] }))}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="email">Email</SelectItem>
                  <SelectItem value="slack">Slack Webhook</SelectItem>
                  <SelectItem value="sms">SMS</SelectItem>
                  <SelectItem value="webhook">Generic Webhook</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Target</Label>
              <Input
                placeholder={
                  channelForm.channel_type === "email" ? "alerts@yourdomain.com" :
                  channelForm.channel_type === "slack" ? "https://hooks.slack.com/services/..." :
                  channelForm.channel_type === "sms" ? "+1234567890" :
                  "https://your-api.com/webhook"
                }
                value={channelForm.target}
                onChange={(e) => setChannelForm(p => ({ ...p, target: e.target.value }))}
              />
              <p className="text-xs text-muted-foreground">
                {channelForm.channel_type === "email" && "Email address to receive notifications."}
                {channelForm.channel_type === "slack" && "Incoming webhook URL from Slack workspace settings."}
                {channelForm.channel_type === "sms" && "Phone number in E.164 format (requires SMSLY SMS API)."}
                {channelForm.channel_type === "webhook" && "URL that will receive POST requests with alert payloads."}
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowChannelDialog(false)}>Cancel</Button>
            <Button onClick={handleSaveChannel} disabled={savingChannel}>
              {savingChannel ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Check className="h-4 w-4 mr-1" />}
              {editingChannel ? "Update" : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Rule Dialog ────────────────────────────────────────── */}
      <Dialog open={showRuleDialog} onOpenChange={setShowRuleDialog}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingRule ? "Edit Alert Rule" : "Create Alert Rule"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Rule Name</Label>
              <Input
                placeholder="e.g. High CPU Usage"
                value={ruleForm.name}
                onChange={(e) => setRuleForm(p => ({ ...p, name: e.target.value }))}
              />
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-2">
                <Label>Metric</Label>
                <Select value={ruleForm.metric} onValueChange={(v) => setRuleForm(p => ({ ...p, metric: v }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {METRICS.map(m => <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Operator</Label>
                <Select value={ruleForm.operator} onValueChange={(v) => setRuleForm(p => ({ ...p, operator: v }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {OPERATORS.map(o => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Threshold</Label>
                <Input
                  type="number"
                  value={ruleForm.threshold}
                  onChange={(e) => setRuleForm(p => ({ ...p, threshold: parseFloat(e.target.value) || 0 }))}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>Severity</Label>
                <Select value={ruleForm.severity} onValueChange={(v) => setRuleForm(p => ({ ...p, severity: v as AlertRule["severity"] }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {SEVERITIES.map(s => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Cooldown (minutes)</Label>
                <Input
                  type="number"
                  min="1"
                  value={ruleForm.cooldown_minutes}
                  onChange={(e) => setRuleForm(p => ({ ...p, cooldown_minutes: parseInt(e.target.value) || 5 }))}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>Notification Channels</Label>
              <div className="flex flex-wrap gap-2">
                {channels.map(ch => (
                  <button
                    key={ch.id}
                    type="button"
                    onClick={() => {
                      setRuleForm(p => ({
                        ...p,
                        channels: p.channels.includes(ch.id)
                          ? p.channels.filter(id => id !== ch.id)
                          : [...p.channels, ch.id],
                      }));
                    }}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                      ruleForm.channels.includes(ch.id)
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border text-muted-foreground hover:bg-muted"
                    }`}
                  >
                    {CHANNEL_ICONS[ch.channel_type]}
                    {ch.name}
                    {ruleForm.channels.includes(ch.id) && <Check className="h-3 w-3" />}
                  </button>
                ))}
                {channels.length === 0 && (
                  <p className="text-xs text-muted-foreground">No channels configured. Add a channel first.</p>
                )}
              </div>
            </div>
            <div className="space-y-2">
              <Label>Custom Message Template (optional)</Label>
              <Input
                placeholder="Use {metric}, {value}, {threshold}, {service} as placeholders"
                value={ruleForm.message_template}
                onChange={(e) => setRuleForm(p => ({ ...p, message_template: e.target.value }))}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowRuleDialog(false)}>Cancel</Button>
            <Button onClick={handleSaveRule} disabled={savingRule}>
              {savingRule ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Check className="h-4 w-4 mr-1" />}
              {editingRule ? "Update" : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
