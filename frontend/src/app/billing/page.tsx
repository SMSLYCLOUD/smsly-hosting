'use client';

import React, { useState } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardContent, CardFooter, CardDescription } from '@/components/ui/card';
import { Check, CreditCard, Download } from 'lucide-react';

export default function BillingPage() {
  const [selectedPlan, setSelectedPlan] = useState('pro');

  return (
    <DashboardShell>

      <div className="container max-w-6xl mx-auto p-6 space-y-12">

        {/* Plans */}
        <section>
            <div className="text-center mb-8">
                <h1 className="text-3xl font-bold mb-2">Simple, Transparent Pricing</h1>
                <p className="text-muted-foreground">Pay only for what you use.</p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
                {['Hobby', 'Pro', 'Enterprise'].map((plan) => (
                    <Card key={plan} className={`relative ${selectedPlan === plan.toLowerCase() ? 'border-primary ring-2 ring-primary/20' : ''}`}>
                        {plan === 'Pro' && <div className="absolute top-0 right-0 bg-primary text-primary-foreground text-xs font-bold px-2 py-1 rounded-bl-lg rounded-tr-lg">POPULAR</div>}
                        <CardHeader>
                            <CardTitle>{plan}</CardTitle>
                            <div className="mt-4">
                                <span className="text-4xl font-bold">${plan === 'Hobby' ? 5 : plan === 'Pro' ? 29 : 199}</span>
                                <span className="text-muted-foreground">/mo</span>
                            </div>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            <div className="flex items-center gap-2 text-sm"><Check size={16} className="text-primary" /> {plan === 'Hobby' ? '2' : plan === 'Pro' ? '10' : 'Unlimited'} Services</div>
                            <div className="flex items-center gap-2 text-sm"><Check size={16} className="text-primary" /> Auto-Scaling</div>
                            <div className="flex items-center gap-2 text-sm"><Check size={16} className="text-primary" /> AI Diagnostics</div>
                        </CardContent>
                        <CardFooter>
                            <Button className="w-full" variant={selectedPlan === plan.toLowerCase() ? 'default' : 'outline'} onClick={() => setSelectedPlan(plan.toLowerCase())}>
                                {selectedPlan === plan.toLowerCase() ? 'Current Plan' : 'Upgrade'}
                            </Button>
                        </CardFooter>
                    </Card>
                ))}
            </div>
        </section>

        {/* Invoice History */}
        <section>
            <h2 className="text-xl font-bold mb-4">Invoice History</h2>
            <Card>
                <div className="divide-y divide-border">
                    {[1,2,3].map((i) => (
                        <div key={i} className="p-4 flex justify-between items-center hover:bg-muted/50 transition-colors">
                            <div className="flex items-center gap-4">
                                <div className="p-2 bg-muted rounded-full">
                                    <CreditCard size={20} className="text-muted-foreground" />
                                </div>
                                <div>
                                    <div className="font-bold text-sm">Invoice #INV-2024-00{i}</div>
                                    <div className="text-xs text-muted-foreground">Paid on Mar {10-i}, 2024</div>
                                </div>
                            </div>
                            <div className="flex items-center gap-6">
                                <span className="font-bold">${(29.00 + i * 2.5).toFixed(2)}</span>
                                <Button variant="ghost" size="sm" className="gap-2">
                                    <Download size={14} /> PDF
                                </Button>
                            </div>
                        </div>
                    ))}
                </div>
            </Card>
        </section>
      </div>
    </DashboardShell>
  );
}
