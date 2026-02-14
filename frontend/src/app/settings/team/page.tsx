'use client';

import React from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Users, Lock, Clock } from 'lucide-react';

export default function TeamPage() {
  return (
    <DashboardShell>
      <div className="container max-w-4xl mx-auto p-6 space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Team Management</h1>
          <p className="text-muted-foreground">Invite and manage team members.</p>
        </div>

        <Card className="border-dashed">
          <CardContent className="py-16 text-center space-y-4">
            <div className="mx-auto w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center">
              <Users className="h-8 w-8 text-primary" />
            </div>
            <div>
              <h3 className="text-xl font-semibold">Team Features — Coming Soon</h3>
              <p className="text-muted-foreground mt-2 max-w-md mx-auto">
                Multi-user team management with role-based access control is in development. 
                You&apos;ll be able to invite team members, assign roles, and manage permissions.
              </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 max-w-lg mx-auto pt-4">
              <div className="p-3 rounded-xl bg-muted/40 text-center">
                <Users className="h-5 w-5 mx-auto mb-1 text-muted-foreground" />
                <p className="text-xs font-medium">Team Invites</p>
              </div>
              <div className="p-3 rounded-xl bg-muted/40 text-center">
                <Lock className="h-5 w-5 mx-auto mb-1 text-muted-foreground" />
                <p className="text-xs font-medium">RBAC Roles</p>
              </div>
              <div className="p-3 rounded-xl bg-muted/40 text-center">
                <Clock className="h-5 w-5 mx-auto mb-1 text-muted-foreground" />
                <p className="text-xs font-medium">Activity Log</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </DashboardShell>
  );
}
