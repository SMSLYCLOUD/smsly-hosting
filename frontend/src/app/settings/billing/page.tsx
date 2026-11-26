'use client';

import React from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { PageHeader } from '@/components/ui/page-header';
import { BillingTab } from '@/components/settings/BillingTab';
import { CreditCard } from 'lucide-react';

export default function BillingSettingsPage() {
  return (
    <DashboardShell>
      <div className="container max-w-5xl mx-auto p-6 space-y-8">
        <PageHeader
          title="Billing & Usage"
          description="Manage your subscription, view invoices, and track resource usage."
          icon={<CreditCard className="h-8 w-8 text-primary" />}
          breadcrumbs={[
            { label: 'Settings', href: '/settings' },
            { label: 'Billing' },
          ]}
          backHref="/settings"
        />
        <BillingTab />
      </div>
    </DashboardShell>
  );
}
