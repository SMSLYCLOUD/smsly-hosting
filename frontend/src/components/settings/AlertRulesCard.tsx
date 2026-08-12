"use client";

import React, { useState } from "react";
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
import { alertsApi, AlertRule, NotificationChannel } from "@/lib/api";
import { Loader2, Plus, Trash2, Activity, Check } from "lucide-react";

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
  email: <span className="h-4 w-4" />,
  slack: <span className="h-4 w-4" />,
  sms: <span className="h-4 w-4" />,
  webhook: <span className="h-4 w-4" />,
};

interface AlertRulesCardProps {
  rules: AlertRule[];
  channels: NotificationChannel[];
  onRefresh: () => void;
}

export function AlertRulesCard({ rules, channels, onRefresh }: AlertRulesCardProps) {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [showDialog, setShowDialog] = useState(false);
  const [editing, setEditing] = useState<AlertRule | null>(null);
  const [form, setForm] = useState<{
    name: string; metric: string; operator: string; threshold: number;
    severity: AlertRule["severity"]; channels: string[]; cooldown_minutes: number; message_template: string;
  }>({
    name: "", metric: "cpu", operator: ">", threshold: 90,
    severity: "warning", channels: [], cooldown_minutes: 5, message_template: "",
  });
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (!form.name) {
      toast({ title: "Rule name is required", variant: "destructive" });
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await alertsApi.updateRule(editing.id, form);
        toast({ title: "Rule updated" });
      } else {
        await alertsApi.createRule(form);
        toast({ title: "Rule created" });
      }
      setShowDialog(false);
      setEditing(null);
      setForm({ name: "", metric: "cpu", operator: ">", threshold: 90, severity: "warning", channels: [], cooldown_minutes: 5, message_template: "" });
      onRefresh();
    } catch (err: any) {
      toast({ title: "Failed to save rule", description: err.response?.data?.error || err.message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!await confirm({ title: "Delete rule?", message: "This will permanently remove this alert rule.", variant: "destructive", confirmText: "Delete" })) return;
    try {
      await alertsApi.deleteRule(id);
      toast({ title: "Rule deleted" });
      onRefresh();
    } catch {
      toast({ title: "Failed to delete rule", variant: "destructive" });
    }
  };

  const handleToggle = async (id: string) => {
    try {
      await alertsApi.toggleRule(id);
      onRefresh();
    } catch {
      toast({ title: "Failed to toggle rule", variant: "destructive" });
    }
  };

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <Activity className="h-5 w-5 text-red-500" /> Alert Rules
              <span className="text-sm font-normal text-muted-foreground">({rules.length})</span>
            </CardTitle>
            <Button size="sm" onClick={() => {
              setEditing(null);
              setForm({ name: "", metric: "cpu", operator: ">", threshold: 90, severity: "warning", channels: [], cooldown_minutes: 5, message_template: "" });
              setShowDialog(true);
            }}>
              <Plus className="h-4 w-4 mr-1" /> Add Rule
            </Button>
          </div>
          <CardDescription>Define conditions that trigger alert notifications.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
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
                            onCheckedChange={() => handleToggle(r.id)}
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
                            setEditing(r);
                            setForm({
                              name: r.name, metric: r.metric, operator: r.operator,
                              threshold: r.threshold, severity: r.severity as AlertRule["severity"],
                              channels: r.channels, cooldown_minutes: r.cooldown_minutes,
                              message_template: r.message_template,
                            });
                            setShowDialog(true);
                          }}>
                            Edit
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => handleDelete(r.id)}>
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
      </Card>

      <Dialog open={showDialog} onOpenChange={setShowDialog}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editing ? "Edit Alert Rule" : "Create Alert Rule"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Rule Name</Label>
              <Input
                placeholder="e.g. High CPU Usage"
                value={form.name}
                onChange={(e) => setForm(p => ({ ...p, name: e.target.value }))}
              />
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-2">
                <Label>Metric</Label>
                <Select value={form.metric} onValueChange={(v) => setForm(p => ({ ...p, metric: v }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {METRICS.map(m => <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Operator</Label>
                <Select value={form.operator} onValueChange={(v) => setForm(p => ({ ...p, operator: v }))}>
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
                  value={form.threshold}
                  onChange={(e) => setForm(p => ({ ...p, threshold: parseFloat(e.target.value) || 0 }))}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>Severity</Label>
                <Select value={form.severity} onValueChange={(v) => setForm(p => ({ ...p, severity: v as AlertRule["severity"] }))}>
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
                  value={form.cooldown_minutes}
                  onChange={(e) => setForm(p => ({ ...p, cooldown_minutes: parseInt(e.target.value) || 5 }))}
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
                      setForm(p => ({
                        ...p,
                        channels: p.channels.includes(ch.id)
                          ? p.channels.filter(id => id !== ch.id)
                          : [...p.channels, ch.id],
                      }));
                    }}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                      form.channels.includes(ch.id)
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border text-muted-foreground hover:bg-muted"
                    }`}
                  >
                    {CHANNEL_ICONS[ch.channel_type]}
                    {ch.name}
                    {form.channels.includes(ch.id) && <Check className="h-3 w-3" />}
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
                value={form.message_template}
                onChange={(e) => setForm(p => ({ ...p, message_template: e.target.value }))}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDialog(false)}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Check className="h-4 w-4 mr-1" />}
              {editing ? "Update" : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
