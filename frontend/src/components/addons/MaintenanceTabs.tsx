'use client';

import { useState, useEffect, useCallback, memo } from 'react';
import { addonMaintenanceApi, addonsApi } from '@/lib/api';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { useToast } from '@/components/ui/use-toast';
import {
  Tabs, TabsContent, TabsList, TabsTrigger,
} from '@/components/ui/tabs';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  BarChart3, Database, Play, Loader2, Trash2, RotateCcw, Eye, EyeOff, Copy, Check,
  Clock, HardDrive, Wifi, Activity, Server, Table2, Zap, Shield,
} from 'lucide-react';
import { Textarea } from '@/components/ui/textarea';

interface MaintenanceTabsProps {
  addonId: string;
}

type TabId = 'overview' | 'tables' | 'query' | 'vacuum' | 'credentials';

export function MaintenanceTabs({ addonId }: MaintenanceTabsProps) {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [activeTab, setActiveTab] = useState<TabId>('overview');

  // Overview
  const [stats, setStats] = useState<any>(null);
  const [statsLoading, setStatsLoading] = useState(false);

  // Tables
  const [tables, setTables] = useState<any[]>([]);
  const [tablesLoading, setTablesLoading] = useState(false);

  // Query
  const [query, setQuery] = useState('SELECT * FROM information_schema.tables LIMIT 10;');
  const [queryResults, setQueryResults] = useState<any[]>([]);
  const [queryColumns, setQueryColumns] = useState<string[]>([]);
  const [queryLoading, setQueryLoading] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);

  // Vacuum
  const [vacuumLoading, setVacuumLoading] = useState(false);

  // Credentials
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [credsLoading, setCredsLoading] = useState(false);
  const [revealedCreds, setRevealedCreds] = useState<Record<string, boolean>>({});
  const [rotateLoading, setRotateLoading] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  const fetchStats = useCallback(async () => {
    setStatsLoading(true);
    try {
      const data = await addonMaintenanceApi.stats(addonId);
      setStats(data);
    } catch {
      toast({ title: 'Failed to load DB stats', variant: 'destructive' });
    } finally {
      setStatsLoading(false);
    }
  }, [addonId, toast]);

  const fetchTables = useCallback(async () => {
    setTablesLoading(true);
    try {
      const data = await addonMaintenanceApi.tables(addonId);
      setTables(Array.isArray(data) ? data : data?.tables || []);
    } catch {
      toast({ title: 'Failed to load tables', variant: 'destructive' });
    } finally {
      setTablesLoading(false);
    }
  }, [addonId, toast]);

  const fetchCredentials = useCallback(async () => {
    setCredsLoading(true);
    try {
      const data = await addonsApi.addonCredentials(addonId);
      setCredentials(data || {});
    } catch {
      toast({ title: 'Failed to load credentials', variant: 'destructive' });
    } finally {
      setCredsLoading(false);
    }
  }, [addonId, toast]);

  useEffect(() => {
    if (activeTab === 'overview') fetchStats();
    if (activeTab === 'tables') fetchTables();
    if (activeTab === 'credentials') fetchCredentials();
  }, [activeTab, fetchStats, fetchTables, fetchCredentials]);

  const handleRunQuery = async () => {
    if (!query.trim()) return;
    setQueryLoading(true);
    setQueryError(null);
    setQueryResults([]);
    setQueryColumns([]);
    try {
      const data = await addonMaintenanceApi.query(addonId, query);
      if (data.error) {
        setQueryError(data.error);
      } else {
        setQueryResults(data.results || []);
        setQueryColumns(data.columns || []);
      }
    } catch (err: any) {
      setQueryError(err.response?.data?.error || 'Query failed');
    } finally {
      setQueryLoading(false);
    }
  };

  const handleVacuum = async () => {
    if (!await confirm({
      title: 'Run VACUUM?',
      message: 'This will run VACUUM on the database. It may lock tables during execution. Continue?',
      variant: 'warning',
      confirmText: 'Run VACUUM',
    })) return;
    setVacuumLoading(true);
    try {
      const data = await addonMaintenanceApi.vacuum(addonId);
      toast({ title: 'VACUUM completed', description: data.message });
      fetchStats();
    } catch (err: any) {
      toast({ title: 'VACUUM failed', description: err.response?.data?.error || 'Unknown error', variant: 'destructive' });
    } finally {
      setVacuumLoading(false);
    }
  };

  const handleRotateCredentials = async () => {
    if (!await confirm({
      title: 'Rotate Credentials?',
      message: 'This will invalidate current credentials and generate new ones. Connected services will need to be updated. Continue?',
      variant: 'destructive',
      confirmText: 'Rotate',
    })) return;
    setRotateLoading(true);
    try {
      const data = await addonMaintenanceApi.rotateCredentials(addonId);
      toast({ title: 'Credentials rotated', description: 'New connection details generated' });
      setCredentials(data || {});
    } catch (err: any) {
      toast({ title: 'Rotation failed', description: err.response?.data?.error || 'Unknown error', variant: 'destructive' });
    } finally {
      setRotateLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as TabId)}>
        <TabsList className="w-full flex-wrap h-auto gap-1">
          <TabsTrigger value="overview" className="flex items-center gap-2">
            <BarChart3 size={14} /> Overview
          </TabsTrigger>
          <TabsTrigger value="tables" className="flex items-center gap-2">
            <Table2 size={14} /> Tables
          </TabsTrigger>
          <TabsTrigger value="query" className="flex items-center gap-2">
            <Database size={14} /> Query
          </TabsTrigger>
          <TabsTrigger value="vacuum" className="flex items-center gap-2">
            <Zap size={14} /> Vacuum
          </TabsTrigger>
          <TabsTrigger value="credentials" className="flex items-center gap-2">
            <Shield size={14} /> Credentials
          </TabsTrigger>
        </TabsList>

        {/* ── Overview / Stats Tab ── */}
        <TabsContent value="overview" className="space-y-4">
          {statsLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <Card key={i}><CardHeader><Skeleton className="h-4 w-24" /></CardHeader><CardContent><Skeleton className="h-8 w-16" /></CardContent></Card>
              ))}
            </div>
          ) : stats ? (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <StatCard icon={Activity} label="Status" value={stats.status || 'N/A'} />
                <StatCard icon={Clock} label="Uptime" value={stats.uptime || 'N/A'} />
                <StatCard icon={HardDrive} label="Database Size" value={stats.database_size || stats.size || 'N/A'} />
                <StatCard icon={Wifi} label="Active Connections" value={String(stats.active_connections ?? stats.connections ?? 'N/A')} />
                <StatCard icon={Server} label="Version" value={stats.version || 'N/A'} />
                <StatCard icon={BarChart3} label="Transactions" value={stats.transactions ?? stats.txns ?? 'N/A'} />
              </div>
              {stats.extra && Object.keys(stats.extra).length > 0 && (
                <Card>
                  <CardHeader><CardTitle className="text-sm font-medium">Additional Stats</CardTitle></CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
                      {Object.entries(stats.extra).map(([key, val]) => (
                        <div key={key} className="flex items-center justify-between p-3 bg-muted/30 rounded-lg">
                          <span className="text-xs text-muted-foreground font-mono">{key}</span>
                          <span className="text-sm font-semibold">{String(val)}</span>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              )}
              <div className="text-right">
                <Button variant="outline" size="sm" onClick={fetchStats} className="gap-2">
                  <Loader2 size={12} /> Refresh
                </Button>
              </div>
            </>
          ) : (
            <div className="text-center py-12 text-muted-foreground">No stats available.</div>
          )}
        </TabsContent>

        {/* ── Tables Tab ── */}
        <TabsContent value="tables" className="space-y-4">
          {tablesLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-lg" />
              ))}
            </div>
          ) : tables.length > 0 ? (
            <div className="border rounded-lg overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Table Name</TableHead>
                    <TableHead className="text-right">Row Count</TableHead>
                    <TableHead className="text-right">Size</TableHead>
                    <TableHead className="text-right">Live Tuples</TableHead>
                    <TableHead className="text-right">Dead Tuples</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tables.map((t: { name?: string; table_name?: string; row_count?: number; rows?: number; size?: string; total_size?: string; live_tuples?: number; dead_tuples?: number }, i: number) => (
                    <TableRow key={t.name || i}>
                      <TableCell className="font-mono text-sm">{t.name || t.table_name}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{t.row_count ?? t.rows ?? '-'}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{t.size ?? t.total_size ?? '-'}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{t.live_tuples ?? '-'}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{t.dead_tuples ?? '-'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <div className="text-center py-12 text-muted-foreground">No tables found.</div>
          )}
        </TabsContent>

        {/* ── Query Tab ── */}
        <TabsContent value="query" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm font-medium">
                <Database className="w-4 h-4 text-blue-500" />
                SQL Query
              </CardTitle>
              <CardDescription>Execute read-only SQL queries against the addon database.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="relative">
                <Textarea
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="SELECT * FROM ..."
                  className="font-mono text-sm min-h-[120px] bg-secondary/30"
                />
                <div className="absolute bottom-2 right-2">
                  <Button size="sm" onClick={handleRunQuery} disabled={queryLoading || !query.trim()}>
                    {queryLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4 mr-1" />}
                    Run
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>

          {queryError && (
            <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
              {queryError}
            </div>
          )}

          {queryColumns.length > 0 && (
            <div className="border rounded-lg overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    {queryColumns.map((col) => (
                      <TableHead key={col} className="whitespace-nowrap font-mono text-xs">{col}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {queryResults.map((row, i) => (
                    <TableRow key={i}>
                      {queryColumns.map((col) => (
                        <TableCell key={`${i}-${col}`} className="font-mono text-xs whitespace-nowrap">
                          {typeof row[col] === 'object' ? JSON.stringify(row[col]) : String(row[col] ?? '')}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {!queryLoading && !queryError && query.length > 0 && queryResults.length === 0 && queryColumns.length === 0 && (
            <div className="text-center py-8 text-muted-foreground">Run a query to see results.</div>
          )}
        </TabsContent>

        {/* ── Vacuum Tab ── */}
        <TabsContent value="vacuum" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm font-medium">
                <Zap className="w-4 h-4 text-amber-500" />
                Database Vacuum
              </CardTitle>
              <CardDescription>
                VACUUM reclaims storage and updates statistics. May briefly lock tables.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {stats?.last_vacuum && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Clock size={14} />
                  Last vacuum: {new Date(stats.last_vacuum).toLocaleString()}
                </div>
              )}
              {stats?.dead_tuples_total !== undefined && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Trash2 size={14} />
                  Dead tuples: {stats.dead_tuples_total}
                </div>
              )}
              <Button
                onClick={handleVacuum}
                disabled={vacuumLoading}
                variant="outline"
                className="gap-2 border-amber-500/30 text-amber-400 hover:bg-amber-500/10"
              >
                {vacuumLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap size={14} />}
                {vacuumLoading ? 'Running...' : 'Run VACUUM'}
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Credentials Tab ── */}
        <TabsContent value="credentials" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm font-medium">
                <Shield className="w-4 h-4 text-green-500" />
                Connection Credentials
              </CardTitle>
              <CardDescription>
                Rotating credentials will invalidate the current ones. Update any connected services afterwards.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {credsLoading ? (
                <div className="space-y-3">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <Skeleton key={i} className="h-16 w-full rounded-lg" />
                  ))}
                </div>
              ) : Object.keys(credentials).length > 0 ? (
                <div className="space-y-3">
                  {Object.entries(credentials).map(([key, value]) => {
                    const isRevealed = revealedCreds[key];
                    return (
                      <div key={key} className="bg-muted/30 rounded-lg border p-3">
                        <div className="flex items-center justify-between mb-1">
                          <span className="font-mono text-xs text-blue-300">{key}</span>
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => setRevealedCreds(prev => ({ ...prev, [key]: !isRevealed }))}
                              className="text-muted-foreground hover:text-foreground transition-colors"
                            >
                              {isRevealed ? <EyeOff size={12} /> : <Eye size={12} />}
                            </button>
                            <button
                              onClick={() => {
                                navigator.clipboard.writeText(value);
                                setCopied(key);
                                setTimeout(() => setCopied(null), 2000);
                              }}
                              className="text-muted-foreground hover:text-foreground transition-colors"
                            >
                              {copied === key ? <Check size={12} className="text-emerald-500" /> : <Copy size={12} />}
                            </button>
                          </div>
                        </div>
                        <div className="font-mono text-xs text-zinc-400 break-all">
                          {isRevealed ? value : '•'.repeat(24)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="text-center py-8 text-muted-foreground text-sm">
                  No credentials available.
                </div>
              )}

              <div className="flex items-center gap-3 pt-2">
                <Button
                  onClick={handleRotateCredentials}
                  disabled={rotateLoading}
                  variant="outline"
                  className="gap-2 border-red-500/30 text-red-400 hover:bg-red-500/10"
                >
                  {rotateLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw size={14} />}
                  {rotateLoading ? 'Rotating...' : 'Rotate Credentials'}
                </Button>
              </div>

              {stats?.credential_age && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Clock size={12} />
                  Current credentials age: {stats.credential_age}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

const StatCard = memo(function StatCard({ icon: Icon, label, value }: { icon: React.ComponentType<{ className?: string }>; label: string; value: string }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
      </CardContent>
    </Card>
  );
});
