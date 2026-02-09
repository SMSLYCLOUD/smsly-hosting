'use client';

import { Navbar } from '@/components/layout/Navbar';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Users, DollarSign, Activity, Settings } from 'lucide-react';

export default function ResellerPage() {
    return (
        <main className="h-screen flex flex-col bg-background">
            <Navbar />
            <div className="container mx-auto py-10">
                <div className="flex justify-between items-center mb-8">
                    <div>
                        <h1 className="text-3xl font-bold tracking-tight">Partner Console</h1>
                        <p className="text-muted-foreground">Manage your white-labeled hosting platform.</p>
                    </div>
                </div>

                <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                    <Card>
                        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                            <CardTitle className="text-sm font-medium">Total Managed Teams</CardTitle>
                            <Users className="h-4 w-4 text-muted-foreground" />
                        </CardHeader>
                        <CardContent>
                            <div className="text-2xl font-bold">12</div>
                            <p className="text-xs text-muted-foreground">+2 from last month</p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                            <CardTitle className="text-sm font-medium">Monthly Revenue</CardTitle>
                            <DollarSign className="h-4 w-4 text-muted-foreground" />
                        </CardHeader>
                        <CardContent>
                            <div className="text-2xl font-bold">$1,240.00</div>
                            <p className="text-xs text-muted-foreground">+10% from last month</p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                            <CardTitle className="text-sm font-medium">Platform Health</CardTitle>
                            <Activity className="h-4 w-4 text-muted-foreground" />
                        </CardHeader>
                        <CardContent>
                            <div className="text-2xl font-bold text-emerald-500">99.99%</div>
                            <p className="text-xs text-muted-foreground">All systems operational</p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                            <CardTitle className="text-sm font-medium">Branding</CardTitle>
                            <Settings className="h-4 w-4 text-muted-foreground" />
                        </CardHeader>
                        <CardContent>
                            <div className="text-sm font-medium">Acme Corp</div>
                            <p className="text-xs text-muted-foreground">Theme: Dark / Blue</p>
                        </CardContent>
                    </Card>
                </div>
            </div>
        </main>
    );
}
