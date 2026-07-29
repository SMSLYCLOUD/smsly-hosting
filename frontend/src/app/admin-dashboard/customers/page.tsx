'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { billingApi } from '@/lib/api';
import { Loader2 } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import api from '@/lib/api';

export default function CustomersPage() {
    const [customers, setCustomers] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [accessDenied, setAccessDenied] = useState(false);

    useEffect(() => {
        async function load() {
            try {
                await api.get('/system/config/');
                const data = await billingApi.adminGetCustomers();
                setCustomers(data);
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
                <h1 className="text-3xl font-bold mb-6">Top Customers</h1>
                <div className="bg-card border rounded-xl overflow-hidden">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Customer</TableHead>
                                <TableHead>Plan</TableHead>
                                <TableHead>MRR</TableHead>
                                <TableHead>Joined</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {customers.map((c) => (
                                <TableRow key={c.id}>
                                    <TableCell className="font-medium">{c.name}</TableCell>
                                    <TableCell>{c.plan}</TableCell>
                                    <TableCell>${c.mrr}</TableCell>
                                    <TableCell>{new Date(c.joined).toLocaleDateString()}</TableCell>
                                </TableRow>
                            ))}
                            {customers.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={4} className="text-center py-6 text-muted-foreground">No data available</TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </div>
            </div>
        </DashboardShell>
    );
}
