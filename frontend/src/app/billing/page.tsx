'use client';

import React from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardContent, CardFooter, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Check, CreditCard, Download, ExternalLink, Loader2, Receipt, ShieldCheck } from 'lucide-react';
import api, { licensingApi } from '@/lib/api';
import { useToast } from '@/components/ui/use-toast';
import { useTier } from '@/context/TierContext';
import { Input } from '@/components/ui/input';
import { EnhancedCrossSell } from '@/components/dashboard/EnhancedCrossSell';
import { motion } from 'framer-motion';

type PlanCode = 'HOBBY' | 'PRO' | 'ENTERPRISE';

type BillingSummary = {
  currency: string;
  stripe_configured: boolean;
  flutterwave_configured: boolean;
  cryptomus_configured: boolean;
  plan: PlanCode;
  subscription_status: string;
  current_period_end: string | null;
  balance: number;
  total_estimated_cost: number;
  billing_period: string;
  services: { id: string; name: string; cost: number; cpu_usage_hours: number }[];
};

type StripeInvoice = {
  id: string;
  status: string;
  amount_paid: number;
  currency: string;
  hosted_invoice_url?: string | null;
  invoice_pdf?: string | null;
  created?: number | null;
};

const PLANS: Array<{
  code: PlanCode;
  name: string;
  price: string;
  description: string;
  features: string[];
  cta: string;
  popular?: boolean;
}> = [
  {
    code: 'HOBBY',
    name: 'Hobby',
    price: '$0',
    description: 'For personal projects',
    features: ['Community support', 'Basic deployments', 'Usage-based metering'],
    cta: 'Current Plan',
  },
  {
    code: 'PRO',
    name: 'Pro',
    price: '$29',
    description: 'For serious applications',
    features: ['Priority support', 'Higher limits', 'Team-ready features'],
    cta: 'Upgrade',
    popular: true,
  },
  {
    code: 'ENTERPRISE',
    name: 'Enterprise',
    price: 'Custom',
    description: 'For large scale deployments',
    features: ['SLA 99.99%', 'Security reviews', 'Dedicated support'],
    cta: 'Contact Sales',
  },
];

