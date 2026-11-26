"use client";

import { useState, useEffect, useCallback } from "react";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Loader2, Database, Clock, Hash, Zap, RefreshCcw, Trash2, BarChart3, AlertTriangle } from "lucide-react";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { useToast } from "@/components/ui/use-toast";
import api from "@/lib/api";

interface SlowQuery {
  queryid: string;
  query: string;
  calls: number;
  mean_time_ms: number;
  total_time_ms: number;
  rows: number;
  shared_blks_hit: number;
  shared_blks_read: number;
}

interface QueryStats {
  unique_queries: number;
  total_calls: number;
  total_time_ms: number;
  cache_hit_ratio: number;
}

const MIN_MS_OPTIONS = [
  { value: "10", label: "> 10ms" },
  { value: "50", label: "> 50ms" },
  { value: "100", label: "> 100ms" },
  { value: "500", label: "> 500ms" },
  { value: "1000", label: "> 1s" },
];

export default function SlowQueriesPage() {
  const confirm = useConfirm();
  const { toast } = useToast();
  const [queries, setQueries] = useState<SlowQuery[]>([]);
  const [stats, setStats] = useState<QueryStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [minMs, setMinMs] = useState("100");
  const [resetting, setResetting] = useState(false);
  const [extensionAvailable, setExtensionAvailable] = useState<boolean>(true);
  const [extensionWarning, setExtensionWarning] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get(`/slow-queries/?min_ms=${minMs}&limit=50`);
      setQueries(res.data.queries || []);
      setStats(res.data.stats || null);
      setExtensionAvailable(res.data.extension_available !== false);
      setExtensionWarning(res.data.warning || null);
    } catch (err: any) {
      toast({
        title: "Failed to load slow queries",
        description: err?.response?.data?.error || err?.message || "Could not fetch query stats.",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  }, [minMs, toast]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleReset = async () => {
    const ok = await confirm({
      title: "Reset query statistics?",
      message: "This clears accumulated pg_stat_statements counters across the database.",
      variant: "destructive",
      confirmText: "Reset Stats",
    });
    if (!ok) return;
    setResetting(true);
    try {
      await api.post("/slow-queries/reset/");
      toast({ title: "Slow query statistics reset" });
      await fetchData();
    } catch (err: any) {
      toast({
        title: "Failed to reset slow queries",
        description: err?.response?.data?.error || err?.message || "Could not reset query stats.",
        variant: "destructive",
      });
    } finally {
      setResetting(false);
    }
  };

  const formatTime = (ms: number) => ms < 1000 ? `${ms.toFixed(1)}ms` : `${(ms / 1000).toFixed(2)}s`;

  return (
    <DashboardShell>
      <div className="container mx-auto py-8 space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Slow Queries</h1>
            <p className="text-sm text-muted-foreground mt-1">PostgreSQL query performance from pg_stat_statements</p>
          </div>
          <div className="flex items-center gap-3">
            <Select value={minMs} onValueChange={setMinMs}>
              <SelectTrigger className="w-28 h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>{MIN_MS_OPTIONS.map(o => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
            </Select>
            <Button variant="outline" size="sm" onClick={fetchData}><RefreshCcw className="w-3 h-3 mr-1" />Refresh</Button>
            <Button variant="outline" size="sm" onClick={handleReset} disabled={resetting}><Trash2 className="w-3 h-3 mr-1" />{resetting ? "..." : "Reset"}</Button>
          </div>
        </div>

        {/* Extension warning */}
        {!loading && !extensionAvailable && (
          <Card className="border-amber-500/40 bg-amber-500/5">
            <CardContent className="p-4 flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
              <div className="space-y-1">
                <p className="text-sm font-medium text-amber-600 dark:text-amber-400">
                  PostgreSQL query statistics are unavailable
                </p>
                <p className="text-xs text-muted-foreground">
                  {extensionWarning || "pg_stat_statements is not loaded in PostgreSQL shared_preload_libraries."}
                </p>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Stats */}
        {stats && (
          <div className="grid grid-cols-4 gap-3">
            <Card>
              <CardHeader className="p-3"><CardTitle className="text-xs flex items-center gap-1"><Hash className="w-3 h-3" />Unique Queries</CardTitle></CardHeader>
              <CardContent className="p-3 pt-0"><span className="text-2xl font-bold">{stats.unique_queries}</span></CardContent>
            </Card>
            <Card>
              <CardHeader className="p-3"><CardTitle className="text-xs flex items-center gap-1"><Zap className="w-3 h-3" />Total Calls</CardTitle></CardHeader>
              <CardContent className="p-3 pt-0"><span className="text-2xl font-bold">{stats.total_calls.toLocaleString()}</span></CardContent>
            </Card>
            <Card>
              <CardHeader className="p-3"><CardTitle className="text-xs flex items-center gap-1"><Clock className="w-3 h-3" />Total Time</CardTitle></CardHeader>
              <CardContent className="p-3 pt-0"><span className="text-2xl font-bold">{formatTime(stats.total_time_ms)}</span></CardContent>
            </Card>
            <Card>
              <CardHeader className="p-3"><CardTitle className="text-xs flex items-center gap-1"><BarChart3 className="w-3 h-3" />Cache Hit %</CardTitle></CardHeader>
              <CardContent className="p-3 pt-0">
                <span className={`text-2xl font-bold ${stats.cache_hit_ratio < 90 ? 'text-amber-500' : 'text-emerald-500'}`}>{stats.cache_hit_ratio}%</span>
              </CardContent>
            </Card>
          </div>
        )}

        {/* Query Table */}
        <Card>
          <CardContent className="p-0">
            {loading ? (
              <div className="flex items-center justify-center py-24"><Loader2 className="w-8 h-8 animate-spin" /></div>
            ) : queries.length === 0 ? (
              <div className="text-center py-16 text-muted-foreground">
                <Database className="w-12 h-12 mx-auto mb-3 opacity-30" />
                <p>No slow queries found above {minMs}ms threshold.</p>
                <p className="text-xs mt-1">Enable pg_stat_statements and let queries accumulate.</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[80px] text-xs">Time</TableHead>
                      <TableHead className="text-xs">Query</TableHead>
                      <TableHead className="w-[70px] text-xs text-right">Calls</TableHead>
                      <TableHead className="w-[80px] text-xs text-right">Total</TableHead>
                      <TableHead className="w-[60px] text-xs text-right">Rows</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {queries.map((q, i) => (
                      <TableRow key={q.queryid || i}>
                        <TableCell>
                          <Badge variant={q.mean_time_ms > 500 ? "destructive" : q.mean_time_ms > 100 ? "secondary" : "outline"} className="text-[10px]">
                            {formatTime(q.mean_time_ms)}
                          </Badge>
                        </TableCell>
                        <TableCell className="font-mono text-xs max-w-lg">
                          <div className="max-h-16 overflow-y-auto whitespace-pre-wrap break-all text-muted-foreground">
                            {(q.query || '').substring(0, 500)}
                          </div>
                        </TableCell>
                        <TableCell className="text-xs text-right text-muted-foreground">{q.calls.toLocaleString()}</TableCell>
                        <TableCell className="text-xs text-right text-muted-foreground">{formatTime(q.total_time_ms)}</TableCell>
                        <TableCell className="text-xs text-right text-muted-foreground">{q.rows?.toLocaleString() || '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </DashboardShell>
  );
}
