'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Loader2, AlertTriangle, XCircle, Info, Shield, Activity, Clock, ServerCrash, RefreshCw } from 'lucide-react';
import { servicesApi } from '@/lib/api';
import { formatDistanceToNow } from 'date-fns';

interface TimelineEvent {
    type: string;
    severity: string;
    timestamp: string;
    title: string;
    detail: string;
    deployment_id?: string;
    status?: string;
    acknowledged?: boolean;
    alert_id?: string;
    actor?: string;
    action?: string;
    branch?: string;
    is_rollback?: boolean;
    metric?: string;
    threshold?: number;
    current_value?: number;
    backup_id?: string;
    backup_type?: string;
    transfer_id?: string;
    snapshot_id?: string;
    trigger?: string;
}

interface IncidentReport {
    service_id: string;
    service_name: string;
    total_events: number;
    critical: number;
    warning: number;
    info: number;
    events: TimelineEvent[];
}

const severityConfig: Record<string, { icon: typeof AlertTriangle; color: string; bg: string }> = {
    critical: { icon: XCircle, color: 'text-red-500', bg: 'bg-red-500/10' },
    warning: { icon: AlertTriangle, color: 'text-amber-500', bg: 'bg-amber-500/10' },
    info: { icon: Info, color: 'text-blue-500', bg: 'bg-blue-500/10' },
};

const typeMeta: Record<string, { icon: typeof Shield; label: string }> = {
    deployment: { icon: ServerCrash, label: 'Deploy' },
    resource_alert: { icon: AlertTriangle, label: 'Alert' },
    health: { icon: Activity, label: 'Health' },
    waf_summary: { icon: Shield, label: 'WAF' },
    rollback: { icon: ServerCrash, label: 'Rollback' },
    backup: { icon: Shield, label: 'Backup' },
    backup_failure: { icon: XCircle, label: 'Backup Fail' },
    ai_remediation: { icon: Activity, label: 'AI Fix' },
    service_lifecycle: { icon: Info, label: 'Lifecycle' },
    transfer: { icon: ServerCrash, label: 'Transfer' },
    snapshot: { icon: Clock, label: 'Snapshot' },
    infrastructure: { icon: ServerCrash, label: 'Infra' },
    mesh: { icon: AlertTriangle, label: 'Mesh' },
    cloud_upload_failure: { icon: XCircle, label: 'Cloud Fail' },
};

export function IncidentReportTab({ serviceId }: { serviceId: string }) {
    const [report, setReport] = useState<IncidentReport | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchReport = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const data = await servicesApi.getIncidentReport(serviceId);
            setReport(data);
        } catch (err: any) {
            setError(err?.response?.data?.error || 'Failed to load incident report.');
        } finally {
            setLoading(false);
        }
    }, [serviceId]);

    useEffect(() => { fetchReport(); }, [fetchReport]);

    if (loading) {
        return (
            <div className="flex items-center justify-center py-20">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
        );
    }

    if (error) {
        return (
            <Card className="border-red-500/30 bg-red-500/5">
                <CardContent className="py-10 text-center">
                    <XCircle className="h-10 w-10 text-red-500 mx-auto mb-3" />
                    <p className="text-red-400 font-medium">{error}</p>
                </CardContent>
            </Card>
        );
    }

    if (!report || report.events.length === 0) {
        return (
            <Card>
                <CardContent className="py-16 text-center text-muted-foreground">
                    <Shield className="h-12 w-12 mx-auto mb-3 opacity-30" />
                    <p className="text-lg font-medium">No incidents or events</p>
                    <p className="text-sm mt-1">This service has a clean record over the last 90 days.</p>
                </CardContent>
            </Card>
        );
    }

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            {/* Summary header */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">Total Events</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-3xl font-bold">{report.total_events}</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">Critical</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-3xl font-bold text-red-500">{report.critical}</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">Warnings</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-3xl font-bold text-amber-500">{report.warning}</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">Info</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-3xl font-bold text-blue-500">{report.info}</p>
                    </CardContent>
                </Card>
            </div>

            {/* Timeline */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Clock className="h-5 w-5" />
                        Event Timeline
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="relative border-l-2 border-border ml-3">
                        {report.events.map((event, idx) => {
                            const sev = severityConfig[event.severity] || severityConfig.info;
                            const Icon = sev.icon;
                            const meta = typeMeta[event.type] || { icon: Activity, label: event.type };
                            const TypeIcon = meta.icon;
                            const timeAgo = event.timestamp
                                ? formatDistanceToNow(new Date(event.timestamp), { addSuffix: true })
                                : '';

                            return (
                                <div key={idx} className="mb-6 ml-6 relative">
                                    {/* Timeline dot */}
                                    <div className={`absolute -left-[31px] p-1 rounded-full border-2 border-background ${sev.bg}`}>
                                        <Icon className={`h-4 w-4 ${sev.color}`} />
                                    </div>

                                    <div className="flex flex-col gap-1">
                                        <div className="flex items-center gap-2 flex-wrap">
                                            <Badge variant="outline" className="text-[10px] uppercase tracking-wider">
                                                <TypeIcon className="h-3 w-3 mr-1" />
                                                {meta.label}
                                            </Badge>
                                            <Badge className={`text-[10px] ${sev.bg} ${sev.color} border-0`}>
                                                {event.severity}
                                            </Badge>
                                            {event.action && (
                                                <span className="text-[10px] text-muted-foreground font-mono">
                                                    {event.action}
                                                </span>
                                            )}
                                            {timeAgo && (
                                                <span className="text-xs text-muted-foreground ml-auto">{timeAgo}</span>
                                            )}
                                        </div>
                                        <p className="text-sm font-medium text-foreground">{event.title}</p>
                                        {event.detail && (
                                            <p className="text-xs text-muted-foreground">{event.detail}</p>
                                        )}
                                        {/* Extra metadata */}
                                        {event.metric && (
                                            <span className="text-[10px] text-muted-foreground">
                                                {event.metric}: {event.current_value ?? '?'} / threshold {event.threshold ?? '?'}
                                            </span>
                                        )}
                                        {event.branch && (
                                            <span className="text-[10px] text-muted-foreground font-mono">
                                                branch: {event.branch}
                                            </span>
                                        )}
                                        {event.acknowledged !== undefined && (
                                            <span className="text-[10px] text-muted-foreground">
                                                {event.acknowledged ? '✓ Acknowledged' : '⚠ Unacknowledged'}
                                            </span>
                                        )}
                                        {event.is_rollback && (
                                            <Badge variant="outline" className="text-[9px] w-fit border-amber-500/30 text-amber-500">
                                                ROLLBACK
                                            </Badge>
                                        )}
                                        {event.trigger && (
                                            <span className="text-[10px] text-muted-foreground">
                                                Trigger: {event.trigger}
                                            </span>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </CardContent>
            </Card>

            {/* Refresh button */}
            <div className="flex justify-end">
                <button
                    onClick={fetchReport}
                    disabled={loading}
                    className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
                >
                    <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                    Refresh
                </button>
            </div>
        </div>
    );
}
