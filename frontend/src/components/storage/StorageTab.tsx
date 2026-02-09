'use client';

import React, { useState, useEffect } from 'react';
import { servicesApi, Volume } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { HardDrive, Plus, Trash2, FolderOpen, FileText, ChevronRight, Download } from 'lucide-react';
import { toast } from '@/components/ui/use-toast';
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";

export function StorageTab({ serviceId }: { serviceId: string }) {
    const [volumes, setVolumes] = useState<Volume[]>([]);
    const [loading, setLoading] = useState(true);
    const [newName, setNewName] = useState('');
    const [newPath, setNewPath] = useState('/data');

    // File Browser State
    const [browsingVolume, setBrowsingVolume] = useState<Volume | null>(null);
    const [files, setFiles] = useState<any[]>([]);
    const [currentPath, setCurrentPath] = useState('');
    const [loadingFiles, setLoadingFiles] = useState(false);

    useEffect(() => {
        loadVolumes();
    }, [serviceId]);

    const loadVolumes = async () => {
        try {
            const data = await servicesApi.getVolumes(serviceId);
            setVolumes(data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handleAdd = async () => {
        if (!newName || !newPath) return;
        try {
            await servicesApi.createVolume(serviceId, {
                name: newName,
                mount_path: newPath,
                size_gb: 1
            });
            setNewName('');
            loadVolumes();
            toast({ title: "Volume created", description: "Redeploy to attach." });
        } catch (err) {
            toast({ title: "Failed to create volume", variant: "destructive" });
        }
    };

    const handleDelete = async (id: number) => {
        if (!confirm('Delete this volume? Data will be lost.')) return;
        try {
            await servicesApi.deleteVolume(serviceId, id);
            loadVolumes();
            toast({ title: "Volume deleted" });
        } catch (err) {
            toast({ title: "Failed to delete", variant: "destructive" });
        }
    };

    const openBrowser = async (volume: Volume) => {
        setBrowsingVolume(volume);
        setCurrentPath(volume.mount_path);
        loadFiles(volume, volume.mount_path);
    };

    const loadFiles = async (volume: Volume, path: string) => {
        setLoadingFiles(true);
        try {
            const data = await servicesApi.browseVolume(serviceId, volume.id, path);
            setFiles(data.files || []);
            setCurrentPath(path);
        } catch (err) {
            console.error(err);
            toast({ title: "Failed to list files", variant: "destructive" });
            setFiles([]);
        } finally {
            setLoadingFiles(false);
        }
    };

    const handleNavigate = (file: any) => {
        if (file.permissions.startsWith('d')) {
            const nextPath = currentPath.endsWith('/')
                ? `${currentPath}${file.name}`
                : `${currentPath}/${file.name}`;
            loadFiles(browsingVolume!, nextPath);
        }
    };

    const goUp = () => {
        if (!browsingVolume || currentPath === browsingVolume.mount_path) return;
        const parts = currentPath.split('/');
        parts.pop();
        const nextPath = parts.join('/') || '/';
        loadFiles(browsingVolume, nextPath);
    };

    if (loading) return <div className="p-4 text-center">Loading storage...</div>;

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center justify-between mb-6">
                    <div>
                        <h3 className="font-bold text-lg">Persistent Storage</h3>
                        <p className="text-sm text-muted-foreground">
                            Volumes persist data across deployments.
                        </p>
                    </div>
                    <HardDrive className="w-10 h-10 text-muted-foreground/20" />
                </div>

                {/* Add Form */}
                <div className="flex gap-4 mb-8 bg-muted/30 p-4 rounded-lg border border-border">
                    <Input
                        placeholder="Volume Name"
                        value={newName}
                        onChange={(e) => setNewName(e.target.value)}
                    />
                    <Input
                        placeholder="Mount Path (e.g. /data)"
                        className="font-mono"
                        value={newPath}
                        onChange={(e) => setNewPath(e.target.value)}
                    />
                    <Button onClick={handleAdd}>
                        <Plus className="w-4 h-4 mr-2" /> Create
                    </Button>
                </div>

                {/* List */}
                <div className="space-y-3">
                    {volumes.length === 0 ? (
                        <p className="text-center text-muted-foreground italic py-8">No volumes attached.</p>
                    ) : (
                        volumes.map((vol) => (
                            <div key={vol.id} className="flex items-center justify-between p-4 bg-card border border-border rounded-lg group hover:border-primary/50 transition-colors">
                                <div className="flex items-center gap-4">
                                    <div className="bg-orange-500/10 p-2 rounded-full">
                                        <HardDrive className="w-5 h-5 text-orange-500" />
                                    </div>
                                    <div>
                                        <div className="flex items-center gap-2">
                                            <span className="font-bold">{vol.name}</span>
                                            <span className="text-xs bg-muted px-2 py-0.5 rounded">{vol.size_gb} GB</span>
                                        </div>
                                        <code className="text-xs text-muted-foreground block mt-1 font-mono">
                                            {vol.mount_path}
                                        </code>
                                    </div>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Dialog>
                                        <DialogTrigger asChild>
                                            <Button variant="outline" size="sm" onClick={() => openBrowser(vol)}>
                                                <FolderOpen className="w-4 h-4 mr-2" /> Browse
                                            </Button>
                                        </DialogTrigger>
                                        <DialogContent className="max-w-3xl h-[600px] flex flex-col">
                                            <DialogHeader>
                                                <DialogTitle className="flex items-center gap-2">
                                                    <HardDrive className="w-5 h-5" /> {browsingVolume?.name} Browser
                                                </DialogTitle>
                                            </DialogHeader>

                                            {/* Browser Interface */}
                                            <div className="flex-1 bg-zinc-950 rounded-lg border border-border overflow-hidden flex flex-col">
                                                {/* Toolbar */}
                                                <div className="p-2 border-b border-border bg-muted/20 flex items-center gap-2">
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        onClick={goUp}
                                                        disabled={currentPath === browsingVolume?.mount_path}
                                                    >
                                                        ..
                                                    </Button>
                                                    <span className="font-mono text-sm px-2 truncate flex-1">{currentPath}</span>
                                                </div>

                                                {/* File List */}
                                                <div className="flex-1 overflow-auto p-2">
                                                    {loadingFiles ? (
                                                        <div className="p-8 text-center text-muted-foreground">Listing files...</div>
                                                    ) : (
                                                        <div className="space-y-1">
                                                            {files.map((file, i) => (
                                                                <div
                                                                    key={i}
                                                                    className="flex items-center gap-2 p-2 hover:bg-white/5 rounded cursor-pointer group"
                                                                    onClick={() => handleNavigate(file)}
                                                                >
                                                                    {file.permissions.startsWith('d') ? (
                                                                        <FolderOpen className="w-4 h-4 text-blue-400" />
                                                                    ) : (
                                                                        <FileText className="w-4 h-4 text-zinc-400" />
                                                                    )}
                                                                    <span className="flex-1 font-mono text-sm truncate">{file.name}</span>
                                                                    <span className="text-xs text-muted-foreground w-20 text-right">{file.size}</span>
                                                                    <span className="text-xs text-muted-foreground w-32 text-right hidden sm:block">{file.date}</span>
                                                                </div>
                                                            ))}
                                                            {files.length === 0 && (
                                                                <div className="p-8 text-center text-muted-foreground">Empty directory</div>
                                                            )}
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        </DialogContent>
                                    </Dialog>

                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="text-destructive hover:text-destructive hover:bg-destructive/10"
                                        onClick={() => handleDelete(vol.id)}
                                    >
                                        <Trash2 className="w-4 h-4" />
                                    </Button>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </Card>
        </div>
    );
}
