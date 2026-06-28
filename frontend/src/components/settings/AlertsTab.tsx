"use client";

import React, { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { Loader2, Plus, Trash2, Save, Activity } from "lucide-react";

export function AlertsTab() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(false);
  const [rules, setRules] = useState<any[]>([
    { id: "1", name: "High CPU Usage", condition: "cpu > 90%", severity: "critical", channels: ["email", "slack"] },
    { id: "2", name: "High Memory Usage", condition: "memory > 85%", severity: "warning", channels: ["email"] },
    { id: "3", name: "Node Offline", condition: "status == offline", severity: "critical", channels: ["slack", "sms"] },
  ]);
  const [channels, setChannels] = useState<any[]>([
    { id: "c1", type: "slack", target: "#alerts" },
    { id: "c2", type: "email", target: "admin@example.com" },
  ]);

  const handleSave = async () => {
    setLoading(true);
    try {
      // Placeholder for Prometheus/Alertmanager sync
      await new Promise((resolve) => setTimeout(resolve, 800));
      toast({ title: "Alert rules synced to Alertmanager." });
    } catch (err: any) {
      toast({ title: "Failed to save rules", description: err.message, variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6 max-w-5xl">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-blue-500" /> Platform Health & Alerting
          </CardTitle>
          <CardDescription>Configure Prometheus alerts and Alertmanager routing rules.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="space-y-4">
            <div className="flex justify-between items-center">
              <h3 className="text-lg font-medium">Alerting Rules</h3>
              <Button variant="outline" size="sm"><Plus className="h-4 w-4 mr-1" /> Add Rule</Button>
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Rule Name</TableHead>
                  <TableHead>Condition (PromQL)</TableHead>
                  <TableHead>Severity</TableHead>
                  <TableHead>Channels</TableHead>
                  <TableHead></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rules.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium">{r.name}</TableCell>
                    <TableCell><code className="px-2 py-1 bg-muted rounded text-xs">{r.condition}</code></TableCell>
                    <TableCell>
                      <span className={`px-2 py-1 rounded-full text-xs font-medium ${r.severity === 'critical' ? 'bg-red-100 text-red-700' : 'bg-yellow-100 text-yellow-700'}`}>
                        {r.severity}
                      </span>
                    </TableCell>
                    <TableCell>{r.channels.join(", ")}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm"><Trash2 className="h-4 w-4 text-destructive" /></Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <div className="space-y-4 pt-4 border-t">
            <div className="flex justify-between items-center">
              <h3 className="text-lg font-medium">Notification Channels</h3>
              <Button variant="outline" size="sm"><Plus className="h-4 w-4 mr-1" /> Add Channel</Button>
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Type</TableHead>
                  <TableHead>Target / Webhook URL</TableHead>
                  <TableHead></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {channels.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="font-medium uppercase">{c.type}</TableCell>
                    <TableCell>{c.target}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm"><Trash2 className="h-4 w-4 text-destructive" /></Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <div className="pt-4 border-t flex justify-end">
            <Button onClick={handleSave} disabled={loading}>
              {loading ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Save className="h-4 w-4 mr-2" />}
              Sync with Alertmanager
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
