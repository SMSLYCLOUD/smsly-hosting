'use client';

import { useState, Suspense, useCallback } from 'react';
import { servicesApi } from '@/lib/api';
import { useRouter, useSearchParams } from 'next/navigation';
import { Navbar } from '@/components/layout/Navbar';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { RepoAnalyzer } from '@/components/ai/RepoAnalyzer';
import {
    Github, Upload, Container, LayoutTemplate, Loader2,
    Rocket, Settings2, Lock, ChevronDown, ChevronUp, Sparkles
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

interface AnalysisData {
    detected_framework: string;
    confidence: number;
    suggested_port: number;
    build_command: string;
    start_command: string;
    has_dockerfile: boolean;
    suggested_env_vars: string[];
    resource_recommendation: {
        cpu: string;
        memory: string;
        recommendation: string;
    };
    repoUrl: string;
}

function NewServiceForm() {
    const router = useRouter();
    const searchParams = useSearchParams();

    // Mode: 'smart' (AI-driven) or 'manual' (tabs)
    const [mode, setMode] = useState<'smart' | 'manual'>('smart');
    const [activeTab, setActiveTab] = useState('git');

    // AI Analysis State
    const [analysisData, setAnalysisData] = useState<AnalysisData | null>(null);

    // Form State - auto-populated from AI or manual
    const [name, setName] = useState(searchParams.get('name') || '');
    const [port, setPort] = useState(searchParams.get('port') || '8000');
    const [domain, setDomain] = useState('');
    const [repoUrl, setRepoUrl] = useState(searchParams.get('repo') || '');
    const [branch, setBranch] = useState('main');
    const [buildCommand, setBuildCommand] = useState('');
    const [startCommand, setStartCommand] = useState('');

    // Docker/Upload/Template State
    const [dockerImage, setDockerImage] = useState('');
    const [file, setFile] = useState<File | null>(null);
    const [selectedTemplate, setSelectedTemplate] = useState('');

    // Advanced options
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [envVars, setEnvVars] = useState<{ key: string, value: string }[]>([]);

    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    // Handle AI analysis completion - auto-fill form
    const handleAnalysisComplete = useCallback((result: AnalysisData) => {
        setAnalysisData(result);
        setRepoUrl(result.repoUrl);
        setPort(result.suggested_port.toString());
        setBuildCommand(result.build_command);
        setStartCommand(result.start_command);

        // Auto-generate name from repo URL
        const repoName = result.repoUrl.split('/').pop()?.replace('.git', '') || '';
        if (!name) setName(repoName);

        // Pre-populate env var placeholders
        if (result.suggested_env_vars.length > 0) {
            setEnvVars(result.suggested_env_vars.map(key => ({ key, value: '' })));
        }
    }, [name]);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setError('');

        try {
            let payload: any = {
                name,
                internal_port: parseInt(port),
                public_domain: domain || undefined,
                deploy_type: mode === 'smart' || activeTab === 'git' ? 'GIT' : activeTab.toUpperCase(),
                build_command: buildCommand || undefined,
                start_command: startCommand || undefined,
            };

            if (mode === 'smart' || activeTab === 'git') {
                payload = { ...payload, repository_url: repoUrl, branch };
            } else if (activeTab === 'docker') {
                payload = { ...payload, docker_image: dockerImage };
            } else if (activeTab === 'template') {
                payload = { ...payload, template_id: selectedTemplate };
            } else if (activeTab === 'upload') {
                if (!file) throw new Error("Please select a file to upload");
                const formData = new FormData();
                Object.entries(payload).forEach(([k, v]) => v && formData.append(k, String(v)));
                formData.append('source_file', file);
                payload = formData;
            }

            // Create Service
            const service = await servicesApi.create(payload);

            // Add environment variables if any
            for (const env of envVars) {
                if (env.key && env.value) {
                    await servicesApi.addEnvVar(service.id, env.key, env.value, false);
                }
            }

            // Trigger Initial Deployment
            if (mode === 'smart' || activeTab === 'git' || activeTab === 'docker') {
                await servicesApi.deploy(service.id, activeTab === 'git' || mode === 'smart' ? 'HEAD' : 'latest');
            }

            router.push(`/services/${service.id}`);
        } catch (err: any) {
            console.error(err);
            setError(err.response?.data?.detail || err.message || 'Failed to deploy service');
        } finally {
            setLoading(false);
        }
    };

    return (
        <main className="min-h-screen flex flex-col bg-background">
            <Navbar />
            <div className="flex-1 container max-w-4xl mx-auto p-4 md:p-8">
                {/* Header */}
                <div className="flex items-center justify-between mb-8">
                    <div>
                        <h1 className="text-3xl font-bold flex items-center gap-3">
                            <Sparkles className="w-8 h-8 text-primary" />
                            Deploy New Service
                        </h1>
                        <p className="text-muted-foreground mt-1">
                            Paste a repo URL and let AI configure everything for you
                        </p>
                    </div>
                    <div className="flex gap-2">
                        <Button
                            variant={mode === 'smart' ? 'default' : 'outline'}
                            size="sm"
                            onClick={() => setMode('smart')}
                        >
                            <Sparkles className="w-4 h-4 mr-1" /> AI Mode
                        </Button>
                        <Button
                            variant={mode === 'manual' ? 'default' : 'outline'}
                            size="sm"
                            onClick={() => setMode('manual')}
                        >
                            <Settings2 className="w-4 h-4 mr-1" /> Manual
                        </Button>
                    </div>
                </div>

                <AnimatePresence mode="wait">
                    {mode === 'smart' ? (
                        /* ==================== AI-POWERED SMART MODE ==================== */
                        <motion.div
                            key="smart"
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -20 }}
                            className="space-y-6"
                        >
                            {/* AI Repo Analyzer */}
                            <RepoAnalyzer
                                onAnalysisComplete={handleAnalysisComplete}
                                initialUrl={searchParams.get('repo') || ''}
                            />

                            {/* Deploy Form - Shows after analysis */}
                            {analysisData && (
                                <motion.div
                                    initial={{ opacity: 0, y: 20 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: 0.2 }}
                                >
                                    <Card className="p-6">
                                        <form onSubmit={handleSubmit} className="space-y-6">
                                            {error && (
                                                <div className="p-3 bg-destructive/10 border border-destructive/20 text-destructive rounded-md text-sm">
                                                    {error}
                                                </div>
                                            )}

                                            {/* Basic Info */}
                                            <div className="grid md:grid-cols-2 gap-4">
                                                <div className="space-y-2">
                                                    <label className="text-sm font-medium">Service Name</label>
                                                    <input
                                                        type="text"
                                                        required
                                                        placeholder="my-app"
                                                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:ring-2 focus:ring-ring"
                                                        value={name}
                                                        onChange={(e) => setName(e.target.value)}
                                                    />
                                                </div>
                                                <div className="space-y-2">
                                                    <label className="text-sm font-medium flex items-center gap-2">
                                                        Port
                                                        <Badge variant="secondary" className="text-xs font-normal">Auto-detected</Badge>
                                                    </label>
                                                    <input
                                                        type="number"
                                                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:ring-2 focus:ring-ring"
                                                        value={port}
                                                        onChange={(e) => setPort(e.target.value)}
                                                    />
                                                </div>
                                            </div>

                                            {/* Branch */}
                                            <div className="space-y-2">
                                                <label className="text-sm font-medium">Branch</label>
                                                <input
                                                    type="text"
                                                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:ring-2 focus:ring-ring"
                                                    value={branch}
                                                    onChange={(e) => setBranch(e.target.value)}
                                                />
                                            </div>

                                            {/* Advanced Options */}
                                            <div>
                                                <button
                                                    type="button"
                                                    className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
                                                    onClick={() => setShowAdvanced(!showAdvanced)}
                                                >
                                                    {showAdvanced ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                                                    Advanced Options
                                                </button>

                                                <AnimatePresence>
                                                    {showAdvanced && (
                                                        <motion.div
                                                            initial={{ height: 0, opacity: 0 }}
                                                            animate={{ height: 'auto', opacity: 1 }}
                                                            exit={{ height: 0, opacity: 0 }}
                                                            className="overflow-hidden"
                                                        >
                                                            <div className="pt-4 space-y-4">
                                                                <div className="grid md:grid-cols-2 gap-4">
                                                                    <div className="space-y-2">
                                                                        <label className="text-sm font-medium">Build Command</label>
                                                                        <input
                                                                            type="text"
                                                                            placeholder="npm run build"
                                                                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-ring"
                                                                            value={buildCommand}
                                                                            onChange={(e) => setBuildCommand(e.target.value)}
                                                                        />
                                                                    </div>
                                                                    <div className="space-y-2">
                                                                        <label className="text-sm font-medium">Start Command</label>
                                                                        <input
                                                                            type="text"
                                                                            placeholder="npm start"
                                                                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-ring"
                                                                            value={startCommand}
                                                                            onChange={(e) => setStartCommand(e.target.value)}
                                                                        />
                                                                    </div>
                                                                </div>

                                                                <div className="space-y-2">
                                                                    <label className="text-sm font-medium">Public Domain (Optional)</label>
                                                                    <input
                                                                        type="text"
                                                                        placeholder="app.example.com"
                                                                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:ring-2 focus:ring-ring"
                                                                        value={domain}
                                                                        onChange={(e) => setDomain(e.target.value)}
                                                                    />
                                                                </div>

                                                                {/* Environment Variables */}
                                                                {envVars.length > 0 && (
                                                                    <div className="space-y-3">
                                                                        <label className="text-sm font-medium flex items-center gap-2">
                                                                            <Lock className="w-4 h-4" /> Environment Variables
                                                                        </label>
                                                                        {envVars.map((env, idx) => (
                                                                            <div key={idx} className="flex gap-2">
                                                                                <input
                                                                                    type="text"
                                                                                    placeholder="KEY"
                                                                                    className="flex h-10 w-1/3 rounded-md border border-input bg-muted px-3 py-2 text-sm font-mono"
                                                                                    value={env.key}
                                                                                    onChange={(e) => {
                                                                                        const updated = [...envVars];
                                                                                        updated[idx].key = e.target.value;
                                                                                        setEnvVars(updated);
                                                                                    }}
                                                                                />
                                                                                <input
                                                                                    type="password"
                                                                                    placeholder="Value (secret)"
                                                                                    className="flex h-10 flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm"
                                                                                    value={env.value}
                                                                                    onChange={(e) => {
                                                                                        const updated = [...envVars];
                                                                                        updated[idx].value = e.target.value;
                                                                                        setEnvVars(updated);
                                                                                    }}
                                                                                />
                                                                            </div>
                                                                        ))}
                                                                    </div>
                                                                )}
                                                            </div>
                                                        </motion.div>
                                                    )}
                                                </AnimatePresence>
                                            </div>

                                            {/* Deploy Button */}
                                            <Button
                                                type="submit"
                                                size="lg"
                                                className="w-full h-14 text-lg font-bold bg-gradient-to-r from-emerald-500 to-cyan-500 hover:from-emerald-600 hover:to-cyan-600 shadow-lg"
                                                disabled={loading}
                                            >
                                                {loading ? (
                                                    <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                                                ) : (
                                                    <Rocket className="mr-2 h-5 w-5" />
                                                )}
                                                Deploy to Cloud
                                            </Button>
                                        </form>
                                    </Card>
                                </motion.div>
                            )}
                        </motion.div>
                    ) : (
                        /* ==================== MANUAL MODE (TABS) ==================== */
                        <motion.div
                            key="manual"
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -20 }}
                        >
                            {/* Tab Headers */}
                            <div className="grid grid-cols-4 gap-2 mb-6">
                                {[
                                    { id: 'git', icon: Github, label: 'Git Repo' },
                                    { id: 'docker', icon: Container, label: 'Docker' },
                                    { id: 'upload', icon: Upload, label: 'Upload' },
                                    { id: 'template', icon: LayoutTemplate, label: 'Template' },
                                ].map((tab) => (
                                    <button
                                        key={tab.id}
                                        type="button"
                                        className={`flex flex-col items-center gap-2 p-4 rounded-xl border-2 transition-all ${activeTab === tab.id
                                                ? 'border-primary bg-primary/5'
                                                : 'border-muted hover:border-primary/50'
                                            }`}
                                        onClick={() => setActiveTab(tab.id)}
                                    >
                                        <tab.icon className="w-5 h-5" />
                                        <span className="text-sm font-medium">{tab.label}</span>
                                    </button>
                                ))}
                            </div>

                            <Card className="p-6">
                                <form onSubmit={handleSubmit} className="space-y-6">
                                    {error && (
                                        <div className="p-3 bg-destructive/10 border border-destructive/20 text-destructive rounded-md text-sm">
                                            {error}
                                        </div>
                                    )}

                                    {/* Common Fields */}
                                    <div className="grid md:grid-cols-2 gap-4">
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium">Service Name</label>
                                            <input
                                                type="text"
                                                required
                                                placeholder="my-app"
                                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:ring-2 focus:ring-ring"
                                                value={name}
                                                onChange={(e) => setName(e.target.value)}
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium">Internal Port</label>
                                            <input
                                                type="number"
                                                placeholder="8000"
                                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:ring-2 focus:ring-ring"
                                                value={port}
                                                onChange={(e) => setPort(e.target.value)}
                                            />
                                        </div>
                                    </div>

                                    {/* Tab-specific Fields */}
                                    {activeTab === 'git' && (
                                        <div className="space-y-4">
                                            <div className="space-y-2">
                                                <label className="text-sm font-medium">Repository URL</label>
                                                <input
                                                    type="url"
                                                    required
                                                    placeholder="https://github.com/username/repo"
                                                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:ring-2 focus:ring-ring"
                                                    value={repoUrl}
                                                    onChange={(e) => setRepoUrl(e.target.value)}
                                                />
                                            </div>
                                            <div className="space-y-2">
                                                <label className="text-sm font-medium">Branch</label>
                                                <input
                                                    type="text"
                                                    placeholder="main"
                                                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:ring-2 focus:ring-ring"
                                                    value={branch}
                                                    onChange={(e) => setBranch(e.target.value)}
                                                />
                                            </div>
                                        </div>
                                    )}

                                    {activeTab === 'docker' && (
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium">Docker Image</label>
                                            <input
                                                type="text"
                                                required
                                                placeholder="nginx:latest"
                                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:ring-2 focus:ring-ring"
                                                value={dockerImage}
                                                onChange={(e) => setDockerImage(e.target.value)}
                                            />
                                            <p className="text-xs text-muted-foreground">Image must be public or provide credentials in settings.</p>
                                        </div>
                                    )}

                                    {activeTab === 'upload' && (
                                        <div className="border-2 border-dashed border-muted-foreground/25 rounded-xl p-8 text-center hover:bg-muted/50 transition-colors">
                                            <Upload className="w-10 h-10 mx-auto text-muted-foreground mb-4" />
                                            <p className="text-sm font-medium mb-2">Drag and drop your source code archive</p>
                                            <p className="text-xs text-muted-foreground mb-4">Supported: .zip, .tar.gz</p>
                                            <input
                                                type="file"
                                                accept=".zip,.tar,.tar.gz"
                                                onChange={(e) => setFile(e.target.files?.[0] || null)}
                                                className="block w-full text-sm file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:bg-primary file:text-primary-foreground hover:file:bg-primary/90"
                                            />
                                        </div>
                                    )}

                                    {activeTab === 'template' && (
                                        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                                            {['PostgreSQL', 'Redis', 'MongoDB', 'MySQL', 'WordPress', 'n8n'].map((t) => (
                                                <div
                                                    key={t}
                                                    className={`p-4 border rounded-xl cursor-pointer transition-all ${selectedTemplate === t
                                                            ? 'border-primary bg-primary/5 ring-1 ring-primary'
                                                            : 'hover:border-primary/50'
                                                        }`}
                                                    onClick={() => setSelectedTemplate(t)}
                                                >
                                                    <div className="font-medium text-center">{t}</div>
                                                </div>
                                            ))}
                                        </div>
                                    )}

                                    {/* Domain */}
                                    <div className="pt-4 border-t space-y-4">
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium">Public Domain (Optional)</label>
                                            <input
                                                type="text"
                                                placeholder="app.example.com"
                                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:ring-2 focus:ring-ring"
                                                value={domain}
                                                onChange={(e) => setDomain(e.target.value)}
                                            />
                                        </div>

                                        <Button
                                            type="submit"
                                            className="w-full h-12 font-bold bg-gradient-to-r from-emerald-500 to-cyan-500 hover:from-emerald-600 hover:to-cyan-600"
                                            disabled={loading}
                                        >
                                            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Rocket className="mr-2 h-4 w-4" />}
                                            Deploy Project
                                        </Button>
                                    </div>
                                </form>
                            </Card>
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        </main>
    );
}

export default function NewServicePage() {
    return (
        <Suspense fallback={<div className="flex items-center justify-center min-h-screen"><Loader2 className="animate-spin" /></div>}>
            <NewServiceForm />
        </Suspense>
    );
}
