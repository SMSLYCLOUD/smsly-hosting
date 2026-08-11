"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Cloud } from "lucide-react";

export function BillingTab() {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Cloud className="h-5 h-5 text-emerald-500" /> Billing & Usage</CardTitle>
          <CardDescription>Manage your subscription, view invoices, and track resource usage.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">View and manage your billing details, subscription plan, and invoice history.</p>
          <Button asChild variant="outline">
            <a href="/settings/billing">Open Full Billing Page</a>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