export default function BillingPage() {
  const { toast } = useToast();
  const { license, refreshLicense } = useTier();

  const [summary, setSummary] = React.useState<BillingSummary | null>(null);
  const [invoices, setInvoices] = React.useState<StripeInvoice[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [upgradingTo, setUpgradingTo] = React.useState<PlanCode | null>(null);
  const [openingPortal, setOpeningPortal] = React.useState(false);
  const [provider, setProvider] = React.useState<'stripe' | 'flutterwave' | 'cryptomus'>('stripe');
  const [checkoutStatus, setCheckoutStatus] = React.useState<string | null>(null);
  const [licenseKey, setLicenseKey] = React.useState('');
  const [activating, setActivating] = React.useState(false);

  React.useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    setCheckoutStatus(params.get('checkout'));
  }, []);

  React.useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const res = await api.get('/billing/summary/').catch(() => null);
        if (res) {
          const s = res.data as BillingSummary;
          setSummary(s);
          setProvider(
            s?.stripe_configured
              ? 'stripe'
              : s?.flutterwave_configured
                ? 'flutterwave'
                : s?.cryptomus_configured
                  ? 'cryptomus'
                  : 'stripe'
          );
        }

        const inv = await api.get('/billing/invoices/').catch(() => null);
        if (inv) {
          setInvoices(Array.isArray(inv?.data?.invoices) ? inv.data.invoices : []);
        }
      } catch (err: any) {
        const msg =
          err?.response?.data?.error ||
          err?.response?.data?.detail ||
          err?.message ||
          'Failed to load billing.';
        toast({ title: 'Billing error', description: msg, variant: 'destructive' });
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, [toast]);

  const handleActivateLicense = async () => {
    if (!licenseKey) return;
    setActivating(true);
    try {
      await licensingApi.activate(licenseKey);
      await refreshLicense();
      toast({ title: 'License Activated', description: 'Features unlocked successfully.' });
      setLicenseKey('');
    } catch (e: any) {
      toast({
        title: 'Activation Failed',
        description: e?.response?.data?.detail || e?.message || 'Invalid license key',
        variant: 'destructive',
      });
    } finally {
      setActivating(false);
    }
  };

  const handleDeactivateLicense = async () => {
    if (!confirm('Deactivate license? This will downgrade features to Community.')) return;
    try {
      await licensingApi.deactivate();
      await refreshLicense();
      toast({ title: 'License Deactivated' });
    } catch {
      toast({ title: 'Error', variant: 'destructive' });
    }
  };

  React.useEffect(() => {
    if (checkoutStatus === 'success') {
      toast({ title: 'Payment successful', description: 'Your plan will update shortly.' });
    } else if (checkoutStatus === 'cancelled') {
      toast({ title: 'Checkout cancelled', description: 'No changes were made.' });
    }
  }, [checkoutStatus, toast]);

  const handleUpgrade = async (plan: PlanCode) => {
    if (!summary) return;

    const providerConfigured =
      provider === 'stripe'
        ? summary.stripe_configured
        : provider === 'flutterwave'
          ? summary.flutterwave_configured
          : summary.cryptomus_configured;
    if (!providerConfigured) {
      toast({
        title: 'Billing not configured',
        description: `${provider} is not configured on the server.`,
        variant: 'destructive',
      });
      return;
    }

    if (plan === 'ENTERPRISE') {
      toast({
        title: 'Enterprise plan',
        description: 'Contact sales to activate Enterprise.',
      });
      return;
    }

    if (plan === summary.plan) return;

    setUpgradingTo(plan);
    try {
      const res = await api.post('/billing/checkout/', { plan, provider });
      const url = res?.data?.url;
      if (!url) throw new Error('Missing checkout URL');
      window.location.assign(url);
    } catch (err: any) {
      const msg =
        err?.response?.data?.error ||
        err?.response?.data?.detail ||
        err?.message ||
        'Checkout failed.';
      toast({ title: 'Upgrade failed', description: msg, variant: 'destructive' });
      setUpgradingTo(null);
    }
  };

  const handleOpenPortal = async () => {
    if (!summary?.stripe_configured) {
      toast({
        title: 'Stripe not configured',
        description: 'The customer portal requires Stripe to be configured.',
        variant: 'destructive',
      });
      return;
    }
    setOpeningPortal(true);
    try {
      const res = await api.post('/billing/portal/', {
        return_url: typeof window !== 'undefined' ? `${window.location.origin}/billing` : '',
      });
      const url = res?.data?.url;
      if (!url) throw new Error('Missing portal URL');
      window.location.assign(url);
    } catch (err: any) {
      const msg =
        err?.response?.data?.error ||
        err?.response?.data?.detail ||
        err?.message ||
        'Failed to open billing portal.';
      toast({ title: 'Portal error', description: msg, variant: 'destructive' });
    } finally {
      setOpeningPortal(false);
    }
  };

  return (
    <DashboardShell>
       <div className="container max-w-6xl mx-auto p-6 space-y-10">
         {/* SMSLY Ecosystem Cross-Sell */}
         <motion.div
           initial={{ opacity: 0, y: -6 }}
           animate={{ opacity: 1, y: 0 }}
           transition={{ duration: 0.4 }}
           className="mb-6"
         >
           <EnhancedCrossSell variant="compact" dismissible={true} />
         </motion.div>
         
         <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
           <div>
             <h1 className="text-3xl font-bold mb-1">Billing & License</h1>
             <p className="text-muted-foreground">Manage your platform license and billing.</p>
           </div>
           <div className="flex gap-2">
             <Button
               variant="outline"
               onClick={handleOpenPortal}
               disabled={openingPortal || loading || !summary?.stripe_configured}
               title={!summary?.stripe_configured ? 'Stripe is not configured' : undefined}
             >
               {openingPortal ? (
                 <Loader2 className="mr-2 h-4 w-4 animate-spin" />
               ) : (
                 <ExternalLink className="mr-2 h-4 w-4" />
               )}
               Manage Billing
             </Button>
           </div>
         </div>

        <Card className="border-primary/20 bg-primary/5">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5 text-primary" />
              Platform License
            </CardTitle>
            <CardDescription>
              Current Tier: <Badge className="ml-1 uppercase">{license?.tier || 'COMMUNITY'}</Badge>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-col md:flex-row gap-4 items-end">
              <div className="flex-1 space-y-2 w-full">
                <label className="text-sm font-medium">License Key</label>
                <div className="flex gap-2">
                  <Input
                    placeholder="smsly_..."
                    value={licenseKey}
                    onChange={(e) => setLicenseKey(e.target.value)}
                    type="password"
                  />
                  <Button onClick={handleActivateLicense} disabled={activating}>
                    {activating ? <Loader2 className="animate-spin" /> : 'Activate'}
                  </Button>
                </div>
              </div>
              {license?.tier !== 'community' && (
                <Button
                  variant="outline"
                  onClick={handleDeactivateLicense}
                  className="border-red-500/50 text-red-500 hover:bg-red-500/10"
                >
                  Deactivate License
                </Button>
              )}
            </div>
            {license?.tier !== 'community' && (
              <div className="text-sm text-muted-foreground grid grid-cols-2 gap-4 pt-2">
                <div>
                  Licensed To:{' '}
                  <span className="font-medium text-foreground">{license?.licensed_to || 'N/A'}</span>
                </div>
                <div>
                  Expires:{' '}
                  <span className="font-medium text-foreground">
                    {license?.expires_at ? new Date(license.expires_at).toLocaleDateString() : 'Never'}
                  </span>
                </div>
                <div>
                  Max Services:{' '}
                  <span className="font-medium text-foreground">
                    {license?.max_services === -1 ? 'Unlimited' : license?.max_services}
                  </span>
                </div>
                <div>
                  Max Team Members:{' '}
                  <span className="font-medium text-foreground">
                    {license?.max_team_members === -1 ? 'Unlimited' : license?.max_team_members}
                  </span>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {loading ? (
          <div className="py-24 text-center text-muted-foreground">Loading billing...</div>
        ) : !summary ? (
          <div className="py-24 text-center text-muted-foreground">Billing unavailable.</div>
        ) : (
          <>
            {!summary.stripe_configured && !summary.flutterwave_configured && !summary.cryptomus_configured && (
              <Card className="border-yellow-500/40 bg-yellow-500/5">
                <CardHeader>
                  <CardTitle>Billing Not Configured</CardTitle>
                  <CardDescription>
                    Configure Stripe, Flutterwave, or Cryptomus environment variables on the server.
                  </CardDescription>
                </CardHeader>
              </Card>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-muted-foreground">Pay with:</span>
              <Button
                size="sm"
                variant={provider === 'stripe' ? 'default' : 'outline'}
                onClick={() => setProvider('stripe')}
                disabled={!summary.stripe_configured}
              >
                Stripe
              </Button>
              <Button
                size="sm"
                variant={provider === 'flutterwave' ? 'default' : 'outline'}
                onClick={() => setProvider('flutterwave')}
                disabled={!summary.flutterwave_configured}
              >
                Flutterwave
              </Button>
              <Button
                size="sm"
                variant={provider === 'cryptomus' ? 'default' : 'outline'}
                onClick={() => setProvider('cryptomus')}
                disabled={!summary.cryptomus_configured}
              >
                Cryptomus
              </Button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm text-muted-foreground">Current Plan</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <div className="text-2xl font-bold">{summary.plan}</div>
                  <div className="flex items-center gap-2">
                    <Badge variant="outline">{summary.subscription_status || 'NONE'}</Badge>
                    {summary.current_period_end && (
                      <span className="text-xs text-muted-foreground">
                        Renews: {new Date(summary.current_period_end).toLocaleDateString()}
                      </span>
                    )}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-sm text-muted-foreground">Estimated Cost</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <div className="text-2xl font-bold">
                    ${Number(summary.total_estimated_cost || 0).toFixed(2)}{' '}
                    <span className="text-sm text-muted-foreground">({summary.billing_period})</span>
                  </div>
                  <div className="text-xs text-muted-foreground">
                    Usage is metered from hourly snapshots of active services.
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-sm text-muted-foreground">Credits</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <div className="text-2xl font-bold">${Number(summary.balance || 0).toFixed(2)}</div>
                  <div className="text-xs text-muted-foreground">Stored balance (not yet wired to payments).</div>
                </CardContent>
              </Card>
            </div>

            <section className="space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-bold">Plans</h2>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                {PLANS.map((p) => {
                  const isCurrent = summary.plan === p.code;
                  return (
                    <Card key={p.code} className={`relative ${isCurrent ? 'border-primary ring-2 ring-primary/20' : ''}`}>
                      {p.popular && (
                        <div className="absolute top-0 right-0 bg-primary text-primary-foreground text-xs font-bold px-2 py-1 rounded-bl-lg rounded-tr-lg">
                          POPULAR
                        </div>
                      )}
                      <CardHeader>
                        <CardTitle>{p.name}</CardTitle>
                        <CardDescription>{p.description}</CardDescription>
                        <div className="mt-4">
                          <span className="text-4xl font-bold">{p.price}</span>
                          {p.code !== 'ENTERPRISE' && <span className="text-muted-foreground">/mo</span>}
                        </div>
                      </CardHeader>
                      <CardContent className="space-y-2">
                        {p.features.map((f) => (
                          <div key={f} className="flex items-center gap-2 text-sm">
                            <Check size={16} className="text-primary" /> {f}
                          </div>
                        ))}
                      </CardContent>
                      <CardFooter>
                        <Button
                          className="w-full"
                          variant="outline"
                          disabled={true}
                        >
                          Included in Self-Hosted
                        </Button>
                      </CardFooter>
                    </Card>
                  );
                })}
              </div>
            </section>

            <section className="space-y-4">
              <h2 className="text-xl font-bold">Invoices</h2>

              <Card>
                {!summary.stripe_configured ? (
                  <div className="p-8 text-center text-muted-foreground">
                    Invoices are available when Stripe billing is enabled.
                  </div>
                ) : invoices.length === 0 ? (
                  <div className="p-8 text-center text-muted-foreground">No invoices found.</div>
                ) : (
                  <div className="divide-y divide-border">
                    {invoices.map((inv) => (
                      <div key={inv.id} className="p-4 flex justify-between items-center">
                        <div className="flex items-center gap-4 min-w-0">
                          <div className="p-2 bg-muted rounded-full">
                            <Receipt size={18} className="text-muted-foreground" />
                          </div>
                          <div className="min-w-0">
                            <div className="font-bold text-sm truncate">Invoice {inv.id}</div>
                            <div className="text-xs text-muted-foreground">
                              {inv.created ? new Date(inv.created * 1000).toLocaleDateString() : 'Unknown date'} | {inv.status}
                            </div>
                          </div>
                        </div>
                        <div className="flex items-center gap-4">
                          <span className="font-bold">
                            {inv.currency?.toUpperCase() === 'USD' ? '$' : ''}{' '}
                            {(Number(inv.amount_paid || 0) / 100).toFixed(2)}
                          </span>
                          {inv.invoice_pdf || inv.hosted_invoice_url ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="gap-2"
                              onClick={() => window.open(inv.invoice_pdf || inv.hosted_invoice_url || '#', '_blank')}
                            >
                              <Download size={14} /> PDF
                            </Button>
                          ) : (
                            <Button variant="ghost" size="sm" className="gap-2" disabled>
                              <Download size={14} /> PDF
                            </Button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Card>
            </section>

            <section className="space-y-3">
              <h2 className="text-xl font-bold">Usage Breakdown</h2>
              <Card>
                {summary.services.length === 0 ? (
                  <div className="p-8 text-center text-muted-foreground">No services found.</div>
                ) : (
                  <div className="divide-y divide-border">
                    {summary.services.map((s) => (
                      <div key={s.id} className="p-4 flex justify-between items-center">
                        <div className="flex items-center gap-3">
                          <div className="p-2 bg-muted rounded-full">
                            <CreditCard size={18} className="text-muted-foreground" />
                          </div>
                          <div>
                            <div className="font-medium">{s.name}</div>
                            <div className="text-xs text-muted-foreground">
                              Hours: {s.cpu_usage_hours} | Cost: ${Number(s.cost || 0).toFixed(2)}
                            </div>
                          </div>
                        </div>
                        <div className="font-mono text-sm">${Number(s.cost || 0).toFixed(2)}</div>
                      </div>
                    ))}
                  </div>
                )}
              </Card>
            </section>
          </>
        )}
      </div>
    </DashboardShell>
  );
}
