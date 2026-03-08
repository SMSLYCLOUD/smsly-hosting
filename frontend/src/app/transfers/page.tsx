'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ArrowRight, Loader2, Server, CheckCircle2, RotateCcw, Lock, Key } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';
import api, { servicesApi, Service, serversApi, ManagedServer } from '@/lib/api';
import { Progress } from '@/components/ui/progress';
import { RequiresTier } from '@/components/licensing/RequiresTier';

export default function TransfersPage() {
    const { toast } = useToast();
    const [services, setServices] = useState<Service[]>([]);
    const [servers, setServers] = useState<ManagedServer[]>([]);
    const [serversLoading, setServersLoading] = useState(false);
    const [step, setStep] = useState(1);
    const [sshAuthMethod, setSshAuthMethod] = useState<'password' | 'key'>('password');
    const [formData, setFormData] = useState({
        transfer_type: 'SERVICE',
        service_id: '',
        target_server_ip: '',
        target_server_id: '',
        target_ssh_key: '',
        target_ssh_password: '',
    });
    const [transfers, setTransfers] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);
    const hasTargetIp = formData.target_server_ip.trim().length > 0;
    const hasAuth = sshAuthMethod === 'password'
        ? formData.target_ssh_password.trim().length > 0
        : formData.target_ssh_key.trim().length > 0;
    const usingConnectedTarget = formData.target_server_id.trim().length > 0;

    const isValidIp = (value: string) => {
        const v4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
        const v6 = /^(([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|::1)$/;
        return v4.test(value.trim()) || v6.test(value.trim());
    };

    const loadServers = useCallback(async () => {
        setServersLoading(true);
        try {
            const list = await serversApi.list();
            const normalized = (Array.isArray(list) ? list : []).sort((a, b) => {
                if (a.status === b.status) return a.name.localeCompare(b.name);
                if (a.status === 'ONLINE') return -1;
                if (b.status === 'ONLINE') return 1;
                return 0;
            });

            if (normalized.length > 0) {
                setServers(normalized);
            } else {
                const refreshed = await serversApi.checkAll().catch(() => ({ servers: [] }));
                setServers(Array.isArray(refreshed?.servers) ? refreshed.servers : []);
            }
        } catch (err) {
            console.error(err);
            toast({
                title: "Connected servers unavailable",
                description: "Could not load managed servers list.",
                variant: "destructive",
            });
        } finally {
            setServersLoading(false);
        }
    }, [toast]);

    const loadTransfers = useCallback(async () => {
        try {
            const res = await api.get('/transfers/');
            setTransfers(Array.isArray(res.data) ? res.data : res.data.results || []);
        } catch (err) { console.error(err); }
    }, []);

    useEffect(() => {
        servicesApi.list().then(setServices);
        loadServers();
        loadTransfers();
    }, [loadServers, loadTransfers]);

    const handleStartTransfer = async () => {
        setLoading(true);
        try {
            await api.post('/transfers/', formData);
            toast({ title: "Transfer Initiated", description: "Migration process started." });
            setStep(1);
            setFormData({
                transfer_type: 'SERVICE',
                service_id: '',
                target_server_ip: '',
                target_server_id: '',
                target_ssh_key: '',
                target_ssh_password: '',
            });
            loadTransfers();
        } catch (err: any) {
            const data = err.response?.data;
            let msg = 'Error starting transfer';
            if (data) {
                if (typeof data === 'string') msg = data;
                else if (data.error) msg = data.error;
                else if (data.non_field_errors) msg = data.non_field_errors.join(', ');
                else {
                    // DRF field-level validation errors: {field: ["msg"]}
                    const parts = Object.entries(data).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`);
                    if (parts.length) msg = parts.join(' | ');
                }
            }
            toast({ title: "Transfer Failed", description: msg, variant: "destructive" });
        } finally {
            setLoading(false);
        }
    };

    return (
        <DashboardShell>
            <RequiresTier tier="pro">
            <div className="container max-w-5xl mx-auto p-6 space-y-8">
                <div className="flex items-center justify-between">
                    <div>
                        <h1 className="text-3xl font-bold">Server Migration</h1>
                        <p className="text-muted-foreground">Move services or entire servers with zero-downtime DNS cutover.</p>
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                    {/* Wizard */}
                    <Card>
                        <CardHeader>
                            <CardTitle>New Transfer</CardTitle>
                            <CardDescription>Step {step} of 3</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {step === 1 && (
                                <>
                                    <div className="space-y-2">
                                        <Label>Scope</Label>
                                        <div className="rounded-lg border p-3 text-sm font-medium">
                                            Single Service
                                        </div>
                                    </div>
                                    {formData.transfer_type === 'SERVICE' && (
                                        <div className="space-y-2">
                                            <Label>Select Service</Label>
                                            <Select
                                                value={formData.service_id}
                                                onValueChange={v => setFormData({...formData, service_id: v})}
                                            >
                                                <SelectTrigger><SelectValue placeholder="Choose service..." /></SelectTrigger>
                                                <SelectContent>
                                                    {services.map(s => (
                                                        <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    )}
                                    <Button
                                        onClick={() => setStep(2)}
                                        disabled={formData.transfer_type === 'SERVICE' && !formData.service_id}
                                        className="w-full"
                                    >
                                        Next <ArrowRight className="ml-2 w-4 h-4" />
                                    </Button>
                                </>
                            )}

                            {step === 2 && (
                                <>
                                    <div className="space-y-2">
                                        <Label>Target Server IP</Label>
                                        <Input
                                            placeholder="1.2.3.4"
                                            value={formData.target_server_ip}
                                            onChange={e => setFormData({
                                                ...formData,
                                                target_server_ip: e.target.value,
                                                target_server_id: '',
                                            })}
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Authentication</Label>
                                        {usingConnectedTarget && (
                                            <p className="text-xs text-emerald-600">
                                                Using saved SSH credentials from selected connected server.
                                            </p>
                                        )}
                                        <div className="flex items-center gap-2 mb-3">
                                            <button
                                                onClick={() => setSshAuthMethod('password')}
                                                className={`px-3 py-1.5 rounded-md text-xs font-medium flex items-center gap-1.5 transition-all ${
                                                    sshAuthMethod === 'password'
                                                        ? 'bg-blue-500/10 text-blue-500 border border-blue-500/30'
                                                        : 'border border-border text-muted-foreground hover:text-foreground'
                                                }`}
                                            >
                                                <Lock className="w-3 h-3" /> Password
                                            </button>
                                            <button
                                                onClick={() => setSshAuthMethod('key')}
                                                className={`px-3 py-1.5 rounded-md text-xs font-medium flex items-center gap-1.5 transition-all ${
                                                    sshAuthMethod === 'key'
                                                        ? 'bg-blue-500/10 text-blue-500 border border-blue-500/30'
                                                        : 'border border-border text-muted-foreground hover:text-foreground'
                                                }`}
                                            >
                                                <Key className="w-3 h-3" /> SSH Key
                                            </button>
                                        </div>

                                        {sshAuthMethod === 'password' ? (
                                            <>
                                                <Input
                                                    type="password"
                                                    placeholder="Root SSH password"
                                                    value={formData.target_ssh_password}
                                                    onChange={e => setFormData({...formData, target_ssh_password: e.target.value})}
                                                />
                                                <p className="text-xs text-muted-foreground">Password is used once for rsync and never stored.</p>
                                            </>
                                        ) : (
                                            <>
                                                <Input
                                                    type="password"
                                                    placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
                                                    value={formData.target_ssh_key}
                                                    onChange={e => setFormData({...formData, target_ssh_key: e.target.value})}
                                                />
                                                <p className="text-xs text-muted-foreground">Key is only used once for rsync and never stored.</p>
                                            </>
                                        )}
                                    </div>
                                    <div className="flex gap-2">
                                        <Button variant="outline" onClick={() => setStep(1)} className="flex-1">Back</Button>
                                            <Button
                                                onClick={() => setStep(3)}
                                                disabled={!hasTargetIp || !(hasAuth || usingConnectedTarget)}
                                                className="flex-1"
                                            >
                                                Next
                                            </Button>
                                    </div>
                                </>
                            )}

                            {step === 3 && (
                                <>
                                    <div className="p-4 border rounded bg-muted/20 space-y-2">
                                        <div className="flex justify-between">
                                            <span className="text-muted-foreground">Type:</span>
                                            <span className="font-medium">{formData.transfer_type}</span>
                                        </div>
                                        <div className="flex justify-between">
                                            <span className="text-muted-foreground">Target:</span>
                                            <span className="font-medium">{formData.target_server_ip}</span>
                                        </div>
                                        <div className="flex justify-between">
                                            <span className="text-muted-foreground">Est. Downtime:</span>
                                            <span className="font-medium text-emerald-600">~15 seconds</span>
                                        </div>
                                    </div>
                                    <div className="flex gap-2">
                                        <Button variant="outline" onClick={() => setStep(2)} className="flex-1">Back</Button>
                                        <Button onClick={handleStartTransfer} disabled={loading} className="flex-1">
                                            {loading ? <Loader2 className="mr-2 w-4 h-4 animate-spin" /> : <Server className="mr-2 w-4 h-4" />}
                                            Start Migration
                                        </Button>
                                    </div>
                                </>
                            )}
                        </CardContent>
                    </Card>

                    {/* Active Transfers */}
                    <div className="space-y-4">
                        <h2 className="text-xl font-bold">Connected Servers ({servers.length})</h2>
                        <Card>
                            <CardContent className="p-4 space-y-3">
                                <div className="flex items-center justify-between">
                                    <p className="text-sm text-muted-foreground">
                                        Select a connected server as transfer target.
                                    </p>
                                    <Button size="sm" variant="outline" onClick={loadServers} disabled={serversLoading}>
                                        {serversLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Refresh"}
                                    </Button>
                                </div>
                                {servers.length === 0 ? (
                                    <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                                        No connected servers found.
                                    </div>
                                ) : (
                                    <div className="space-y-2">
                                        {servers.map((server) => {
                                            const hostValue = (server.host || '').trim();
                                            const canUseAsTarget = isValidIp(hostValue);
                                            const statusTone =
                                                server.status === 'ONLINE'
                                                    ? 'text-emerald-600 bg-emerald-500/10'
                                                    : server.status === 'OFFLINE'
                                                        ? 'text-red-600 bg-red-500/10'
                                                        : 'text-amber-600 bg-amber-500/10';
                                            return (
                                                <div key={server.id} className="flex items-center justify-between rounded-lg border p-3">
                                                    <div className="min-w-0">
                                                        <div className="flex items-center gap-2">
                                                            <p className="font-medium truncate">{server.name}</p>
                                                            <span className={`text-[10px] px-2 py-0.5 rounded ${statusTone}`}>
                                                                {server.status}
                                                            </span>
                                                        </div>
                                                        <p className="text-xs text-muted-foreground truncate">
                                                            {server.host} {server.api_url ? `• ${server.api_url}` : ''}
                                                        </p>
                                                    </div>
                                                    <Button
                                                        size="sm"
                                                        variant="outline"
                                                        disabled={!canUseAsTarget}
                                                        onClick={() => {
                                                            setFormData((prev) => ({
                                                                ...prev,
                                                                target_server_ip: hostValue,
                                                                target_server_id: server.id,
                                                            }));
                                                            setStep(2);
                                                        }}
                                                    >
                                                        Use Target
                                                    </Button>
                                                </div>
                                            );
                                        })}
                                    </div>
                                )}
                            </CardContent>
                        </Card>

                        <h2 className="text-xl font-bold">Active Transfers</h2>
                        {transfers.filter(t => ['PREPARING', 'UPLOADING', 'RESTORING', 'DNS_CUTOVER', 'VERIFYING'].includes(t.status)).length === 0 && (
                            <div className="p-8 border rounded-xl text-center text-muted-foreground bg-muted/10">
                                No active transfers.
                            </div>
                        )}
                        {transfers.filter(t => ['PREPARING', 'UPLOADING', 'RESTORING', 'DNS_CUTOVER', 'VERIFYING'].includes(t.status)).map(t => (
                            <Card key={t.id}>
                                <CardContent className="p-4 space-y-3">
                                    <div className="flex justify-between items-center">
                                        <div className="font-medium">{t.transfer_type} → {t.target_server_ip}</div>
                                        <div className="text-xs font-mono bg-blue-100 text-blue-700 px-2 py-1 rounded">{t.status}</div>
                                    </div>
                                    <Progress value={t.progress_percent} />
                                    <p className="text-xs text-muted-foreground">{t.current_step}</p>
                                </CardContent>
                            </Card>
                        ))}

                        <h2 className="text-xl font-bold pt-4">History</h2>
                        <div className="space-y-2">
                            {transfers.filter(t => ['COMPLETED', 'FAILED', 'ROLLED_BACK'].includes(t.status)).map(t => (
                                <div key={t.id} className="flex items-center justify-between p-3 border rounded-lg bg-background">
                                    <div className="flex items-center gap-3">
                                        {t.status === 'COMPLETED' ? <CheckCircle2 className="text-emerald-500 w-5 h-5" /> :
                                         t.status === 'ROLLED_BACK' ? <RotateCcw className="text-orange-500 w-5 h-5" /> :
                                         <span className="w-2 h-2 rounded-full bg-red-500" />}
                                        <div>
                                            <div className="text-sm font-medium">{t.target_server_ip}</div>
                                            <div className="text-xs text-muted-foreground">{new Date(t.created_at).toLocaleDateString()}</div>
                                        </div>
                                    </div>
                                    {t.can_rollback && (
                                        <Button size="sm" variant="outline" onClick={() => api.post(`/transfers/${t.id}/rollback/`)}>
                                            Rollback
                                        </Button>
                                    )}
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </div>
            </RequiresTier>
        </DashboardShell>
    );
}
