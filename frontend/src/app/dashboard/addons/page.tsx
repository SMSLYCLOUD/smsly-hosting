"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { addonsApi, Addon } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Plus, Database, Server, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { PageHeader } from "@/components/ui/page-header";

export default function AddonsPage() {
    const [addons, setAddons] = useState<Addon[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        addonsApi.list().then(setAddons).catch(console.error).finally(() => setLoading(false));
    }, []);

    return (
        <DashboardShell>
            <div className="container mx-auto py-10">
                <PageHeader
                    title="Addons"
                    description="Manage your databases, caches, and storage."
                    icon={<Database className="h-8 w-8 text-primary" />}
                    actions={
                        <Link href="/dashboard/addons/new">
                            <Button>
                                <Plus className="w-4 h-4 mr-2" />
                                New Addon
                            </Button>
                        </Link>
                    }
                />

                {loading ? (
                    <div className="flex justify-center py-10"><RefreshCw className="animate-spin text-muted-foreground" /></div>
                ) : addons.length === 0 ? (
                    <div className="text-center py-20 bg-muted/20 rounded-lg">
                        <Database className="w-12 h-12 mx-auto text-muted-foreground mb-4 opacity-50" />
                        <h3 className="text-lg font-medium">No Addons Yet</h3>
                        <p className="text-muted-foreground mb-6">Provision a database or cache for your applications.</p>
                        <Link href="/dashboard/addons/new">
                            <Button variant="outline">Create First Addon</Button>
                        </Link>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {addons.map(addon => (
                            <Link key={addon.id} href={`/dashboard/addons/${addon.id}`}>
                                <Card className="hover:border-primary/50 transition-colors cursor-pointer">
                                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                                        <CardTitle className="text-sm font-medium">
                                            {addon.name}
                                        </CardTitle>
                                        {addon.addon_type === 'POSTGRES' && <Database className="h-4 w-4 text-blue-500" />}
                                        {addon.addon_type === 'REDIS' && <Server className="h-4 w-4 text-red-500" />}
                                    </CardHeader>
                                    <CardContent>
                                        <div className="text-2xl font-bold flex items-center gap-2">
                                            {addon.addon_type}
                                        </div>
                                        <div className="flex items-center mt-2 space-x-2">
                                            <Badge variant={addon.status === 'RUNNING' ? 'default' : 'secondary'}>
                                                {addon.status}
                                            </Badge>
                                            <span className="text-xs text-muted-foreground">
                                                {new Date(addon.created_at).toLocaleDateString()}
                                            </span>
                                        </div>
                                    </CardContent>
                                </Card>
                            </Link>
                        ))}
                    </div>
                )}
            </div>
        </DashboardShell>
    );
}
