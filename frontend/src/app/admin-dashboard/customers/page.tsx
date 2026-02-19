'use client';

import { useState, useEffect } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { billingApi } from '@/lib/api';
import { Loader2 } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

export default function CustomersPage() {
    const [customers, setCustomers] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function load() {
            try {
                const data = await billingApi.adminGetCustomers();
                setCustomers(data);
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
