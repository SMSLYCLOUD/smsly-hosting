'use client';

import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

export default function ResellerPage() {
    return (
        <DashboardShell>
            <div className="container p-6">
                <div className="flex justify-between items-center mb-6">
                    <h1 className="text-3xl font-bold">Reseller Dashboard</h1>
                    <Badge>Beta</Badge>
                </div>

                <div className="grid gap-6 md:grid-cols-2">
                    <Card>
                        <CardHeader>
                            <CardTitle>White-Label Settings</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <p className="text-muted-foreground">Configure custom branding, domain, and colors for your customers.</p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader>
                            <CardTitle>Customer Management</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <p className="text-muted-foreground">Manage your sub-accounts and their resource usage.</p>
                        </CardContent>
                    </Card>
                </div>
            </div>
        </DashboardShell>
    );
}
