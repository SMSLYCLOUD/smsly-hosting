"use client";

import React, { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { alertsApi, NotificationChannel } from "@/lib/api";
import { Loader2, Plus, Trash2, Bell, Mail, MessageSquare, Webhook, Send, Check } from "lucide-react";

const CHANNEL_ICONS: Record<string, React.ReactNode> = {
  email: <Mail className="h-4 w-4" />,
  slack: <MessageSquare className="h-4 w-4" />,
  sms: <Bell className="h-4 w-4" />,
  webhook: <Webhook className="h-4 w-4" />,
};

interface NotificationChannelsCardProps {
  channels: NotificationChannel[];
  onRefresh: () => void;
}

export function NotificationChannelsCard({ channels, onRefresh }: NotificationChannelsCardProps) {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [showDialog, setShowDialog] = useState(false);
  const [editing, setEditing] = useState<NotificationChannel | null>(null);
  const [form, setForm] = useState<{ name: string; channel_type: NotificationChannel["channel_type"]; target: string }>({
    name: "", channel_type: "email", target: "",
  });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);

  const handleSave = async () => {
    if (!form.name || !form.target) {
      toast({ title: "Name and target are required", variant: "destructive" });
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await alertsApi.updateChannel(editing.id, form);
        toast({ title: "Channel updated" });
      } else {
        await alertsApi.createChannel(form);
        toast({ title: "Channel created" });
      }
      setShowDialog(false);
      setEditing(null);
      setForm({ name: "", channel_type: "email", target: "" });
      onRefresh();
    } catch (err: any) {
      toast({ title: "Failed to save channel", description: err.response?.data?.error || err.message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!await confirm({ title: "Delete channel?", message: "This will permanently remove this notification channel.", variant: "destructive", confirmText: "Delete" })) return;
    try {
      await alertsApi.deleteChannel(id);
      toast({ title: "Channel deleted" });
      onRefresh();
    } catch {
      toast({ title: "Failed to delete channel", variant: "destructive" });
    }
  };

  const handleTest = async (id: string) => {
    setTesting(id);
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
      setTesting(null);
    }
  };

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <Bell className="h-5 w-5 text-orange-500" /> Notification Channels
              <span className="text-sm font-normal text-muted-foreground">({channels.length})</span>
            </CardTitle>
            <Button size="sm" onClick={() => { setEditing(null); setForm({ name: "", channel_type: "email", target: "" }); setShowDialog(true); }}>
              <Plus className="h-4 w-4 mr-1" /> Add Channel
            </Button>
          </div>
          <CardDescription>Define where alert notifications are delivered.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
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
                          onClick={() => handleTest(c.id)}
                          disabled={testing === c.id}
                          title="Send test notification"
                        >
                          {testing === c.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Send className="h-3 w-3" />}
                        </Button>
                        <Button
                          variant="ghost" size="sm"
                          onClick={() => {
                            setEditing(c);
                            setForm({ name: c.name, channel_type: c.channel_type as NotificationChannel["channel_type"], target: c.target });
                            setShowDialog(true);
                          }}
                        >
                          Edit
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => handleDelete(c.id)}>
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
      </Card>

      <Dialog open={showDialog} onOpenChange={setShowDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? "Edit Channel" : "Add Notification Channel"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Channel Name</Label>
              <Input
                placeholder="e.g. Ops Email, Slack Alerts"
                value={form.name}
                onChange={(e) => setForm(p => ({ ...p, name: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>Type</Label>
              <Select value={form.channel_type} onValueChange={(v) => setForm(p => ({ ...p, channel_type: v as NotificationChannel["channel_type"] }))}>
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
                  form.channel_type === "email" ? "alerts@yourdomain.com" :
                  form.channel_type === "slack" ? "https://hooks.slack.com/services/..." :
                  form.channel_type === "sms" ? "+1234567890" :
                  "https://your-api.com/webhook"
                }
                value={form.target}
                onChange={(e) => setForm(p => ({ ...p, target: e.target.value }))}
              />
              <p className="text-xs text-muted-foreground">
                {form.channel_type === "email" && "Email address to receive notifications."}
                {form.channel_type === "slack" && "Incoming webhook URL from Slack workspace settings."}
                {form.channel_type === "sms" && "Phone number in E.164 format (requires SMSLY SMS API)."}
                {form.channel_type === "webhook" && "URL that will receive POST requests with alert payloads."}
              </p>
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
