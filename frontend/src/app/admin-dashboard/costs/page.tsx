'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { billingApi } from '@/lib/api';
import { Loader2 } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import api from '@/lib/api';

export default function CostsPage() {
    const [costs, setCosts] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [accessDenied, setAccessDenied] = useState(false);

    useEffect(() => {
        async function load() {
            try {
                await api.get('/system/config/');
                const data = await billingApi.adminGetCosts();
                setCosts(data);
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
