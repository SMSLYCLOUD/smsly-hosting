'use client';

import { useState, useEffect } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { billingApi } from '@/lib/api';
import { Loader2 } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default function PnLPage() {
    const [overview, setOverview] = useState<any>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function load() {
            try {
                const data = await billingApi.adminGetOverview();
                setOverview(data);
            } finally {
                setLoading(false);
            }
        }
        load();
    }, []);

    if (loading) return <div className="flex justify-center p-10"><Loader2 className="animate-spin" /></div>;

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
