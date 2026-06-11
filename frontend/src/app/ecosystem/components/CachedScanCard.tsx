'use client';

import { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Card, CardContent, CardHeader, CardTitle, CardDescription,
} from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Loader2, RefreshCw, Clock, Server, Bug, Package, AlertTriangle, ChevronDown, ChevronUp, FileCode } from 'lucide-react';
import { ecosystemApi } from '@/lib/api';
import { useToast } from '@/components/ui/use-toast';

interface ScanSummary {
  total_apps: number;
  vulnerabilities: number;
  outdated_packages: number;
}

interface CachedScanResult {
  id: string;
  scanned_at: string;
  summary: ScanSummary;
  apps: {
    id: string;
    name: string;
    repo?: string;
    stack?: string;
    vulnerabilities: number;
    outdated_packages: number;
    issues: string[];
  }[];
}

export function CachedScanCard() {
  const { toast } = useToast();
  const [data, setData] = useState<CachedScanResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [appExpanded, setAppExpanded] = useState<Set<string>>(new Set());

  const fetch = useCallback(async (showToast = true) => {
    setLoading(true);
    try {
      const result = await ecosystemApi.cachedScan();
      setData(result);
      setLoaded(true);
    } catch (err: any) {
      if (showToast) {
        toast({ title: 'Failed to load cached scan', description: err?.message || 'Unknown error', variant: 'destructive' });
      }
      setLoaded(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetch(false); }, [fetch]);

  const toggleApp = (id: string) => {
    setAppExpanded(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const summaryItems = data ? [
    { label: 'Total Apps', value: data.summary.total_apps, icon: Server, color: 'text-blue-500' },
    { label: 'Vulnerabilities', value: data.summary.vulnerabilities, icon: Bug, color: data.summary.vulnerabilities > 0 ? 'text-red-500' : 'text-emerald-500' },
    { label: 'Outdated Packages', value: data.summary.outdated_packages, icon: Package, color: data.summary.outdated_packages > 0 ? 'text-yellow-500' : 'text-emerald-500' },
  ] : [];

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <FileCode size={16} className="text-primary" />
              Cached Scan Results
            </CardTitle>
            <CardDescription>
              {data
                ? `Last scanned: ${new Date(data.scanned_at).toLocaleString()}`
                : loaded
                  ? 'No cached scan available'
                  : 'Loading...'}
            </CardDescription>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetch(true)}
            disabled={loading}
          >
            <RefreshCw size={14} className={loading ? 'animate-spin mr-1' : 'mr-1'} />
            {loading ? 'Refreshing...' : 'Refresh'}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {loading && !data && (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="animate-spin text-muted-foreground" size={20} />
          </div>
        )}

        {!loading && !data && loaded && (
          <div className="flex flex-col items-center justify-center py-6 gap-2 text-muted-foreground">
            <Clock size={24} />
            <p className="text-sm">No cached scan data found.</p>
            <p className="text-xs">Run an ecosystem scan to generate results.</p>
          </div>
        )}

        {data && (
          <div className="space-y-4">
            {/* Summary grid */}
            <div className="grid grid-cols-3 gap-3">
              {summaryItems.map(item => {
                const Icon = item.icon;
                return (
                  <div key={item.label} className="bg-muted/50 rounded-lg p-3 text-center">
                    <Icon size={18} className={`mx-auto mb-1 ${item.color}`} />
                    <p className={`text-lg font-bold ${item.color}`}>{item.value}</p>
                    <p className="text-[10px] text-muted-foreground uppercase tracking-wider">{item.label}</p>
                  </div>
                );
              })}
            </div>

            {/* Expandable detail */}
            <div>
              <button
                onClick={() => setExpanded(!expanded)}
                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors w-full justify-center py-1"
              >
                {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                {expanded ? 'Hide Details' : `Show Details (${data.apps.length} apps)`}
              </button>

              <AnimatePresence>
                {expanded && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    className="space-y-2 pt-2"
                  >
                    {data.apps.length === 0 ? (
                      <p className="text-sm text-muted-foreground text-center py-2">No app details available.</p>
                    ) : (
                      data.apps.map(app => (
                        <div key={app.id} className="border border-border rounded-lg overflow-hidden">
                          <button
                            onClick={() => toggleApp(app.id)}
                            className="w-full flex items-center justify-between p-3 hover:bg-muted/30 transition-colors text-left"
                          >
                            <div className="flex items-center gap-2 min-w-0">
                              <Server size={12} className="text-muted-foreground shrink-0" />
                              <span className="text-sm font-medium truncate">{app.name || app.repo || app.id}</span>
                              {app.stack && (
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0">{app.stack}</Badge>
                              )}
                            </div>
                            <div className="flex items-center gap-2 shrink-0">
                              {app.vulnerabilities > 0 && (
                                <span className="text-xs text-red-500 flex items-center gap-0.5">
                                  <Bug size={10} /> {app.vulnerabilities}
                                </span>
                              )}
                              {app.outdated_packages > 0 && (
                                <span className="text-xs text-yellow-500 flex items-center gap-0.5">
                                  <Package size={10} /> {app.outdated_packages}
                                </span>
                              )}
                              {app.vulnerabilities === 0 && app.outdated_packages === 0 && (
                                <span className="text-xs text-emerald-500">Clean</span>
                              )}
                              {appExpanded.has(app.id) ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                            </div>
                          </button>
                          <AnimatePresence>
                            {appExpanded.has(app.id) && (
                              <motion.div
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: 'auto' }}
                                exit={{ opacity: 0, height: 0 }}
                                className="border-t border-border px-3 py-2 space-y-1"
                              >
                                {app.issues.length === 0 ? (
                                  <p className="text-xs text-emerald-500 flex items-center gap-1">
                                    <AlertTriangle size={10} /> No issues detected
                                  </p>
                                ) : (
                                  app.issues.map((issue, i) => (
                                    <p key={i} className="text-xs text-muted-foreground flex items-start gap-1.5">
                                      <AlertTriangle size={10} className="text-yellow-500 shrink-0 mt-0.5" />
                                      {issue}
                                    </p>
                                  ))
                                )}
                              </motion.div>
                            )}
                          </AnimatePresence>
                        </div>
                      ))
                    )}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
