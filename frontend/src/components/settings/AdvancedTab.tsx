'use client';

import React, { useState } from 'react';
import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';
const Editor = dynamic(() => import('@monaco-editor/react'), { ssr: false, loading: () => <Skeleton className="h-[400px] w-full" /> });
import { Service, servicesApi } from '@/lib/api';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Save, AlertTriangle, Check, Loader2, Search, FileText, Code2 } from 'lucide-react';

export function AdvancedTab({ service }: { service: Service }) {
    const confirm = useConfirm();
    // The effective registry comes from the backend (ScopedRegistry
    // chain → platform config). Never fabricate a hardcoded registry
    // domain here: the old fallback invented 'registry.Trulay.co/<name>'
    // for every service and then PERSISTED that bogus image on save.
    const effectiveRegistry = service.effective_registry || '';
    const defaultImage = effectiveRegistry
        ? `${effectiveRegistry}/${service.name}`
        : (service.docker_image || '');
    const [config, setConfig] = useState<{ docker_image: string; start_command: string; restart_policy: string }>({
        docker_image: service.docker_image || defaultImage,
        start_command: service.start_command || '',
        restart_policy: service.restart_policy || 'unless-stopped',
    });
    const [scanDepth, setScanDepth] = useState<'shallow' | 'standard' | 'deep'>(service.env_scan_depth || 'shallow');
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [error, setError] = useState('');

    const handleSave = async () => {
        setSaving(true);
        setError('');
        setSaved(false);
        try {
            const payload: Record<string, string> = {
                start_command: config.start_command,
                restart_policy: config.restart_policy,
            };
            // Only send docker_image when the user actually changed it —
            // otherwise the auto-filled default would get persisted as if
            // it were an explicit override.
            if (config.docker_image !== (service.docker_image || defaultImage)) {
                payload.docker_image = config.docker_image;
            }
            await servicesApi.update(service.id, payload as any);
            setSaved(true);
            setTimeout(() => setSaved(false), 3000);
        } catch (err: any) {
            setError(err?.response?.data?.detail || 'Failed to save configuration');
        } finally {
            setSaving(false);
        }
    };

    const handleDelete = async () => {
        if (!await confirm({ 
            title: 'Delete Service?', 
            message: `Are you sure you want to delete "${service.name}"? This action is irreversible.`,
            variant: 'destructive',
            confirmText: 'Delete Forever'
        })) return;

        setSaving(true);
        try {
            await servicesApi.delete(service.id);
            window.location.href = '/dashboard';
        } catch (err: any) {
            setError(err?.response?.data?.detail || 'Failed to delete service');
            setSaving(false);
        }
    };

    return (
        <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4">

            {/* Raw JSON Config */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex justify-between items-center mb-4">
                    <div>
                        <h3 className="font-bold text-lg">Raw Container Configuration</h3>
                        <p className="text-sm text-muted-foreground">Directly override Docker specifications.</p>
                    </div>
                    <Button variant="outline" className="gap-2">
                        <Save size={16} /> Apply
                    </Button>
                </div>
                <div className="h-96 border border-border rounded-lg overflow-hidden">
                    <Editor
                        height="100%"
                        defaultLanguage="json"
                        defaultValue={`{
  "config": {
    "containers": [
      {
        "name": "${service.name}",
        "image": "${config.docker_image}:latest",
        "resources": {
          "limits": {
            "cpu": "${service.cpu_cores}",
            "memory": "${service.memory_mb}Mi"
          }
        },
        "restartPolicy": "${config.restart_policy}",
        "securityContext": {
          "allowPrivilegeEscalation": false
        }
      }
    ]
  }
}`}
                        theme="vs-dark"
                        options={{ minimap: { enabled: false }, fontSize: 13 }}
                    />
                </div>
            </Card>

            {/* Container Settings Form */}
            <Card className="p-6 border-border shadow-md">
                <h3 className="font-bold text-lg mb-6">Container Runtime</h3>

                {error && (
                    <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 mb-6 text-red-500 text-sm">
                        {error}
                    </div>
                )}
                {saved && (
                    <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-3 mb-6 text-emerald-500 text-sm flex items-center gap-2">
                        <Check size={16} /> Configuration saved successfully
                    </div>
                )}

                <div className="grid grid-cols-2 gap-6">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Docker Image</label>
                        <Input
                            value={config.docker_image}
                            onChange={(e) => setConfig(prev => ({ ...prev, docker_image: e.target.value }))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Image Tag</label>
                        <Input defaultValue="latest" />
                    </div>
                    <div className="col-span-2 space-y-2">
                        <label className="text-sm font-medium">Command Override</label>
                        <Input
                            placeholder="/bin/sh -c '...'"
                            value={config.start_command}
                            onChange={(e) => setConfig(prev => ({ ...prev, start_command: e.target.value }))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Restart Policy</label>
                        <select
                            value={config.restart_policy}
                            onChange={(e) => setConfig(prev => ({ ...prev, restart_policy: e.target.value }))}
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                        >
                            <option value="always">Always</option>
                            <option value="unless-stopped">Unless Stopped</option>
                            <option value="on-failure">On Failure</option>
                            <option value="no">Never</option>
                        </select>
                    </div>
                </div>
                <div className="mt-6 flex justify-end">
                    <Button onClick={handleSave} disabled={saving} className="gap-2">
                        {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                        {saving ? 'Saving...' : saved ? 'Saved!' : 'Save Configuration'}
                    </Button>
                </div>
            </Card>

            {/* Environment Scan Depth */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex justify-between items-center mb-4">
                    <div>
                        <h3 className="font-bold text-lg flex items-center gap-2">
                            <Search size={20} /> Environment Variable Scan Depth
                        </h3>
                        <p className="text-sm text-muted-foreground">Control how deeply the AI scans your repository for environment variables during deployment analysis.</p>
                    </div>
                </div>
                <div className="space-y-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Scan Depth</label>
                        <Select value={scanDepth} onValueChange={(value) => setScanDepth(value as 'shallow' | 'standard' | 'deep')}>
                            <SelectTrigger className="w-[250px]">
                                <SelectValue placeholder="Select scan depth" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="shallow">
                                    <div className="flex items-center gap-2">
                                        <FileText className="w-4 h-4 text-muted-foreground" />
                                        <span>Shallow</span>
                                    </div>
                                </SelectItem>
                                <SelectItem value="standard">
                                    <div className="flex items-center gap-2">
                                        <Code2 className="w-4 h-4 text-muted-foreground" />
                                        <span>Standard</span>
                                    </div>
                                </SelectItem>
                                <SelectItem value="deep">
                                    <div className="flex items-center gap-2">
                                        <Search className="w-4 h-4 text-muted-foreground" />
                                        <span>Deep</span>
                                    </div>
                                </SelectItem>
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">
                            {scanDepth === 'shallow' && 'Only scans .env files. Fastest but may miss variables.'}
                            {scanDepth === 'standard' && 'Scans .env files + config files + package manifests. Good balance.'}
                            {scanDepth === 'deep' && 'Full codebase scan including all source files. Most thorough but slowest.'}
                        </p>
                    </div>
                    <Button 
                        onClick={async () => {
                            setSaving(true);
                            setError('');
                            setSaved(false);
                            try {
                                await servicesApi.update(service.id, { env_scan_depth: scanDepth });
                                setSaved(true);
                                setTimeout(() => setSaved(false), 3000);
                            } catch (err: any) {
                                setError(err?.response?.data?.detail || 'Failed to save scan depth');
                            } finally {
                                setSaving(false);
                            }
                        }} 
                        disabled={saving} 
                        className="gap-2"
                    >
                        {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                        {saving ? 'Saving...' : saved ? 'Saved!' : 'Save Scan Depth'}
                    </Button>
                    {error && (
                        <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-red-500 text-sm">
                            {error}
                        </div>
                    )}
                    {saved && (
                        <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-3 text-emerald-500 text-sm flex items-center gap-2">
                            <Check size={16} /> Scan depth saved successfully
                        </div>
                    )}
                </div>
            </Card>

            {/* Danger Zone */}
            <Card className="p-6 border-red-200/50 bg-red-50/10 dark:bg-red-900/10">
                <h3 className="font-bold text-lg text-destructive mb-2 flex items-center gap-2">
                    <AlertTriangle size={20} /> Danger Zone
                </h3>
                <p className="text-sm text-muted-foreground mb-4">
                    Irreversible actions that affect your service availability.
                </p>
                <div className="flex gap-4">
                    <Button variant="outline" className="border-destructive text-destructive hover:bg-destructive/10">Force Redeploy</Button>
                    <Button variant="destructive" className="bg-red-600 hover:bg-red-700" onClick={handleDelete} disabled={saving}>
                        {saving ? <Loader2 size={16} className="animate-spin mr-2" /> : null}
                        Delete Service
                    </Button>
                </div>
            </Card>
        </div>
    );
}
