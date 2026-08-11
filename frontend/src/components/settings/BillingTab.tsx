"use client";

import React from 'react';
import { Card, CardHeader, CardTitle, CardContent, CardFooter, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Download, Loader2, CreditCard } from 'lucide-react';
import { billingApi, UserSubscription, Invoice, UsageSummary, PricingPlan } from '@/lib/api';
import { useToast } from '@/components/ui/use-toast';
import { Progress } from '@/components/ui/progress';

export function BillingTab() {
  const { toast } = useToast();
  const [subscription, setSubscription] = React.useState<UserSubscription | null>(null);
  const [invoices, setInvoices] = React.useState<Invoice[]>([]);
  const [usage, setUsage] = React.useState<UsageSummary | null>(null);
  const [plans, setPlans] = React.useState<PricingPlan[]>([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    async function load() {
      try {
        const [sub, inv, use, allPlans] = await Promise.all([
          billingApi.getSubscription(),
          billingApi.getInvoices(),
          billingApi.getUsage(),
          billingApi.getPlans(),
        ]);
        setSubscription(sub);
        setInvoices(inv);
        setUsage(use);
        setPlans(allPlans);
      } catch (err) {
        console.error(err);
        toast({ title: 'Failed to load billing data', variant: 'destructive' });
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [toast]);

  const currentPlan = plans.find((p) => p.id === subscription?.plan);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Subscription Card */}
        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CreditCard className="h-5 w-5 text-emerald-500" />
              Current Subscription
            </CardTitle>
            <CardDescription>
              You are currently on the{' '}
              <span className="font-semibold text-foreground">
                {currentPlan?.name || 'Free'}
              </span>{' '}
              plan.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex justify-between items-center p-4 bg-muted/50 rounded-lg">
              <div>
                <div className="text-sm font-medium text-muted-foreground">Status</div>
                <div className="text-lg font-bold flex items-center gap-2">
                  {subscription?.status || 'Active'}
                  <Badge variant={subscription?.status === 'ACTIVE' ? 'default' : 'destructive'}>
                    {subscription?.status || 'Active'}
                  </Badge>
                </div>
              </div>
              <div className="text-right">
                <div className="text-sm font-medium text-muted-foreground">Renewal Date</div>
                <div className="text-lg font-bold">
                  {subscription?.current_period_end
                    ? new Date(subscription.current_period_end).toLocaleDateString()
                    : 'N/A'}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <div className="text-sm font-medium">Monthly Cost</div>
                <div className="text-2xl font-bold">
                  $
                  {subscription?.billing_cycle === 'YEARLY'
                    ? (currentPlan?.price_yearly_usd || 0) / 12
                    : currentPlan?.price_monthly_usd || 0}
                  <span className="text-sm font-normal text-muted-foreground">/mo</span>
                </div>
              </div>
              <div className="flex items-end justify-end">
                <Button variant="outline" asChild>
                  <a href="/pricing">Change Plan</a>
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Quick Actions / Cost */}
        <Card>
          <CardHeader>
            <CardTitle>Estimated Cost</CardTitle>
            <CardDescription>Current billing period</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-4xl font-extrabold text-emerald-600 dark:text-emerald-400">$0.00</div>
            <p className="text-xs text-muted-foreground mt-2">
              Includes base plan + usage overages.
            </p>
          </CardContent>
          <CardFooter>
            <Button className="w-full">Manage Payment Method</Button>
          </CardFooter>
        </Card>
      </div>

      {/* Usage Section */}
      <Card>
        <CardHeader>
          <CardTitle>Resource Usage</CardTitle>
          <CardDescription>Usage for the current billing period vs plan limits.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span>Services</span>
              <span>
                {usage?.active_services || 0} / {currentPlan?.max_services || '∞'}
              </span>
            </div>
            <Progress
              value={
                ((usage?.active_services || 0) / (currentPlan?.max_services || 1)) * 100
              }
            />
          </div>

          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span>Storage (GB)</span>
              <span>
                {usage?.storage_gb || 0} / {currentPlan?.max_storage_gb || '∞'} GB
              </span>
            </div>
            <Progress
              value={
                ((usage?.storage_gb || 0) / (currentPlan?.max_storage_gb || 1)) * 100
              }
            />
          </div>

          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span>CPU Hours</span>
              <span>{usage?.cpu_hours?.toFixed(1) || 0} hrs</span>
            </div>
            <Progress value={30} className="bg-slate-100 dark:bg-slate-800" />
          </div>
        </CardContent>
      </Card>

      {/* Invoices Section */}
      <Card>
        <CardHeader>
          <CardTitle>Invoice History</CardTitle>
        </CardHeader>
        <CardContent>
          {invoices.length === 0 ? (
            <div className="text-center py-6 text-muted-foreground">No invoices found.</div>
          ) : (
            <div className="divide-y">
              {invoices.map((inv) => (
                <div key={inv.id} className="py-4 flex justify-between items-center">
                  <div>
                    <div className="font-medium">Invoice #{inv.id}</div>
                    <div className="text-sm text-muted-foreground">
                      {new Date(inv.period_end).toLocaleDateString()}
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    <span className="font-bold">${inv.total}</span>
                    <Badge variant={inv.status === 'PAID' ? 'secondary' : 'outline'}>
                      {inv.status}
                    </Badge>
                    <Button variant="ghost" size="icon" asChild>
                      <a href={inv.pdf_url || '#'} target="_blank" rel="noopener noreferrer">
                        <Download className="w-4 h-4" />
                      </a>
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
