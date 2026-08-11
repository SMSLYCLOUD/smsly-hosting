"use client";

import React, { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Search, Shield, User, Settings, Server, Key, AlertTriangle, Loader2 } from "lucide-react";
import api from "@/lib/api";

interface AuditLogEntry {
  id: string;
  action: string;
  actor: string;
  target?: string;
  detail?: string;
  ip_address?: string;
  timestamp: string;
  user?: string | null;
  project?: string | null;
}

const ACTION_ICONS: Record<string, typeof Shield> = {
  login: User,
  logout: User,
  create: Settings,
  update: Settings,
  delete: AlertTriangle,
  deploy: Server,
  api_key: Key,
};

export function AuditLogsTab() {
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchLogs = async () => {
      try {
        const res = await api.get("/audit-logs/");
        const data = Array.isArray(res.data) ? res.data : res.data?.results || [];
        setLogs(data);
      } catch (err: any) {
        console.error("Failed to fetch audit logs:", err);
        setError(
          err?.response?.status === 404
            ? "Audit logs endpoint not available."
            : "Failed to load audit logs."
        );
      } finally {
        setLoading(false);
      }
    };
    fetchLogs();
  }, []);

  const filtered = logs.filter(
    (log) =>
      !search ||
      log.action?.toLowerCase().includes(search.toLowerCase()) ||
      log.actor?.toLowerCase().includes(search.toLowerCase()) ||
      log.target?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Shield className="h-5 w-5 text-yellow-500" />
            Audit Logs
          </CardTitle>
          <CardDescription>Security events and system activity.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search audit logs..."
              className="pl-9"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : error ? (
            <div className="text-center py-12 text-muted-foreground">{error}</div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">No audit logs found.</div>
          ) : (
            <div className="space-y-2 max-h-[500px] overflow-y-auto">
              {filtered.map((log) => {
                const Icon = ACTION_ICONS[log.action] || Shield;
                return (
                  <div
                    key={log.id}
                    className="flex items-start gap-3 p-3 rounded-lg border border-border/50 hover:bg-muted/30 transition-colors"
                  >
                    <Icon className="h-4 w-4 mt-0.5 text-muted-foreground shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge variant="outline" className="text-xs">
                          {log.action}
                        </Badge>
                        <span className="text-sm font-medium">{log.actor}</span>
                        {log.target && (
                          <span className="text-sm text-muted-foreground">
                            → {log.target}
                          </span>
                        )}
                      </div>
                      {log.detail && (
                        <p className="text-xs text-muted-foreground mt-1 truncate">
                          {log.detail}
                        </p>
                      )}
                    </div>
                    <div className="text-right shrink-0">
                      <div className="text-xs text-muted-foreground">
                        {new Date(log.timestamp).toLocaleString()}
                      </div>
                      {log.ip_address && (
                        <div className="text-[10px] text-muted-foreground font-mono">
                          {log.ip_address}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
