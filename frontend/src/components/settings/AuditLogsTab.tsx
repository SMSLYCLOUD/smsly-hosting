"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Shield } from "lucide-react";

export function AuditLogsTab() {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Shield className="h-5 w-5 text-yellow-500" /> Audit Logs</CardTitle>
          <CardDescription>Security events and system activity.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">Review security events, login attempts, and system configuration changes.</p>
          <Button asChild variant="outline">
            <a href="/settings/audit-logs">Open Full Audit Logs</a>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
