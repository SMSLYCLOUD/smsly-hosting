'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Loader2, Key, Copy, AlertCircle, CheckCircle2, RefreshCw, Shield, FileKey, ArrowRight } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { backupsApi } from '@/lib/api';

interface BackupListItem {
    id: string;
    file_path?: string;
    status: string;
    size_bytes?: number;
    created_at: string;
    backup_type?: string;
}

interface V2Header {
    magic: string;
    key_id: string;
    fingerprint: string;
}

export default function BackupKeysTab() {
    const { toast } = useToast();
    const confirm = useConfirm();
    const [activeScope, setActiveScope] = useState<'service' | 'server'>('service');
    const [backups, setBackups] = useState<BackupListItem[]>([]);
    const [loadingBackups, setLoadingBackups] = useState(false);
    const [selectedBackup, setSelectedBackup] = useState<BackupListItem | null>(null);
    const [header, setHeader] = useState<V2Header | null>(null);
    const [loadingHeader, setLoadingHeader] = useState(false);
    const [importKeyId, setImportKeyId] = useState('');
    const [importKeyMaterial, setImportKeyMaterial] = useState('');
    const [importLabel, setImportLabel] = useState('');
    const [importing, setImporting] = useState(false);

    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleImportJsonFile = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (ev) => {
            try {
                const result = ev.target?.result;
                if (typeof result !== 'string') {
                    toast({ title: "Read error", description: "Could not read the file.", variant: "destructive" });
                    return;
                }
                const json = JSON.parse(result);
                
                let populated = false;
                if (json.key_id) { setImportKeyId(json.key_id); populated = true; }
                if (json.key_material) { setImportKeyMaterial(json.key_material); populated = true; }
                if (json.label || json.source_label) { setImportLabel(json.label || json.source_label); populated = true; }
                
                if (!json.key_material && json.encryption?.key_id) {
                    setImportKeyId(json.encryption.key_id);
                    toast({ title: "Metadata loaded", description: "This is a backup header file. It loaded the key_id, but you still need to provide the key_material manually.", variant: "default" });
                } else if (!populated) {
                    toast({ title: "Invalid JSON format", description: "Could not find key_id or key_material in the JSON file.", variant: "destructive" });
                } else {
                    toast({ title: "JSON loaded", description: "Import fields populated from JSON file." });
                }
            } catch {
                toast({ title: "Invalid JSON", description: "Could not parse the selected file as JSON.", variant: "destructive" });
            }
        };
        reader.readAsText(file);
        e.target.value = '';
    };

    const loadBackups = useCallback(async () => {
        setLoadingBackups(true);
        try {
            const list = await backupsApi.list(activeScope);
            setBackups(list);
        } catch (err: any) {
            const msg = err?.response?.data?.error || 'Failed to load backups.';
            toast({ title: 'Error', description: msg, variant: 'destructive' });
        } finally {
            setLoadingBackups(false);
        }
    }, [activeScope, toast]);

    useEffect(() => {
        setSelectedBackup(null);
        setHeader(null);
        setImportKeyId('');
        setImportKeyMaterial('');
        setImportLabel('');
        loadBackups();
    }, [loadBackups]);

    const handleLoadHeader = async (backup: BackupListItem) => {
        setSelectedBackup(backup);
        setHeader(null);
        setLoadingHeader(true);
        try {
            const info = await backupsApi.getHeader(activeScope, backup.id);
            setHeader(info);
            setImportKeyId(info.key_id);
        } catch (err: any) {
            const status = err?.response?.status;
            const msg = err?.response?.data?.error || 'Failed to read backup header.';
            if (status === 400) {
                toast({
                    title: 'Not a V2 backup',
                    description: msg,
                    variant: 'destructive',
                });
            } else {
                toast({ title: 'Error', description: msg, variant: 'destructive' });
            }
        } finally {
            setLoadingHeader(false);
        }
    };

    const handleCopy = (text: string, label: string) => {
        navigator.clipboard.writeText(text);
        toast({ title: `${label} copied`, description: text });
    };

    const handleImport = async () => {
        if (!importKeyId.trim() || !importKeyMaterial.trim()) {
            toast({ title: 'Error', description: 'Both key_id and key_material are required.', variant: 'destructive' });
            return;
        }
        const confirmed = await confirm({
            title: 'Import encryption key?',
            message: 'This will register a foreign backup encryption key on this master. The key material is stored encrypted at rest and is only used to decrypt backups migrated from another master. This action is audit-logged.',
            confirmText: 'Import Key',
        });
        if (!confirmed) return;

        setImporting(true);
        try {
            const result = await backupsApi.importKey(activeScope, {
                key_id: importKeyId.trim(),
                key_material: importKeyMaterial.trim(),
                label: importLabel.trim() || undefined,
            });
            toast({
                title: result.created ? 'Key imported' : 'Key already registered',
                description: `key_id=${result.key_id} (fingerprint=${result.fingerprint})`,
                variant: 'success',
            });
            setImportKeyId('');
            setImportKeyMaterial('');
            setImportLabel('');
        } catch (err: any) {
            const status = err?.response?.status;
            const data = err?.response?.data;
            const msg = data?.error || 'Failed to import key.';
            if (status === 403) {
                toast({ title: 'Admin only', description: msg, variant: 'destructive' });
            } else if (status === 409) {
                toast({ title: 'Key ID collision', description: msg, variant: 'destructive' });
            } else if (status === 400) {
                toast({ title: 'Invalid input', description: msg, variant: 'destructive' });
            } else {
                toast({ title: 'Error', description: msg, variant: 'destructive' });
            }
        } finally {
            setImporting(false);
        }
    };

    return (
        <div className="space-y-6">
            <Card>
                <CardHeader>
                    <div className="flex items-center justify-between gap-4 flex-wrap">
                        <div>
                            <CardTitle className="flex items-center gap-2">
                                <Key className="h-5 w-5 text-amber-500" />
                                Cross-Master Backup Keys
                            </CardTitle>
                            <CardDescription>
                                Import <code className="text-xs">BACKUP_ENCRYPTION_KEY</code> from another master so
                                you can decrypt and restore backups migrated between masters.
                            </CardDescription>
                        </div>
                        <div className="flex items-center gap-2">
                            <select
                                className="h-9 px-3 border border-border rounded-md bg-background text-sm"
                                value={activeScope}
                                onChange={(e) => setActiveScope(e.target.value as 'service' | 'server')}
                            >
                                <option value="service">Service Backups</option>
                                <option value="server">Server Backups</option>
                            </select>
                            <Button variant="outline" size="sm" onClick={loadBackups} disabled={loadingBackups}>
                                {loadingBackups ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                            </Button>
                        </div>
                    </div>
                </CardHeader>
                <CardContent>
                    <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 mb-4">
                        <div className="flex items-start gap-3">
                            <Shield className="h-5 w-5 text-amber-500 shrink-0 mt-0.5" />
                            <div className="text-sm space-y-1">
                                <p className="font-medium text-amber-700 dark:text-amber-400">How cross-master restore works</p>
                                <ol className="list-decimal list-inside space-y-0.5 text-muted-foreground">
                                    <li>On the <strong>source</strong> master, click a backup to read its V2 header — copy the <code className="text-xs">key_id</code>.</li>
                                    <li>SSH into the source and run <code className="text-xs">grep BACKUP_ENCRYPTION_KEY /opt/smsly/.env</code> — copy the value.</li>
                                    <li>On the <strong>target</strong> master (this one), paste both into the form below and click <strong>Import Key</strong>.</li>
                                    <li>The target now decrypts the migrated backup automatically. The action is audit-logged.</li>
                                </ol>
                            </div>
                        </div>
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        {/* Left: Backups list */}
                        <div className="space-y-3">
                            <Label className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                                Source backup (click to read V2 header)
                            </Label>
                            <div className="border border-border rounded-lg max-h-96 overflow-y-auto">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Date</TableHead>
                                            <TableHead>Type</TableHead>
                                            <TableHead></TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {backups.map(b => (
                                            <TableRow
                                                key={b.id}
                                                className={selectedBackup?.id === b.id ? 'bg-primary/5' : 'cursor-pointer hover:bg-muted/30'}
                                                onClick={() => handleLoadHeader(b)}
                                            >
                                                <TableCell className="text-xs">{new Date(b.created_at).toLocaleString()}</TableCell>
                                                <TableCell>
                                                    <span className="text-[10px] font-mono bg-muted px-1.5 py-0.5 rounded">
                                                        {b.backup_type || 'MANUAL'}
                                                    </span>
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    {loadingHeader && selectedBackup?.id === b.id ? (
                                                        <Loader2 className="h-3 w-3 animate-spin inline" />
                                                    ) : (
                                                        <FileKey className="h-3 w-3 text-muted-foreground inline" />
                                                    )}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                        {!loadingBackups && backups.length === 0 && (
                                            <TableRow>
                                                <TableCell colSpan={3} className="text-center py-6 text-sm text-muted-foreground">
                                                    No {activeScope} backups found.
                                                </TableCell>
                                            </TableRow>
                                        )}
                                    </TableBody>
                                </Table>
                            </div>
                        </div>

                        {/* Right: V2 header + import form */}
                        <div className="space-y-4">
                            <div className="space-y-2">
                                <Label className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                                    V2 header
                                </Label>
                                <div className="border border-border rounded-lg p-3 bg-muted/30 min-h-[5rem]">
                                    {!selectedBackup ? (
                                        <p className="text-sm text-muted-foreground">Select a backup to read its V2 header.</p>
                                    ) : loadingHeader ? (
                                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                            <Loader2 className="h-4 w-4 animate-spin" /> Reading header...
                                        </div>
                                    ) : header ? (
                                        <div className="space-y-2 text-xs font-mono">
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="text-muted-foreground">magic</span>
                                                <span>{header.magic}</span>
                                            </div>
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="text-muted-foreground">key_id</span>
                                                <div className="flex items-center gap-2">
                                                    <span className="font-bold">{header.key_id}</span>
                                                    <Button size="icon" variant="ghost" className="h-6 w-6" onClick={() => handleCopy(header.key_id, 'key_id')}>
                                                        <Copy className="h-3 w-3" />
                                                    </Button>
                                                </div>
                                            </div>
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="text-muted-foreground">fingerprint</span>
                                                <div className="flex items-center gap-2">
                                                    <span>{header.fingerprint}</span>
                                                    <Button size="icon" variant="ghost" className="h-6 w-6" onClick={() => handleCopy(header.fingerprint, 'fingerprint')}>
                                                        <Copy className="h-3 w-3" />
                                                    </Button>
                                                </div>
                                            </div>
                                        </div>
                                    ) : (
                                        <p className="text-sm text-red-500">Header read failed.</p>
                                    )}
                                </div>
                            </div>

                            <div className="space-y-3 border-t border-border pt-4">
                                <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">
                                    <ArrowRight className="h-3 w-3" /> Import on this master
                                </div>
                                <div className="flex justify-end">
                                    <input
                                        type="file"
                                        ref={fileInputRef}
                                        accept=".json"
                                        className="hidden"
                                        onChange={handleImportJsonFile}
                                    />
                                    <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()}>
                                        Load from JSON
                                    </Button>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="bk-key-id" className="text-xs">key_id (from source V2 header)</Label>
                                    <Input
                                        id="bk-key-id"
                                        value={importKeyId}
                                        onChange={(e) => setImportKeyId(e.target.value)}
                                        placeholder="a1b2c3d4"
                                        maxLength={8}
                                        className="font-mono"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="bk-key-material" className="text-xs">key_material (Fernet BACKUP_ENCRYPTION_KEY from source .env)</Label>
                                    <Input
                                        id="bk-key-material"
                                        type="password"
                                        value={importKeyMaterial}
                                        onChange={(e) => setImportKeyMaterial(e.target.value)}
                                        placeholder="<base64-fernet-key>"
                                        className="font-mono"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="bk-label" className="text-xs">Label (optional, audit trail)</Label>
                                    <Input
                                        id="bk-label"
                                        value={importLabel}
                                        onChange={(e) => setImportLabel(e.target.value)}
                                        placeholder="from master-A 2026-06-14"
                                        maxLength={100}
                                    />
                                </div>
                                <Button onClick={handleImport} disabled={importing} className="w-full">
                                    {importing ? (
                                        <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Importing...</>
                                    ) : (
                                        <><Key className="mr-2 h-4 w-4" /> Import Key</>
                                    )}
                                </Button>
                            </div>
                        </div>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
