'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Loader2, CheckCircle2, AlertCircle, Sparkles, Github, Zap } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import axios from 'axios';

// Simple debounce utility
function useDebounce<T extends (...args: any[]) => any>(fn: T, delay: number): T {
    const timeoutRef = useRef<NodeJS.Timeout | null>(null);

    return useCallback((...args: Parameters<T>) => {
        if (timeoutRef.current) clearTimeout(timeoutRef.current);
        timeoutRef.current = setTimeout(() => fn(...args), delay);
    }, [fn, delay]) as T;
}

// Framework icons/colors
const FRAMEWORK_INFO: Record<string, { color: string; label: string; icon: string }> = {
    nextjs: { color: 'bg-black text-white', label: 'Next.js', icon: '▲' },
    react: { color: 'bg-cyan-500 text-white', label: 'React', icon: '⚛️' },
    vue: { color: 'bg-green-500 text-white', label: 'Vue.js', icon: '🟢' },
    nuxt: { color: 'bg-green-600 text-white', label: 'Nuxt', icon: '💚' },
    django: { color: 'bg-emerald-700 text-white', label: 'Django', icon: '🐍' },
    fastapi: { color: 'bg-teal-500 text-white', label: 'FastAPI', icon: '⚡' },
    flask: { color: 'bg-gray-700 text-white', label: 'Flask', icon: '🧪' },
    express: { color: 'bg-gray-800 text-white', label: 'Express', icon: '🚂' },
    nestjs: { color: 'bg-red-600 text-white', label: 'NestJS', icon: '🐱' },
    go: { color: 'bg-cyan-600 text-white', label: 'Go', icon: '🐹' },
    rust: { color: 'bg-orange-600 text-white', label: 'Rust', icon: '🦀' },
    rails: { color: 'bg-red-500 text-white', label: 'Rails', icon: '💎' },
    unknown: { color: 'bg-gray-500 text-white', label: 'Unknown', icon: '❓' },
};

interface AnalysisResult {
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
    detected_files: string[];
    warning?: string;
}

interface RepoAnalyzerProps {
    onAnalysisComplete: (result: AnalysisResult & { repoUrl: string }) => void;
    initialUrl?: string;
}

