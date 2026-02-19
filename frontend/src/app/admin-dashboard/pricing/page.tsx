'use client';

import React, { useState, useEffect } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { billingApi, PricingPlan } from '@/lib/api';
import { Loader2, Plus, Save } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';

export default function AdminPricingPage() {
    const { toast } = useToast();
    const [plans, setPlans] = useState<PricingPlan[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        loadPlans();
    }, []);

    async function loadPlans() {
        try {
            const data = await billingApi.adminGetPlans();
            setPlans(data);
        } catch (err) {
            console.error(err);
            toast({ title: 'Failed to load plans', variant: 'destructive' });
        } finally {
            setLoading(false);
        }
    }

    async function handleUpdate(plan: PricingPlan) {
        try {
            await billingApi.adminUpdatePlan(plan.id, plan);
            toast({ title: 'Plan updated successfully' });
        } catch (err) {
             console.error(err);
            toast({ title: 'Update failed', variant: 'destructive' });
        }
    }

    if (loading) return (
        <DashboardShell>
            <div className="flex justify-center h-screen items-center">
                 <Loader2 className="animate-spin w-8 h-8" />
            </div>
        </DashboardShell>
    );

    return (
        <DashboardShell>
            <div className="container p-6 space-y-6">
                <div className="flex justify-between items-center">
                    <h1 className="text-3xl font-bold">Pricing Plans</h1>
                    <Button><Plus className="w-4 h-4 mr-2" /> New Plan</Button>
                </div>

                <div className="grid grid-cols-1 gap-6">
                    {plans.map(plan => (
                        <Card key={plan.id}>
                            <CardHeader>
                                <CardTitle className="flex justify-between items-center">
                                    <Input
                                        value={plan.name}
                                        onChange={e => {
                                            const newPlans = [...plans];
                                            const idx = newPlans.findIndex(p => p.id === plan.id);
                                            newPlans[idx].name = e.target.value;
                                            setPlans(newPlans);
                                        }}
                                        className="w-1/3 font-bold text-lg"
                                    />
                                    <div className="flex items-center gap-2">
                                        <Label>Active</Label>
                                        <Switch
                                            checked={plan.is_active}
                                            onCheckedChange={checked => {
                                                const newPlans = [...plans];
                                                const idx = newPlans.findIndex(p => p.id === plan.id);
                                                newPlans[idx].is_active = checked;
                                                setPlans(newPlans);
                                            }}
                                        />
                                    </div>
                                </CardTitle>
                            </CardHeader>
                            <CardContent className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                <div className="space-y-1">
                                    <Label>Monthly Price ($)</Label>
                                    <Input
                                        type="number"
                                        value={plan.price_monthly_usd}
                                        onChange={e => {
                                            const newPlans = [...plans];
                                            const idx = newPlans.findIndex(p => p.id === plan.id);
                                            newPlans[idx].price_monthly_usd = parseFloat(e.target.value);
                                            setPlans(newPlans);
                                        }}
                                    />
                                </div>
                                <div className="space-y-1">
                                    <Label>Max Services</Label>
                                    <Input
                                        type="number"
                                        value={plan.max_services}
                                        onChange={e => {
                                            const newPlans = [...plans];
                                            const idx = newPlans.findIndex(p => p.id === plan.id);
                                            newPlans[idx].max_services = parseInt(e.target.value);
                                            setPlans(newPlans);
                                        }}
                                    />
                                </div>
                                <div className="space-y-1">
                                    <Label>Max CPU</Label>
                                    <Input
                                        type="number"
                                        value={plan.max_cpu_cores}
                                        onChange={e => {
                                            const newPlans = [...plans];
                                            const idx = newPlans.findIndex(p => p.id === plan.id);
                                            newPlans[idx].max_cpu_cores = parseFloat(e.target.value);
                                            setPlans(newPlans);
                                        }}
                                    />
                                </div>
                                <div className="space-y-1">
                                    <Label>Max RAM (MB)</Label>
                                    <Input
                                        type="number"
                                        value={plan.max_memory_mb}
                                        onChange={e => {
                                            const newPlans = [...plans];
                                            const idx = newPlans.findIndex(p => p.id === plan.id);
                                            newPlans[idx].max_memory_mb = parseInt(e.target.value);
                                            setPlans(newPlans);
                                        }}
                                    />
                                </div>
                                <div className="col-span-full flex justify-end">
                                    <Button onClick={() => handleUpdate(plan)}>
                                        <Save className="w-4 h-4 mr-2" /> Save Changes
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>
                    ))}

                    {plans.length === 0 && (
                        <div className="text-center py-12 text-muted-foreground">
                            No plans found. Create one to get started.
                        </div>
                    )}
                </div>
            </div>
        </DashboardShell>
    );
}
