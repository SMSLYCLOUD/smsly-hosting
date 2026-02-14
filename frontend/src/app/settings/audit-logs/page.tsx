'use client';

import React, { useEffect, useState } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Search, Shield, User, Settings, Server, Key, AlertTriangle, Loader2 } from 'lucide-react';
import api from '@/lib/api';

interface AuditLogEntry {
  id: string;
  action: string;
  actor: string;
  target?: string;
  detail?: string;
  ip_address?: string;
  created_at: string;
}

export default function AuditLogsPage() {
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchLogs = async () => {
      try {
        const res = await api.get('/audit-logs/');
        const data = Array.isArray(res.data) ? res.data : (res.data?.results || []);
        setLogs(data);
      } catch (err: any) {
        console.error('Failed to fetch audit logs:', err);
        setError(err?.response?.status === 404 ? 'Audit logs endpoint not available.' : 'Failed to load audit logs.');
      } finally {
        setLoading(false);
      }
    };
    fetchLogs();
  }, []);

  const filtered = logs.filter(log =>
    !search || 
    log.action?.toLowerCase().includes(search.toLowerCase()) ||
    log.actor?.toLowerCase().includes(search.toLowerCase()) ||
    log.target?.toLowerCase().includes(search.toLowerCase())
  );

  const getActionIcon = (action: string) => {
    if (action?.includes('login') || action?.includes('auth')) return <Key size={14} />;
    if (action?.includes('deploy') || action?.includes('service')) return <Server size={14} />;
    if (action?.includes('setting') || action?.includes('config')) return <Settings size={14} />;
    if (action?.includes('user') || action?.includes('team')) return <User size={14} />;
    if (action?.includes('security') || action?.includes('delete')) return <AlertTriangle size={14} />;
    return <Shield size={14} />;
  };

  const getActionColor = (action: string) => {
    if (action?.includes('delete') || action?.includes('fail')) return 'text-red-500 bg-red-500/10';
    if (action?.includes('create') || action?.includes('deploy')) return 'text-emerald-500 bg-emerald-500/10';
    if (action?.includes('update') || action?.includes('change')) return 'text-blue-500 bg-blue-500/10';
    return 'text-muted-foreground bg-muted';
  };

  return (
    <DashboardShell>
      <div className="container max-w-5xl mx-auto p-6 space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Audit Logs</h1>
          <p className="text-muted-foreground">Security events and system activity.</p>
        </div>

        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search by action, user, or target..."
            className="pl-10"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <Card>
            <CardContent className="py-12 text-center">
              <AlertTriangle className="mx-auto h-8 w-8 text-yellow-500 mb-3" />
              <p className="text-muted-foreground">{error}</p>
            </CardContent>
          </Card>
        ) : filtered.length === 0 ? (
          <Card>
            <CardContent className="py-12 text-center">
              <Shield className="mx-auto h-8 w-8 text-muted-foreground mb-3" />
              <p className="font-medium">No audit logs found</p>
              <p className="text-sm text-muted-foreground mt-1">
                {search ? 'Try a different search query.' : 'Activity will appear here as events occur.'}
              </p>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <div className="divide-y divide-border">
              {filtered.map((log) => (
                <div key={log.id} className="px-4 py-3 flex items-start gap-3 hover:bg-muted/30 transition-colors">
                  <div className={`p-1.5 rounded-lg mt-0.5 ${getActionColor(log.action)}`}>
                    {getActionIcon(log.action)}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-sm">{log.action}</span>
                      {log.target && (
                        <Badge variant="outline" className="text-[10px]">{log.target}</Badge>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground mt-0.5">
                      by {log.actor}
                      {log.ip_address && ` • ${log.ip_address}`}
                      {log.detail && ` • ${log.detail}`}
                    </div>
                  </div>
                  <span className="text-xs text-muted-foreground whitespace-nowrap">
                    {new Date(log.created_at).toLocaleString()}
                  </span>
                </div>
              ))}
            </div>
          </Card>
        )}
      </div>
    </DashboardShell>
  );
}
