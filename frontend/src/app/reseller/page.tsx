'use client';

import React from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardContent } from '@/components/ui/card';
import { Users, DollarSign, Activity, Palette, Lock, Clock } from 'lucide-react';

export default function ResellerPage() {
  return (
    <DashboardShell>
      <div className="container max-w-4xl mx-auto p-6 space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Partner Console</h1>
          <p className="text-muted-foreground">White-label and reseller management.</p>
        </div>

        <Card className="border-dashed">
          <CardContent className="py-16 text-center space-y-4">
            <div className="mx-auto w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center">
              <Palette className="h-8 w-8 text-primary" />
            </div>
            <div>
              <h3 className="text-xl font-semibold">Partner Program — Coming Soon</h3>
              <p className="text-muted-foreground mt-2 max-w-md mx-auto">
                The partner console with white-label branding, reseller analytics, and team management
                is being developed. Apply for early access when it launches.
              </p>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 max-w-xl mx-auto pt-4">
              <div className="p-3 rounded-xl bg-muted/40 text-center">
                <Users className="h-5 w-5 mx-auto mb-1 text-muted-foreground" />
                <p className="text-xs font-medium">Team Mgmt</p>
              </div>
              <div className="p-3 rounded-xl bg-muted/40 text-center">
                <DollarSign className="h-5 w-5 mx-auto mb-1 text-muted-foreground" />
                <p className="text-xs font-medium">Revenue</p>
              </div>
              <div className="p-3 rounded-xl bg-muted/40 text-center">
                <Activity className="h-5 w-5 mx-auto mb-1 text-muted-foreground" />
                <p className="text-xs font-medium">Health</p>
              </div>
              <div className="p-3 rounded-xl bg-muted/40 text-center">
                <Palette className="h-5 w-5 mx-auto mb-1 text-muted-foreground" />
                <p className="text-xs font-medium">Branding</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </DashboardShell>
  );
}
