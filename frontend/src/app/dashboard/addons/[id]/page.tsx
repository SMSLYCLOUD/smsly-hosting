"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { addonsApi, Addon } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { RefreshCw, Database, Server, Key, Eye, EyeOff, Trash2, ArrowLeft, Globe } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { PageHeader } from "@/components/ui/page-header";
import { DbExplorer } from "@/components/addons/DbExplorer";
import { AddonLogsViewer } from "@/components/addons/AddonLogsViewer";
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from '@/components/ui/confirm-dialog';
import { DASHBOARD_ADDONS } from "@/lib/addonConstants";

export default function AddonDetailsPage() {
    const { id } = useParams();
    const router = useRouter();
    const { toast } = useToast();
    const confirm = useConfirm();
    const [addon, setAddon] = useState<Addon | null>(null);
    const [loading, setLoading] = useState(true);
    const [metrics, setMetrics] = useState<any>(null);
    const [showCreds, setShowCreds] = useState(false);
    const [rotating, setRotating] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const [updatingBucket, setUpdatingBucket] = useState(false);

    useEffect(() => {
        async function load() {
            try {
                const data = await addonsApi.get(id as string);
                setAddon(data);
                const m = await addonsApi.getMetrics(id as string);
                setMetrics(m);
            } catch (err) {
                toast({ title: "Error", description: "Failed to load addon details.", variant: "destructive" });
            } finally {
                setLoading(false);
            }
        }
        if (id) load();
    }, [id, toast]);

    const handleRotate = async () => {
        if (!await confirm({ title: 'Rotate credentials?', message: 'This will disconnect all active sessions. Are you sure?', variant: 'destructive', confirmText: 'Rotate' })) return;
        setRotating(true);
        try {
            await addonsApi.rotateCredentials(id as string);
            toast({ title: "Credentials Rotated", description: "Update your applications with the new connection string." });
            // Reload
            const data = await addonsApi.get(id as string);
            setAddon(data);
        } catch (err) {
            toast({ title: "Error", description: "Failed to rotate credentials.", variant: "destructive" });
        } finally {
            setRotating(false);
        }
    };

    const handleDelete = async () => {
        if (!await confirm({ title: 'Delete addon?', message: 'Are you sure you want to delete this addon? This action cannot be undone.', variant: 'destructive', confirmText: 'Delete' })) return;
        setDeleting(true);
        try {
            await addonsApi.delete(id as string);
            toast({ title: "Addon Deleted" });
            router.push('/dashboard/addons');
        } catch (err) {
            toast({ title: "Error", description: "Failed to delete addon.", variant: "destructive" });
            setDeleting(false);
        }
    };

    const handleToggleBucketPublic = async (isPublic: boolean) => {
        setUpdatingBucket(true);
        try {
            await addonsApi.toggleBucketPublic(id as string, isPublic);
            toast({ title: "Bucket Access Updated", description: `Bucket is now ${isPublic ? 'public' : 'private'}.` });
        } catch (err) {
            toast({ title: "Error", description: "Failed to update bucket access policy.", variant: "destructive" });
        } finally {
            setUpdatingBucket(false);
        }
    };

    if (loading) return <DashboardShell><div className="flex justify-center p-20"><RefreshCw className="animate-spin" /></div></DashboardShell>;
    if (!addon) return <DashboardShell><div className="text-center p-20">Addon not found.</div></DashboardShell>;

    return (
        <DashboardShell>
            <div className="container mx-auto py-10 space-y-6">
                <Button variant="ghost" onClick={() => router.back()} className="mb-4 pl-0 hover:bg-transparent hover:text-primary">
                    <ArrowLeft className="mr-2 h-4 w-4" /> Back to Addons
                </Button>

                <div className="flex items-center justify-between">
                    <div>
                        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
                            {addon.name}
                            <Badge variant={addon.status === 'RUNNING' ? 'default' : 'secondary'}>{addon.status}</Badge>
                        </h1>
                        <p className="text-muted-foreground mt-2">{addon.addon_type} Instance • Created {new Date(addon.created_at).toLocaleDateString()}</p>
                    </div>
                    <div className="flex gap-2">
                        {addon.public_domain && (
                            DASHBOARD_ADDONS.includes(addon.addon_type) ? (
                                <Button variant="outline" className="border-emerald-500/20 text-emerald-500 hover:bg-emerald-500/10 hover:text-emerald-500" asChild>
                                    <a href={`https://${addon.public_domain}`} target="_blank" rel="noreferrer">
                                        <Globe className="w-4 h-4 mr-2" /> View Dashboard
                                    </a>
                                </Button>
                            ) : (
                                <Button variant="outline" className="border-zinc-500/20 text-zinc-500 hover:bg-zinc-500/10 hover:text-zinc-500 cursor-not-allowed" title="This addon type does not have an HTTP dashboard. Use TCP clients to connect.">
                                    <Server className="w-4 h-4 mr-2" /> TCP Service
                                </Button>
                            )
                        )}
                        <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
                            {deleting ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4 mr-2" />}
                            Delete Addon
                        </Button>
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    {/* Connection Card */}
                    <Card className="md:col-span-2">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2"><Key className="w-5 h-5 text-orange-500" /> Connection</CardTitle>
                            <CardDescription>Credentials for your applications.</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="bg-muted p-4 rounded-md relative group">
                                <code className="break-all font-mono text-sm block pr-10">
                                    {showCreds ? addon.connection_url : addon.connection_url?.replace(/:[^:]*@/, ':••••••@')}
                                </code>
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity"
                                    onClick={() => setShowCreds(!showCreds)}
                                >
                                    {showCreds ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                </Button>
                            </div>
                            <div className="flex justify-end items-center gap-2">
                                {addon.addon_type === 'MINIO' && (
                                    <div className="mr-auto flex items-center gap-2 text-sm text-muted-foreground">
                                        <span>Bucket Access:</span>
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            disabled={updatingBucket}
                                            onClick={() => {
                                                const makePublic = window.confirm("Make bucket public? Anyone will be able to read objects from the bucket. Ensure you do not store sensitive data if enabling this.");
                                                if (makePublic !== null) handleToggleBucketPublic(makePublic);
                                            }}
                                            className="text-xs"
                                        >
                                            {updatingBucket ? <RefreshCw className="w-3 h-3 animate-spin mr-2" /> : null}
                                            Set Public/Private
                                        </Button>
                                    </div>
                                )}
                                <Button variant="outline" size="sm" onClick={handleRotate} disabled={rotating}>
                                    {rotating ? <RefreshCw className="w-3 h-3 animate-spin mr-2" /> : <RefreshCw className="w-3 h-3 mr-2" />}
                                    Rotate Credentials
                                </Button>
                            </div>
                        </CardContent>
                    </Card>

                    {/* Stats Card */}
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2"><Server className="w-5 h-5 text-green-500" /> Metrics</CardTitle>
                            <CardDescription>Real-time usage stats.</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {metrics ? (
                                <>
                                    <div className="flex justify-between items-center border-b pb-2">
                                        <span className="text-sm font-medium">CPU Usage</span>
                                        <span className="text-lg font-bold">{metrics.cpu_percent || 0}%</span>
                                    </div>
                                    <div className="flex justify-between items-center border-b pb-2">
                                        <span className="text-sm font-medium">Memory</span>
                                        <span className="text-lg font-bold">{metrics.memory_mb || 0} MB</span>
                                    </div>
                                    <div className="flex justify-between items-center pb-2">
                                        <span className="text-sm font-medium">Disk</span>
                                        <span className="text-lg font-bold">{metrics.disk_gb || 0} GB</span>
                                    </div>
                                </>
                            ) : (
                                <div className="text-center text-muted-foreground py-4">Loading metrics...</div>
                            )}
                        </CardContent>
                    </Card>
                </div>

                {/* Tabs for Tools */}
                <Tabs defaultValue="explorer" className="w-full">
                    <TabsList>
                        <TabsTrigger value="explorer" disabled={addon.addon_type !== 'POSTGRES'}>DB Explorer</TabsTrigger>
                        <TabsTrigger value="logs">Logs</TabsTrigger>
                        <TabsTrigger value="backups">Backups</TabsTrigger>
                    </TabsList>

                    <TabsContent value="explorer">
                        <Card>
                            <CardContent className="pt-6">
                                {addon.addon_type === 'POSTGRES' ? (
                                    <DbExplorer addonId={id as string} />
                                ) : (
                                    <div className="text-center py-10 text-muted-foreground">
                                        DB Explorer is only available for PostgreSQL addons.
                                    </div>
                                )}
                            </CardContent>
                        </Card>
                    </TabsContent>

                    <TabsContent value="logs">
                        <Card>
                            <CardContent className="pt-6">
                                <AddonLogsViewer
                                    addonId={id as string}
                                    addonType={addon.addon_type}
                                    status={addon.status}
                                />
                            </CardContent>
                        </Card>
                    </TabsContent>

                    <TabsContent value="backups">
                        <div className="p-4 text-center text-muted-foreground border rounded-lg border-dashed">
                            Managed backups are configured automatically. <br/>
                            Point-in-time recovery coming soon.
                        </div>
                    </TabsContent>
                </Tabs>
            </div>
        </DashboardShell>
    );
}