export function RepoAnalyzer({ onAnalysisComplete, initialUrl = '' }: RepoAnalyzerProps) {
    const [repoUrl, setRepoUrl] = useState(initialUrl);
    const [isAnalyzing, setIsAnalyzing] = useState(false);
    const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
    const [error, setError] = useState('');

    const analyzeRepo = useCallback(async (url: string) => {
        if (!url || !url.includes('github.com')) {
            setAnalysis(null);
            return;
        }

        setIsAnalyzing(true);
        setError('');

        try {
            const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
            const response = await axios.post(
                '/api/v1/analyze-repo/',
                { repo_url: url },
                { headers: token ? { Authorization: `Token ${token}` } : {} }
            );

            setAnalysis(response.data);
            onAnalysisComplete({ ...response.data, repoUrl: url });
        } catch (err: any) {
            console.error(err);
            setError(err.response?.data?.detail || 'Failed to analyze repository');
            setAnalysis(null);
        } finally {
            setIsAnalyzing(false);
        }
    }, [onAnalysisComplete]);

    // Debounced analysis using custom hook
    const debouncedAnalyze = useDebounce(analyzeRepo, 800);

    useEffect(() => {
        if (repoUrl && (repoUrl.includes('github.com') || repoUrl.includes('gitlab.com') || repoUrl.includes('bitbucket.org'))) {
            debouncedAnalyze(repoUrl);
        }
    }, [repoUrl, debouncedAnalyze]);

    const frameworkInfo = analysis ? FRAMEWORK_INFO[analysis.detected_framework] || FRAMEWORK_INFO.unknown : null;
    const confidencePercent = analysis ? Math.round(analysis.confidence * 100) : 0;

    return (
        <div className="space-y-6">
            {/* Main URL Input */}
            <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                    <Github className="h-5 w-5 text-muted-foreground" />
                </div>
                <input
                    type="url"
                    placeholder="Paste your GitHub repository URL..."
                    className="w-full h-14 pl-12 pr-4 text-lg rounded-xl border-2 border-muted bg-background focus:border-primary focus:ring-2 focus:ring-primary/20 transition-all"
                    value={repoUrl}
                    onChange={(e) => setRepoUrl(e.target.value)}
                />
                {isAnalyzing && (
                    <div className="absolute inset-y-0 right-4 flex items-center">
                        <Loader2 className="h-5 w-5 animate-spin text-primary" />
                    </div>
                )}
            </div>

            {/* Error State */}
            {error && (
                <div className="flex items-center gap-2 p-3 bg-destructive/10 border border-destructive/20 text-destructive rounded-lg">
                    <AlertCircle className="h-4 w-4" />
                    <span className="text-sm">{error}</span>
                </div>
            )}

            {/* Analysis Results */}
            {analysis && (
                <Card className="p-6 border-primary/30 bg-gradient-to-br from-primary/5 to-transparent animate-in fade-in slide-in-from-bottom-4 duration-300">
                    <div className="flex items-start justify-between mb-4">
                        <div className="flex items-center gap-3">
                            <div className={`w-12 h-12 rounded-xl flex items-center justify-center text-2xl ${frameworkInfo?.color}`}>
                                {frameworkInfo?.icon}
                            </div>
                            <div>
                                <div className="flex items-center gap-2">
                                    <h3 className="font-bold text-lg">{frameworkInfo?.label} Detected</h3>
                                    <Badge variant={confidencePercent > 80 ? 'default' : 'secondary'} className="text-xs">
                                        {confidencePercent}% confidence
                                    </Badge>
                                </div>
                                <p className="text-sm text-muted-foreground">
                                    AI-powered analysis complete
                                </p>
                            </div>
                        </div>
                        <Sparkles className="h-5 w-5 text-primary" />
                    </div>

                    {/* Detected Configuration */}
                    <div className="grid md:grid-cols-2 gap-4 mt-4">
                        <div className="space-y-3">
                            <div className="flex items-center justify-between p-3 bg-muted/50 rounded-lg">
                                <span className="text-sm text-muted-foreground">Port</span>
                                <span className="font-mono font-bold">{analysis.suggested_port}</span>
                            </div>
                            {analysis.build_command && (
                                <div className="p-3 bg-muted/50 rounded-lg">
                                    <span className="text-xs text-muted-foreground block mb-1">Build Command</span>
                                    <code className="text-sm font-mono">{analysis.build_command}</code>
                                </div>
                            )}
                            {analysis.start_command && (
                                <div className="p-3 bg-muted/50 rounded-lg">
                                    <span className="text-xs text-muted-foreground block mb-1">Start Command</span>
                                    <code className="text-sm font-mono">{analysis.start_command}</code>
                                </div>
                            )}
                        </div>

                        <div className="space-y-3">
                            <div className="p-3 bg-muted/50 rounded-lg">
                                <span className="text-xs text-muted-foreground block mb-2">Resources</span>
                                <div className="flex gap-4">
                                    <div>
                                        <span className="text-xs text-muted-foreground">CPU</span>
                                        <p className="font-bold">{analysis.resource_recommendation.cpu} cores</p>
                                    </div>
                                    <div>
                                        <span className="text-xs text-muted-foreground">Memory</span>
                                        <p className="font-bold">{analysis.resource_recommendation.memory}</p>
                                    </div>
                                </div>
                            </div>

                            {analysis.has_dockerfile && (
                                <div className="flex items-center gap-2 p-3 bg-green-500/10 text-green-600 rounded-lg">
                                    <CheckCircle2 className="h-4 w-4" />
                                    <span className="text-sm font-medium">Dockerfile detected</span>
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Environment Variables */}
                    {analysis.suggested_env_vars.length > 0 && (
                        <div className="mt-4 p-4 bg-amber-500/10 border border-amber-500/20 rounded-lg">
                            <div className="flex items-center gap-2 mb-2">
                                <Zap className="h-4 w-4 text-amber-500" />
                                <span className="text-sm font-medium text-amber-600">Environment Variables Needed</span>
                            </div>
                            <div className="flex flex-wrap gap-2">
                                {analysis.suggested_env_vars.map((envVar) => (
                                    <Badge key={envVar} variant="outline" className="font-mono text-xs">
                                        {envVar}
                                    </Badge>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Warning */}
                    {analysis.warning && (
                        <div className="mt-4 flex items-center gap-2 p-3 bg-yellow-500/10 text-yellow-600 rounded-lg">
                            <AlertCircle className="h-4 w-4" />
                            <span className="text-sm">{analysis.warning}</span>
                        </div>
                    )}
                </Card>
            )}
        </div>
    );
}
