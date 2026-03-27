'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { servicesApi } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { FolderOpen, FileText, ChevronRight, Save, Loader2, ArrowLeft, Trash2, Download, Plus, RefreshCw } from 'lucide-react';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { toast } from '@/components/ui/use-toast';
import dynamic from 'next/dynamic';

const Editor = dynamic(() => import('@monaco-editor/react'), { ssr: false });

export function FilesTab({ serviceId }: { serviceId: string }) {
    const [currentPath, setCurrentPath] = useState('/app');
    const [files, setFiles] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [selectedFile, setSelectedFile] = useState<string | null>(null);
    const [fileContent, setFileContent] = useState<string>('');
    const [originalContent, setOriginalContent] = useState<string>('');
    const [loadingFile, setLoadingFile] = useState(false);
    const [saving, setSaving] = useState(false);
    const confirm = useConfirm();

    const loadDirectory = useCallback(async (path: string) => {
        try {
            setLoading(true);
            const data = await servicesApi.browseFiles(serviceId, path);
            setFiles(data.files || []);
            setCurrentPath(path);
            setSelectedFile(null);
        } catch (err: any) {
            console.error(err);
            toast({ title: 'Failed to load directory', description: err.response?.data?.error || 'Ensure the container is running.', variant: 'destructive' });
            setFiles([]);
        } finally {
            setLoading(false);
        }
    }, [serviceId]);

    useEffect(() => {
        void loadDirectory('/app');
    }, [loadDirectory]);

    const handleNavigate = (file: any) => {
        const nextPath = currentPath.endsWith('/')
            ? `${currentPath}${file.name}`
            : `${currentPath}/${file.name}`;

        if (file.permissions.startsWith('d')) {
            loadDirectory(nextPath);
        } else {
            loadFile(nextPath);
        }
    };

    const goUp = () => {
        if (currentPath === '/') return;
        const parts = currentPath.split('/').filter(Boolean);
        parts.pop();
        const nextPath = '/' + parts.join('/');
        loadDirectory(nextPath);
    };

    const loadFile = async (path: string) => {
        try {
            setLoadingFile(true);
            const data = await servicesApi.readFile(serviceId, path);
            setFileContent(data.content);
            setOriginalContent(data.content);
            setSelectedFile(path);
        } catch (err: any) {
            console.error(err);
            toast({ title: 'Failed to read file', description: err.response?.data?.error || 'Ensure it is a text file.', variant: 'destructive' });
        } finally {
            setLoadingFile(false);
        }
    };

    const handleSave = async () => {
        if (!selectedFile) return;
        try {
            setSaving(true);
            await servicesApi.writeFile(serviceId, selectedFile, fileContent);
            setOriginalContent(fileContent);
            toast({ title: 'File saved successfully' });
        } catch (err: any) {
            console.error(err);
            toast({ title: 'Failed to save file', description: err.response?.data?.error || 'An error occurred', variant: 'destructive' });
        } finally {
            setSaving(false);
        }
    };

    const handleFileDelete = async (file: any) => {
        const path = currentPath.endsWith('/') ? `${currentPath}${file.name}` : `${currentPath}/${file.name}`;
        if (!await confirm({ 
            title: `Delete ${file.permissions.startsWith('d') ? 'folder' : 'file'}?`, 
            message: `Are you sure you want to delete ${file.name}?`, 
            variant: 'destructive' 
        })) return;
        
        try {
            await servicesApi.deleteFile(serviceId, path);
            toast({ title: "Deleted successfully" });
            loadDirectory(currentPath);
        } catch (err: any) {
            toast({ title: "Failed to delete", description: err.response?.data?.error || 'Access denied', variant: "destructive" });
        }
    };

    const handleFileDownload = (file: any) => {
        const path = currentPath.endsWith('/') ? `${currentPath}${file.name}` : `${currentPath}/${file.name}`;
        servicesApi.downloadFile(serviceId, path);
    };

    const handleMkdir = async () => {
        const name = window.prompt("Enter folder name:");
        if (!name) return;
        const path = currentPath.endsWith('/') ? `${currentPath}${name}` : `${currentPath}/${name}`;
        try {
            await servicesApi.createFolder(serviceId, path);
            toast({ title: "Folder created" });
            loadDirectory(currentPath);
        } catch (err: any) {
            toast({ title: "Failed to create folder", description: err.response?.data?.error || 'Access denied', variant: "destructive" });
        }
    };

    const isDirty = fileContent !== originalContent;

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            <Card className="border-border shadow-md overflow-hidden flex flex-col md:flex-row h-[700px]">

                {/* Left Panel: File Browser */}
                <div className={`flex flex-col border-r border-border bg-card ${selectedFile ? 'hidden md:flex w-1/3' : 'w-full md:w-1/3'}`}>
                    <div className="p-3 border-b border-border bg-muted/20 flex items-center justify-between gap-1 overflow-hidden">
                        <div className="flex items-center gap-1 overflow-hidden">
                            <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={goUp} disabled={currentPath === '/'}>
                                <ArrowLeft className="w-4 h-4" />
                            </Button>
                            <span className="font-mono text-sm truncate opacity-70" title={currentPath}>{currentPath}</span>
                        </div>
                        <div className="flex items-center gap-1">
                            <Button variant="ghost" size="icon" className="h-8 w-8 text-blue-400" onClick={handleMkdir}>
                                <Plus className="w-4 h-4" />
                            </Button>
                            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => loadDirectory(currentPath)}>
                                <RefreshCw className="w-4 h-4" />
                            </Button>
                        </div>
                    </div>

                    <div className="flex-1 overflow-y-auto p-2">
                        {loading ? (
                            <div className="p-8 flex justify-center text-muted-foreground">
                                <Loader2 className="w-6 h-6 animate-spin" />
                            </div>
                        ) : files.length === 0 ? (
                            <div className="p-8 text-center text-muted-foreground text-sm flex flex-col items-center gap-2">
                                <FolderOpen className="w-8 h-8 opacity-20" />
                                <span>Empty directory</span>
                                {currentPath === '/app' && (
                                    <Button variant="link" size="sm" onClick={() => loadDirectory('/')}>
                                        Try root directory (/)
                                    </Button>
                                )}
                            </div>
                        ) : (
                            <div className="space-y-1">
                                {files.filter(f => f.name !== '.' && f.name !== '..').map((file, i) => {
                                    const isDir = file.permissions.startsWith('d');
                                    const path = currentPath.endsWith('/') ? `${currentPath}${file.name}` : `${currentPath}/${file.name}`;
                                    const isSelected = selectedFile === path;

                                    return (
                                        <div
                                            key={i}
                                            className={`flex items-center justify-between p-2 rounded cursor-pointer group text-sm ${isSelected ? 'bg-primary/20 text-primary' : 'hover:bg-muted'}`}
                                            onClick={() => handleNavigate(file)}
                                        >
                                            <div className="flex items-center gap-2 overflow-hidden">
                                                {isDir ? <FolderOpen className="w-4 h-4 text-blue-400 shrink-0" /> : <FileText className="w-4 h-4 text-zinc-400 shrink-0" />}
                                                <span className="font-mono truncate">{file.name}</span>
                                            </div>
                                            <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                                                {!isDir && (
                                                    <Button 
                                                        variant="ghost" 
                                                        size="icon" 
                                                        className="h-7 w-7 text-blue-400"
                                                        onClick={(e) => { e.stopPropagation(); handleFileDownload(file); }}
                                                    >
                                                        <Download className="w-3 h-3" />
                                                    </Button>
                                                )}
                                                <Button 
                                                    variant="ghost" 
                                                    size="icon" 
                                                    className="h-7 w-7 text-destructive"
                                                    onClick={(e) => { e.stopPropagation(); handleFileDelete(file); }}
                                                >
                                                    <Trash2 className="w-3 h-3" />
                                                </Button>
                                                {isDir && <ChevronRight className="w-4 h-4 text-muted-foreground" />}
                                            </div>
                                        </div>
                                    )
                                })}
                            </div>
                        )}
                    </div>
                </div>

                {/* Right Panel: Editor */}
                <div className={`flex flex-col bg-zinc-950 ${selectedFile ? 'w-full md:w-2/3' : 'hidden md:flex w-2/3 items-center justify-center'}`}>
                    {!selectedFile ? (
                        <div className="text-zinc-500 flex flex-col items-center gap-4">
                            <FileText className="w-12 h-12 opacity-20" />
                            <p>Select a file to edit</p>
                        </div>
                    ) : (
                        <>
                            <div className="p-3 border-b border-border bg-muted/20 flex items-center justify-between gap-2">
                                <div className="flex items-center gap-2 truncate">
                                    <Button variant="ghost" size="icon" className="h-8 w-8 md:hidden" onClick={() => setSelectedFile(null)}>
                                        <ArrowLeft className="w-4 h-4" />
                                    </Button>
                                    <FileText className="w-4 h-4 text-muted-foreground" />
                                    <span className="font-mono text-sm truncate" title={selectedFile}>{selectedFile}</span>
                                    {isDirty && <span className="w-2 h-2 rounded-full bg-yellow-500"></span>}
                                </div>
                                <Button size="sm" onClick={handleSave} disabled={saving || !isDirty}>
                                    {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
                                    Save
                                </Button>
                            </div>
                            <div className="flex-1 relative">
                                {loadingFile ? (
                                    <div className="absolute inset-0 flex items-center justify-center bg-zinc-950/80 z-10">
                                        <Loader2 className="w-8 h-8 animate-spin text-primary" />
                                    </div>
                                ) : null}
                                <Editor
                                    height="100%"
                                    theme="vs-dark"
                                    value={fileContent}
                                    onChange={(val) => setFileContent(val || '')}
                                    options={{
                                        minimap: { enabled: false },
                                        fontSize: 14,
                                        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                                        wordWrap: "on",
                                        padding: { top: 16 }
                                    }}
                                />
                            </div>
                        </>
                    )}
                </div>

            </Card>
        </div>
    );
}
