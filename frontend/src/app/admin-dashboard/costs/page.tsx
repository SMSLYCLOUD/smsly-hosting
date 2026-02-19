'use client';

import { useState, useEffect } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { billingApi } from '@/lib/api';
import { Loader2 } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default function CostsPage() {
    const [costs, setCosts] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function load() {
            try {
                const data = await billingApi.adminGetCosts();
                setCosts(data);
            } finally {
                setLoading(false);
            }
        }
        load();
    }, []);

    if (loading) return <div className="flex justify-center p-10"><Loader2 className="animate-spin" /></div>;

    return (
        <DashboardShell>
            <div className="container p-6">
                <h1 className="text-3xl font-bold mb-6">Infrastructure Costs</h1>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {costs.map((c) => (
                        <Card key={c.name}>
                            <CardHeader>
                                <CardTitle>{c.name}</CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div className="text-3xl font-bold">${c.value.toFixed(2)}</div>
                            </CardContent>
                        </Card>
                    ))}
                    {costs.length === 0 && (
                        <div className="text-center py-6 text-muted-foreground col-span-full">No cost data available</div>
                    )}
                </div>
            </div>
        </DashboardShell>
    );
}
