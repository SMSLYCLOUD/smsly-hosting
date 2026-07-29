'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { billingApi } from '@/lib/api';
import { Loader2 } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import api from '@/lib/api';

export default function PnLPage() {
    const [overview, setOverview] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [accessDenied, setAccessDenied] = useState(false);

    useEffect(() => {
        async function load() {
            try {
                await api.get('/system/config/');
                const data = await billingApi.adminGetOverview();
                setOverview(data);
            } catch (err: unknown) {
                if ((err as { response?: { status?: number } })?.response?.status === 403) {
                    setAccessDenied(true);
                } else {
                    console.error(err);
                }
            } finally {
                setLoading(false);
            }
        }
        load();
    }, []);

    if (loading) {
        return (
            <DashboardShell>
                <div className="flex justify-center p-10">
                    <Loader2 className="animate-spin" />
                </div>
            </DashboardShell>
        );
    }

    if (accessDenied) {
        return (
            <DashboardShell>
                <div className="container p-6">
                    <div className="border border-border rounded-xl bg-card p-6 space-y-3">
                        <h1 className="text-2xl font-bold">Admin Access Required</h1>
                        <p className="text-muted-foreground">You do not have permission to view this page.</p>
                        <Link href="/dashboard" className="inline-flex px-4 py-2 rounded-lg bg-primary text-white font-medium">
                            Go to User Dashboard
                        </Link>
                    </div>
                </div>
            </DashboardShell>
        );
    }

    return (
        <DashboardShell>
            <div className="container p-6 space-y-6">
                <h1 className="text-3xl font-bold">Profit & Loss (P&L)</h1>

                <Card>
                    <CardHeader>
                        <CardTitle>Monthly Overview</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="space-y-4">
                            <div className="flex justify-between border-b pb-2">
                                <span className="font-semibold">Revenue</span>
                                <span className="font-bold text-green-600">${overview.total_revenue_period.toFixed(2)}</span>
                            </div>
                            <div className="flex justify-between border-b pb-2">
                                <span className="font-semibold">COGS (Infrastructure)</span>
                                <span className="font-bold text-red-600">(${overview.total_costs_period.toFixed(2)})</span>
                            </div>
                            <div className="flex justify-between pt-2">
                                <span className="font-bold text-lg">Gross Profit</span>
                                <span className="font-bold text-lg">${overview.net_profit_period.toFixed(2)}</span>
                            </div>
                            <div className="flex justify-between pt-2 text-sm text-muted-foreground">
                                <span>Margin</span>
                                <span>{overview.gross_margin_percent.toFixed(1)}%</span>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </DashboardShell>
    );
}
